"""命令行入口:`python -m bilifocus ...`

界面还没定的时候,命令行就是最低成本的验证方式,也是脚本化的入口。
所有子命令都只依赖 `FavoritesService`,和将来的前端走同一条路径。

退出码:0 成功,1 业务失败(带可读原因),2 用法错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata
import webbrowser
from pathlib import Path

from . import __version__
from .errors import BiliFocusError
from .models import FavoritesView, Video
from .service import FavoritesService, search
from .store import StateStore
from .wbi import MIXIN_KEY_ENC_TAB, mixin_key, sign


# --------------------------------------------------------------------------
# 终端排版:中文占两格,直接 ljust 会错位
# --------------------------------------------------------------------------
def display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def truncate(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    out = ""
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if used + char_width > width - 1:
            break
        out += char
        used += char_width
    return out + "…"


def print_table(rows: list[list[str]], headers: list[str] | None = None) -> None:
    if not rows:
        return
    table = ([headers] if headers else []) + rows
    columns = len(table[0])
    widths = [max(display_width(row[index]) for row in table) for index in range(columns)]
    for position, row in enumerate(table):
        cells = [pad(cell, widths[index]) for index, cell in enumerate(row)]
        line = "  ".join(cells).rstrip()
        print(line)
        if headers and position == 0:
            print("  ".join("-" * widths[index] for index in range(columns)))


# --------------------------------------------------------------------------
# 各子命令
# --------------------------------------------------------------------------
def cmd_status(service: FavoritesService, args) -> int:
    info = service.store.describe()
    info["home_override"] = bool(os.environ.get("BILI_FOCUS_HOME"))
    info["cache_dir"] = str(service.cache.root)
    info["default_fid"] = service.default_fid
    info["sync_interval_seconds"] = service.sync_interval
    fid = service.default_fid
    if fid:
        last = service.store.get_pref(service._sync_pref(fid), 0)
        info["last_check"] = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(last))) if last else "从未"
        )
    else:
        info["last_check"] = "还没有指定收藏夹"

    if args.offline:
        info["login"] = "unknown (--offline)"
    else:
        try:
            state = service.login_state()
            info["login"] = (
                f"已登录 {state.uname or ''} (mid={state.mid})".strip()
                if state.is_login
                else "未登录"
            )
        except BiliFocusError as error:
            info["login"] = f"检查失败:{error}"

    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    for key in (
        "home",
        "state_path",
        "cache_dir",
        "schema",
        "has_sessdata",
        "login",
        "watched_count",
        "progress_count",
        "finished_count",
        "watch_seconds",
        "default_fid",
        "sync_interval_seconds",
        "last_check",
    ):
        value = info.get(key)
        if key == "sync_interval_seconds" and isinstance(value, float) and value.is_integer():
            value = int(value)
        print(f"{pad(key, 22)}{value}")
    return 0


def cmd_login(service: FavoritesService, args) -> int:
    from .service import normalize_sessdata

    if getattr(args, "qrcode", False):
        return _qr_login_cli(service)

    if getattr(args, "browser", False):
        print("正在打开一个独立的浏览器窗口(不是你日常用的那个)……")
        print("请在窗口里登录 B 站,登录完成后会自动继续。")
        state = service.login_via_browser(progress=lambda message: print(f"  {message}"))
        print(f"登录成功:{state.uname or '(未知用户)'}  mid={state.mid}")
        return 0

    value = args.sessdata
    if not value and args.stdin:
        value = sys.stdin.read()
    if not value:
        value = _prompt_secret("粘贴 SESSDATA(输入不回显,回车确认):")
    if not value:
        print("没有拿到 SESSDATA,已取消。", file=sys.stderr)
        return 1
    state = service.login(normalize_sessdata(value))
    print(f"登录成功:{state.uname or '(未知用户)'}  mid={state.mid}")
    return 0


def _qr_login_cli(service: FavoritesService) -> int:
    """终端里显示二维码,轮询到你扫码成功为止。"""
    from .qrlogin import QrLoginSession, render_terminal

    session = QrLoginSession()
    try:
        ticket = session.start()
    except BiliFocusError as error:
        print(f"生成二维码失败:{error}", file=sys.stderr)
        return 1

    print(render_terminal(session.make_qr(ticket.url)))
    print("请用 B 站手机客户端(App 右上角「扫一扫」)扫描上面的二维码…")

    last_status = None
    while True:
        try:
            result = session.poll(ticket)
        except BiliFocusError as error:
            print(f"检查状态失败:{error}")
            time.sleep(2)
            continue
        if result.status != last_status:
            print(f"  → {result.message}")
            last_status = result.status
        if result.done:
            try:
                state = service.login(result.sessdata or "")
            except BiliFocusError as error:
                print(f"扫码成功但校验失败:{error}", file=sys.stderr)
                return 1
            print(f"登录成功:{state.uname or '(未知用户)'}  mid={state.mid}")
            return 0
        if result.expired:
            print("二维码已失效,请重新运行:bilifocus login --qrcode", file=sys.stderr)
            return 1
        time.sleep(2)


def cmd_logout(service: FavoritesService, args) -> int:
    service.logout()
    print("已删除本机保存的 SESSDATA。")
    if getattr(args, "forget_browser", False):
        removed = service.forget_browser_login()
        print("已删除浏览器登录配置。" if removed else "没有需要删除的浏览器配置。")
    return 0


def cmd_folders(service: FavoritesService, args) -> int:
    mid = args.mid or service.default_mid
    if not mid:
        print("不知道该读哪个账号的收藏夹:请加 --mid <你的mid>,或先 login 一次。")
        return 2
    folders = service.folders(mid)
    if args.json:
        print(json.dumps([folder.to_dict() for folder in folders], ensure_ascii=False, indent=2))
        return 0
    if not folders:
        print("没有可见的收藏夹。私密收藏夹需要先用登录态属于该账号的 SESSDATA 登录。")
        return 0
    rows = [[str(folder.fid), folder.title, str(folder.media_count), "公开" if folder.is_public else "私密"]
            for folder in folders]
    print_table(rows, ["fid", "标题", "条数", "可见性"])
    return 0


def cmd_list(service: FavoritesService, args) -> int:
    fid = _resolve_fid(service, args.fid)
    view = service.videos(fid, force=args.refresh)
    videos = search(view.videos, args.search)
    if args.limit and not args.all:
        videos = videos[: args.limit]

    if args.json:
        print(json.dumps(_view_payload(service, view, videos), ensure_ascii=False, indent=2))
        return 0

    age = _age_text(view.fetched_at)
    source = f"缓存,{age}前" if view.from_cache else "刚拉取"
    stats = service.stats(view)
    print(
        f"收藏夹 {fid}:{len(view.videos)} 条,已看 {stats['watched']},"
        f"未看 {stats['unwatched']},失效 {stats['invalid']}(数据:{source})"
    )
    if args.search:
        print(f"筛选「{args.search}」命中 {len(videos)} 条")
    print()
    rows = []
    for position, video in enumerate(videos, start=1):
        rows.append(
            [
                str(position),
                "✓" if service.is_watched(video) else "",
                truncate(video.title, 44),
                truncate(video.upper, 12),
                video.duration_text,
                video.bvid or "-",
            ]
        )
    print_table(rows, ["#", "看", "标题", "UP主", "时长", "BV"])
    if not args.all and len(videos) < len(view.videos):
        print(f"\n(只显示前 {len(videos)} 条,用 --all 看全部)")
    return 0


def cmd_watch(service: FavoritesService, args) -> int:
    changed = service.mark_watched(args.key)
    print(("已标记看过:" if changed else "本来就是已看:") + args.key)
    return 0


def cmd_unwatch(service: FavoritesService, args) -> int:
    changed = service.unmark_watched(args.key)
    print(("已取消标记:" if changed else "本来就没标记:") + args.key)
    return 0


def cmd_watched(service: FavoritesService, args) -> int:
    fid = _resolve_fid(service, args.fid)
    view = service.videos(fid)
    watched = [video for video in view.videos if service.is_watched(video)]
    if args.json:
        print(json.dumps([video.to_dict() for video in watched], ensure_ascii=False, indent=2))
        return 0
    if not watched:
        print("这个收藏夹里还没有标记过任何视频。")
        return 0
    rows = [
        [str(position), truncate(video.title, 48), video.duration_text, video.bvid]
        for position, video in enumerate(watched, start=1)
    ]
    print_table(rows, ["#", "标题", "时长", "BV"])
    return 0


def cmd_random(service: FavoritesService, args) -> int:
    fid = _resolve_fid(service, args.fid)
    view = service.videos(fid)
    video = service.random_unwatched(view)
    if video is None:
        print("这个收藏夹是空的。", file=sys.stderr)
        return 1
    print(f"{video.title}\n  {video.upper}  ·  {video.duration_text}\n  {video.url or '(没有可用链接)'}")
    if args.open and video.url:
        webbrowser.open(video.url)
        service.mark_watched(video)
        print("(已打开浏览器,并标记为看过)")
    return 0


def cmd_export(service: FavoritesService, args) -> int:
    fid = _resolve_fid(service, args.fid)
    view = service.videos(fid, force=args.refresh)
    target = Path(args.out)
    if target.suffix.lower() == ".json":
        path = service.export_json(view, target)
    else:
        path = service.export_csv(view, target)
    print(f"已导出 {len(view.videos)} 条到 {path}")
    return 0


def cmd_doctor(service: FavoritesService, args) -> int:
    failures: list[str] = []

    def report(label: str, ok: bool, detail: str = "") -> None:
        print(f"[{'ok' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    print("== 底层自检 ==")
    report("数据目录可用", _check_home(service), str(service.store.home))
    report("状态文件可解析", True, f"schema={service.store._data.get('schema')}")

    img_key, sub_key = "7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45"
    expected_mixin = "ea1db124af3c7062474693fa704f4ff8"
    report("WBI 混流密钥(官方向量)", mixin_key(img_key + sub_key) == expected_mixin)
    signed = sign({"foo": "114", "bar": "514", "zab": 1919810}, img_key, sub_key, timestamp=1702204169)
    expected_rid = "8f6f2b5b3d485fe1886cec6a0be8c5d4"
    report("WBI 签名(官方 Rust 测试向量)", signed.get("w_rid") == expected_rid, str(signed.get("w_rid")))
    report("置换表长度 64", len(MIXIN_KEY_ENC_TAB) == 64)

    if args.offline:
        print("\n(跳过联网检查)")
    else:
        print("\n== 联网检查 ==")
        try:
            keys = service.client.wbi_keys()
            report("nav 可访问且取到密钥", len(keys[0]) + len(keys[1]) >= 64, f"{keys[0][:8]}…")
        except BiliFocusError as error:
            report("nav 可访问", False, str(error))

        if service.is_logged_in:
            try:
                state = service.login_state()
                report("登录态有效", state.is_login, state.uname or "")
            except BiliFocusError as error:
                report("登录态有效", False, str(error))
            fid = service.default_fid
            if fid:
                try:
                    page = service.page(fid, 1)
                    report("收藏夹可读", True, f"第 1 页 {len(page.videos)} 条")
                except BiliFocusError as error:
                    report("收藏夹可读", False, str(error))
        else:
            print("[--] 未登录,跳过登录态与收藏夹检查")

    print()
    if failures:
        print(f"有 {len(failures)} 项未通过:{', '.join(failures)}")
        return 1
    print("全部通过。")
    return 0


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _resolve_fid(service: FavoritesService, explicit: int | None) -> int:
    if explicit:
        service.set_default_fid(explicit)
        return int(explicit)
    fid = service.default_fid
    if fid:
        return fid
    print(
        "还没有指定收藏夹。用 --fid 指定一次,之后会记住(也可以先跑 folders 看有哪些)。",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _view_payload(service: FavoritesService, view: FavoritesView, videos: list[Video]) -> dict:
    return {
        "fid": view.fid,
        "total": view.total,
        "from_cache": view.from_cache,
        "fetched_at": view.fetched_at,
        "count": len(videos),
        "stats": service.stats(view),
        "videos": [
            {**video.to_dict(), "watched": service.is_watched(video), "url": video.url}
            for video in videos
        ],
    }


def _age_text(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 86400} 天"


def _check_home(service: FavoritesService) -> bool:
    try:
        probe = service.store.home / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _prompt_secret(prompt: str) -> str:
    try:
        import getpass

        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:  # noqa: BLE001 - 非交互环境没有 tty 时退回普通输入
        try:
            return input(prompt)
        except EOFError:
            return ""


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bilifocus",
        description="BiliFocus 底层:只看收藏夹的 B 站工具",
        epilog="界面版入口:python -m bilifocus.ui_tk",
    )
    parser.add_argument("--version", action="version", version=f"bilifocus {__version__}")
    parser.add_argument("--home", help="覆盖数据目录(默认 %%APPDATA%%\\bili-focus)")
    subparsers = parser.add_subparsers(dest="command")

    status = subparsers.add_parser("status", help="显示配置、登录态与数据目录")
    status.add_argument("--json", action="store_true")
    status.add_argument("--offline", action="store_true", help="不联网")
    status.set_defaults(func=cmd_status)

    login = subparsers.add_parser("login", help="保存并验证 SESSDATA")
    login.add_argument("--sessdata", help="直接给出值(注意会留在 shell 历史里)")
    login.add_argument("--stdin", action="store_true", help="从标准输入读取")
    login.add_argument(
        "--browser",
        action="store_true",
        help="打开独立浏览器登录,自动获取 SESSDATA(不用手动复制)",
    )
    login.add_argument(
        "--qrcode",
        action="store_true",
        help="在终端显示二维码,用 B 站手机客户端扫码登录",
    )
    login.set_defaults(func=cmd_login)

    logout = subparsers.add_parser("logout", help="删除本机保存的 SESSDATA")
    logout.add_argument("--forget-browser", action="store_true", help="同时删除浏览器登录配置")
    logout.set_defaults(func=cmd_logout)

    folders = subparsers.add_parser("folders", help="列出账号下的收藏夹")
    folders.add_argument("--mid", type=int, default=0, help="收藏夹所属用户 mid(默认用本机登录时记住的)")
    folders.add_argument("--json", action="store_true")
    folders.set_defaults(func=cmd_folders)

    list_cmd = subparsers.add_parser("list", help="列出收藏夹内容")
    list_cmd.add_argument("--fid", type=int)
    list_cmd.add_argument("--refresh", action="store_true", help="忽略缓存,强制联网")
    list_cmd.add_argument("--search", default="", help="按标题或 UP 主筛选")
    list_cmd.add_argument("--limit", type=int, default=30)
    list_cmd.add_argument("--all", action="store_true", help="显示全部,不截断")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    watch = subparsers.add_parser("watch", help="把某条标记为已看")
    watch.add_argument("key", help="BV 号或 av+aid")
    watch.set_defaults(func=cmd_watch)

    unwatch = subparsers.add_parser("unwatch", help="取消已看标记")
    unwatch.add_argument("key")
    unwatch.set_defaults(func=cmd_unwatch)

    watched = subparsers.add_parser("watched", help="列出已看过的")
    watched.add_argument("--fid", type=int)
    watched.add_argument("--json", action="store_true")
    watched.set_defaults(func=cmd_watched)

    random_cmd = subparsers.add_parser("random", help="随机挑一条没看过的")
    random_cmd.add_argument("--fid", type=int)
    random_cmd.add_argument("--open", action="store_true", help="同时用浏览器打开")
    random_cmd.set_defaults(func=cmd_random)

    export = subparsers.add_parser("export", help="导出为 CSV 或 JSON")
    export.add_argument("--fid", type=int)
    export.add_argument("--out", required=True, help="输出路径,后缀决定格式(.csv/.json)")
    export.add_argument("--refresh", action="store_true")
    export.set_defaults(func=cmd_export)

    doctor = subparsers.add_parser("doctor", help="自检:签名、目录、登录态、接口连通性")
    doctor.add_argument("--offline", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    try:
        service = FavoritesService(store=StateStore(args.home))
        return int(args.func(service, args) or 0)
    except BiliFocusError as error:
        print(f"\n出错了:{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
