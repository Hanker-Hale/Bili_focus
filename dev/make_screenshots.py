#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用示例数据重新生成 README 里的截图(脱敏版,不含真实账号/收藏夹信息)。

产物写到 docs/screenshots/:

    ui.png        主界面(全屏、自动分类)
    grouped.png   分类收起状态(展示分类标题与箭头)
    schedule.png  日程面板
    farewell.png  退出小结
    qr-login.png  扫码登录

实现说明:只用标准库。截图靠 Windows GDI(BitBlt + StretchBlt)抓窗口,
PNG 用 zlib 手写(不依赖 Pillow),这样在装了 tkinter 的机器上都能重跑。

    python dev/make_screenshots.py
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import tempfile
import time
import tkinter as tk
import zlib
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 先把数据目录指到临时沙盒,避免碰到真实配置
os.environ["FAVFOCUS_HOME"] = tempfile.mkdtemp(prefix="favfocus-shots-")

import demo_data  # noqa: E402
from favfocus import theme, ui_tk  # noqa: E402
from favfocus.models import FavoritesView  # noqa: E402
from favfocus.ui_farewell import FarewellDialog, FarewellSummary  # noqa: E402
from favfocus.qrlogin import QrLoginSession  # noqa: E402
from favfocus.ui_schedule import ScheduleDialog  # noqa: E402

OUT_DIR = ROOT / "docs" / "screenshots"
TARGET_WIDTH = 1600
SLEEP = 0.35

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()


# ── GDI 截图 + 手写 PNG ─────────────────────────────────────────────────
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _window_handle(window: tk.Misc) -> int:
    window.update_idletasks()
    inner = window.winfo_id()
    parent = user32.GetParent(inner)
    return int(parent) or int(inner)


def _write_png(path: Path, width: int, height: int, rgb_rows: list[bytes]) -> None:
    raw = b"".join(b"\x00" + row for row in rgb_rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def grab(window: tk.Misc, target_width: int = TARGET_WIDTH) -> bytes:
    """抓窗口内容,等比缩到 target_width 宽,返回 PNG 字节。

    用 PrintWindow 渲染窗口本身,而不是从屏幕 BitBlt——这样别的窗口、
    输入法候选框不会混进截图里。
    """
    hwnd = _window_handle(window)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)

    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    width = client.right - client.left
    height = client.bottom - client.top
    if width <= 0 or height <= 0:
        raise RuntimeError(f"窗口尺寸异常:{width}x{height}")

    scale = min(1.0, target_width / width)
    out_w = max(1, int(width * scale))
    out_h = max(1, int(height * scale))

    screen_dc = user32.GetDC(0)
    src_dc = gdi32.CreateCompatibleDC(screen_dc)
    src_info = BITMAPINFO()
    src_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    src_info.bmiHeader.biWidth = width
    src_info.bmiHeader.biHeight = -height
    src_info.bmiHeader.biPlanes = 1
    src_info.bmiHeader.biBitCount = 32
    src_info.bmiHeader.biCompression = 0
    src_bits = ctypes.c_void_p()
    src_bitmap = gdi32.CreateDIBSection(
        src_dc, ctypes.byref(src_info), 0, ctypes.byref(src_bits), None, 0
    )
    old_src = gdi32.SelectObject(src_dc, src_bitmap)

    printed = user32.PrintWindow(hwnd, src_dc, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
    if not printed:
        origin = POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        gdi32.BitBlt(src_dc, 0, 0, width, height, screen_dc, origin.x, origin.y, 0x00CC0020)

    dst_dc = gdi32.CreateCompatibleDC(screen_dc)
    dst_info = BITMAPINFO()
    dst_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    dst_info.bmiHeader.biWidth = out_w
    dst_info.bmiHeader.biHeight = -out_h
    dst_info.bmiHeader.biPlanes = 1
    dst_info.bmiHeader.biBitCount = 32
    dst_info.bmiHeader.biCompression = 0
    dst_bits = ctypes.c_void_p()
    dst_bitmap = gdi32.CreateDIBSection(
        dst_dc, ctypes.byref(dst_info), 0, ctypes.byref(dst_bits), None, 0
    )
    old_dst = gdi32.SelectObject(dst_dc, dst_bitmap)
    gdi32.SetStretchBltMode(dst_dc, 4)  # HALFTONE
    gdi32.StretchBlt(dst_dc, 0, 0, out_w, out_h, src_dc, 0, 0, width, height, 0x00CC0020)

    buffer = (ctypes.c_ubyte * (out_w * out_h * 4)).from_address(dst_bits.value)
    data = bytes(buffer)
    rows = []
    for y in range(out_h):
        start_index = y * out_w * 4
        line = data[start_index : start_index + out_w * 4]
        rgb = bytearray(out_w * 3)
        rgb[0::3] = line[2::4]
        rgb[1::3] = line[1::4]
        rgb[2::3] = line[0::4]
        rows.append(bytes(rgb))

    gdi32.SelectObject(dst_dc, old_dst)
    gdi32.DeleteObject(dst_bitmap)
    gdi32.DeleteDC(dst_dc)
    gdi32.SelectObject(src_dc, old_src)
    gdi32.DeleteObject(src_bitmap)
    gdi32.DeleteDC(src_dc)
    user32.ReleaseDC(0, screen_dc)

    tmp = tempfile.mktemp(suffix=".png")
    _write_png(Path(tmp), out_w, out_h, rows)
    result = Path(tmp).read_bytes()
    Path(tmp).unlink()
    return result


def save(window: tk.Misc, name: str) -> None:
    payload = grab(window)
    target = OUT_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    print(f"  写出 {name} ({len(payload)//1024} KB)")


def pump(window: tk.Misc, seconds: float = 1.2, rounds_each: int = 20) -> None:
    end = time.time() + seconds
    while time.time() < end:
        window.update_idletasks()
        window.update()
        time.sleep(0.01)


# ── 各个截图场景 ────────────────────────────────────────────────────────
def shot_splash() -> None:
    """启动动画:灯泡 + 光线。"""
    service, videos, folder = demo_data.build_demo()
    root = tk.Tk()
    app = ui_tk.FocusApp(
        root,
        fid_override=demo_data.DEMO_FID,
        mid=0,
        bootstrap=False,
        service=service,
        fullscreen=True,
    )
    app.folders = [folder]
    app.folder_box.configure(values=[f"{folder.title} ({folder.media_count})"])
    app.folder_box.current(0)
    app.splash.set_status("正在读取收藏夹…")
    pump(root, 1.3)  # 让光线动画跑起来
    save(root, "splash.png")
    root.destroy()


def shot_main(collapsed: set[str], name: str) -> None:
    service, videos, folder = demo_data.build_demo()
    root = tk.Tk()
    app = ui_tk.FocusApp(
        root,
        fid_override=demo_data.DEMO_FID,
        mid=0,
        bootstrap=False,
        service=service,
        fullscreen=True,
    )
    app.folders = [folder]
    app.folder_box.configure(values=[f"{folder.title} ({folder.media_count})"])
    app.folder_box.current(0)
    service.store.set_pref("collapsed_groups", sorted(collapsed))

    # 等全屏生效、网格的列数按最终宽度算出来,再塞数据
    pump(root, 1.4)

    # 封面缓存 key 依赖网格的封面宽高,所以这里按实际尺寸补种
    grid = app.grid_view
    demo_data.seed_covers(service.store.home, videos, grid._cover_w, grid._cover_h)
    app.videos = videos
    app._render()
    app._hide_splash()
    # 底部状态栏(和 refresh() 成功时同一句文案)
    view = FavoritesView(
        fid=demo_data.DEMO_FID,
        videos=tuple(videos),
        total=len(videos),
        fetched_at=time.time(),
        from_cache=True,
        checked_at=time.time(),
        new_count=0,
    )
    stats = service.stats(view)
    digest = service.digest(videos)
    app._set_status(
        f"共 {stats['count']} 条,已看完 {digest['finished']},"
        f"未看完 {digest['remaining']}(缓存)。双击播放。"
    )
    pump(root, 1.8)
    if getattr(app.splash, "visible", False):
        app._hide_splash(force=True) if "force" in getattr(app._hide_splash, "__code__", type("", (), {"co_varnames": ()})).co_varnames else app._hide_splash()
        pump(root, 0.6)
    save(root, name)
    root.destroy()


def visible_root() -> tk.Tk:
    """对话框需要一个可见的父窗口才能被正常映射和截图。"""
    root = tk.Tk()
    root.title("FavFocus")
    root.configure(bg=theme.BG)
    root.geometry("760x560+60+60")
    root.update_idletasks()
    root.update()
    return root


def shot_update_prompt() -> None:
    """发现新版本横幅(版本号用示例值)。"""
    from favfocus import update as update_mod

    service, videos, folder = demo_data.build_demo()
    service.mark_watched(videos[0].key)
    root = tk.Tk()
    app = ui_tk.FocusApp(
        root,
        fid_override=demo_data.DEMO_FID,
        mid=0,
        bootstrap=False,
        service=service,
        fullscreen=True,
    )
    app.folders = [folder]
    app.folder_box.configure(values=[f"{folder.title} ({folder.media_count})"])
    app.folder_box.current(0)
    pump(root, 1.4)
    grid = app.grid_view
    demo_data.seed_covers(service.store.home, videos, grid._cover_w, grid._cover_h)
    app.videos = videos
    app._render()
    app._hide_splash()
    pump(root, 1.4)
    info = update_mod.ReleaseInfo(
        version="2.5.0",
        tag="v2.5.0",
        installer_url="https://example.invalid/setup.exe",
        portable_url="https://example.invalid/app.exe",
        page_url="https://example.invalid/releases",
        notes="- 更新提示改成全屏,可以直接看到更新内容\n- 修复了新版本提醒不及时的问题\n- 启动动画换成灯泡与光线\n- 一些细节改进",
    )
    app._update_checked = True
    app._show_update_prompt(info)
    pump(root, 0.8)
    save(root, "update.png")
    root.destroy()


def shot_schedule() -> None:
    service, videos, _folder = demo_data.build_demo()
    service.mark_watched(videos[0].key)
    # 日程面板的封面是另一种尺寸,单独种一份缓存(否则截图里会显示"封面加载中")
    from favfocus.ui_schedule import cover_size

    cover_w, cover_h = cover_size()
    demo_data.seed_covers(service.store.home, videos, cover_w, cover_h)
    root = visible_root()
    dialog = ScheduleDialog(root, service, videos=videos, on_play=lambda item: None, on_change=None)
    pump(root, 1.6)
    save(dialog, "schedule.png")
    dialog.destroy()
    root.destroy()


def shot_farewell() -> None:
    service, videos, _folder = demo_data.build_demo()
    root = visible_root()
    digest = service.digest(videos)
    summary = FarewellSummary(
        username=demo_data.DEMO_USERNAME,
        session_watch_seconds=42 * 60 + 15,
        session_plays=3,
        total_watch_seconds=service.total_watch_seconds(),
        digest=digest,
        progress_count=6,
    )
    dialog = FarewellDialog(root, summary, on_close=lambda: None)
    pump(root, 1.2)
    save(dialog, "farewell.png")
    dialog.destroy()
    root.destroy()


class _FakeTicket:
    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key


class _FakePollResult:
    status = 86101  # 等待扫码
    message = "等待扫码"
    sessdata = None


class _FakeQrSession:
    """假会话:不联网,只画一张二维码(矩阵由内置编码器本地生成)。"""

    make_qr = staticmethod(QrLoginSession.make_qr)

    def start(self):
        return _FakeTicket("https://example.invalid/qrcode?key=demo", "demo")

    def poll(self, ticket):
        return _FakePollResult()

    def close(self):
        pass


def shot_reminder() -> None:
    """到点提醒条:主界面顶部那一条。"""
    service, videos, folder = demo_data.build_demo()
    # 加一条"已经到点"的计划:00:00 一定过了
    service.add_schedule("00:00", video=videos[0])
    root = tk.Tk()
    app = ui_tk.FocusApp(
        root,
        fid_override=demo_data.DEMO_FID,
        mid=0,
        bootstrap=False,
        service=service,
        fullscreen=True,
    )
    app.folders = [folder]
    app.folder_box.configure(values=[f"{folder.title} ({folder.media_count})"])
    app.folder_box.current(0)
    pump(root, 1.4)
    dialog = app.grid_view
    demo_data.seed_covers(service.store.home, videos, dialog._cover_w, dialog._cover_h)
    app.videos = videos
    app._render()
    app._hide_splash()
    pump(root, 1.6)
    app._check_schedule_reminder(force=True)
    pump(root, 0.8)
    save(root, "reminder.png")
    root.destroy()


def shot_qr() -> None:
    service, _videos, _folder = demo_data.build_demo()
    root = visible_root()
    original = ui_tk.QrLoginSession
    ui_tk.QrLoginSession = lambda: _FakeQrSession()
    try:
        dialog = ui_tk.QrLoginDialog(root, service)
        pump(root, 1.2)
        save(dialog, "qr-login.png")
        dialog.destroy()
    finally:
        ui_tk.QrLoginSession = original
        root.destroy()


def main() -> int:
    theme.enable_dpi_awareness()
    print("生成脱敏截图 →", OUT_DIR)
    shot_splash()
    shot_update_prompt()
    shot_main(collapsed=set(), name="ui.png")
    shot_main(collapsed={"code", "ai"}, name="grouped.png")
    shot_schedule()
    shot_reminder()
    shot_farewell()
    shot_qr()
    total = sum(path.stat().st_size for path in OUT_DIR.glob("*.png"))
    print(f"完成:{len(list(OUT_DIR.glob('*.png')))} 张,共 {total//1024} KB")
    # Tk 销毁时残留的 after 回调会在解释器退出阶段报错,这里直接结束进程
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
