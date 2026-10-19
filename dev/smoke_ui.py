#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面冒烟测试:构建真实 Tk 窗口、跑几圈事件循环、验证卡片网格与交互。

不联网、不弹登录框、不碰真实配置目录。
"""

import os
import sys
import tempfile
import time
import tkinter as tk

sandbox = tempfile.mkdtemp(prefix="bili-focus-smoke-")
os.environ["BILI_FOCUS_HOME"] = sandbox
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bilifocus.ui_tk as ui  # noqa: E402
from bilifocus.models import FavoritesView, Video  # noqa: E402
from bilifocus.service import FavoritesService  # noqa: E402
from bilifocus.store import StateStore  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


def pump(root, rounds=10, delay=0.005):
    """跑几圈事件循环。留一点真实延时,否则 40/50ms 的 after() 定时器不会触发。"""
    for _ in range(rounds):
        root.update_idletasks()
        root.update()
        if delay:
            time.sleep(delay)


service = FavoritesService(store=StateStore(home=sandbox))
root = tk.Tk()
# bootstrap=False:跳过启动流程,避免无头环境卡在模态登录框的 wait_window
app = ui.FocusApp(root, fid_override=750450031, mid=479257031, bootstrap=False, service=service, fullscreen=False)
pump(root)


def card_state(key):
    return app.grid_view.debug_card(key) if hasattr(app.grid_view, "debug_card") else {}


def card_has_tag(grid, key, tag):
    """卡片是直接画在画布上的,所以用 tag 判断有没有画某个元素。"""
    for item in grid.canvas.find_withtag(f"card:{key}"):
        if tag in grid.canvas.gettags(item):
            return True
    return False

check("window built", bool(root.winfo_exists()))
check("folder combobox exists", isinstance(app.folder_box, ui.ttk.Combobox))
check("card grid exists", isinstance(app.grid_view, ui.CardGrid))
check("search entry exists", isinstance(app.search_entry, tk.Entry))
check("watched toggle present", isinstance(app.hide_var, tk.BooleanVar))
check("fid override applied", service.default_fid == 750450031, str(service.default_fid))
check("status label has text", bool(app.status.cget("text")))
check("initial placeholder shown", app.grid_view.count == 0)

# ── 启动页:默认可见、有图形、在动、能改文案 ────────────────────────
check("splash shown at startup", app.splash.visible)
check("splash draws something", len(app.splash.canvas.find_all()) >= 8, str(len(app.splash.canvas.find_all())))
check("splash animation scheduled", app.splash._job is not None)
app.splash.set_status("正在读取收藏夹…")
check(
    "splash status updates",
    app.splash.canvas.itemcget(app.splash._status_id, "text") == "正在读取收藏夹…",
    app.splash.canvas.itemcget(app.splash._status_id, "text"),
)
check("splash keeps a minimum display time", app.splash.remaining_ms() >= 0)
# 回归:窗口尺寸变化时启动页不能重复叠画(启动时窗口从小变全屏会触发)
def splash_counts():
    canvas = app.splash.canvas
    return len(canvas.find_withtag("logo")), len(canvas.find_withtag("splash-dots"))


before_counts = splash_counts()
check("splash draws one logo and three dots", before_counts == (9, 3), str(before_counts))
app.splash._size = (0, 0)          # 强制下一次 Configure 重新布局
root.geometry("900x620")
pump(root, rounds=10)
after_counts = splash_counts()
check("relayout does not duplicate splash graphics", after_counts == before_counts, str(after_counts))

# 登录框必须居中在父窗口上,而不是按屏幕居中(否则会偏出主窗口)
dialog = ui.LoginDialog(root, "")
pump(root)
dx, dy = dialog.winfo_rootx(), dialog.winfo_rooty()
dw, dh = dialog.winfo_width(), dialog.winfo_height()
rx, ry = root.winfo_rootx(), root.winfo_rooty()
rw, rh = root.winfo_width(), root.winfo_height()
check(
    "login dialog horizontally inside parent",
    rx <= dx and dx + dw <= rx + rw,
    f"dialog {dx}..{dx + dw} vs root {rx}..{rx + rw}",
)
check(
    "login dialog vertically inside parent",
    ry <= dy and dy + dh <= ry + rh,
    f"dialog {dy}..{dy + dh} vs root {ry}..{ry + rh}",
)
# "用浏览器登录"按钮返回的是标记值,不能被当成用户填的 SESSDATA
dialog._use_browser()
check("browser login button returns sentinel", dialog.result == ui.BROWSER_LOGIN, str(dialog.result))
dialog.grab_release()
pump(root)

sample = [
    Video(aid=1, bvid="BV1xx411c7mD", title="第一个视频", upper="UP甲", duration=125, fav_time=1700000000),
    Video(aid=2, bvid="BV1yy411c7mE", title="Serenity 环境搭建", upper="UP乙", duration=3730, fav_time=1700000100),
]
app.videos = sample
app._render()
pump(root, rounds=20)

check("two cards rendered", app.grid_view.count == 2, str(app.grid_view.count))
check("visible list matches", len(app.visible) == 2)
check("first card auto-selected", app.grid_view.selected() is not None)

# 有内容之后启动页必须退场(最多等它跑完最短展示时长)
deadline = time.time() + 2.0
while app.splash.visible and time.time() < deadline:
    pump(root, rounds=5)
check("splash hides once content is ready", not app.splash.visible)

first = card_state("BV1xx411c7mD")
check("card shows title", first.get("title") == "第一个视频", str(first.get("title")))
check("card shows uploader", first.get("meta", "").startswith("UP甲"), str(first.get("meta")))
check(
    "card draws a cover area",
    card_has_tag(app.grid_view, "BV1xx411c7mD", "cover")
    or card_has_tag(app.grid_view, "BV1xx411c7mD", "cover-placeholder"),
)

# ── 自动分类:开关 + 分组标题 ─────────────────────────────────────────
def header_labels(grid):
    labels = []
    for item in grid.canvas.find_withtag("group-header"):
        try:
            text = grid.canvas.itemcget(item, "text")
        except tk.TclError:
            continue
        if text:
            labels.append(str(text))
    return labels


labeled = [
    Video(aid=11, bvid="BVG1", title="Python 编程入门:环境变量", upper="u", duration=600, fav_time=0),
    Video(aid=12, bvid="BVG2", title="高等数学 极限与导数", upper="u", duration=600, fav_time=0),
    Video(aid=13, bvid="BVG3", title="Python 编程:函数与调试", upper="u", duration=600, fav_time=0),
    Video(aid=14, bvid="BVG4", title="线性代数 矩阵的秩", upper="u", duration=600, fav_time=0),
]
app.videos = labeled
app.group_var.set(True)
app._render()
pump(root, rounds=20)
labels = header_labels(app.grid_view)
check("grouping draws section headers", "编程开发" in labels and "数学" in labels, str(labels))
def header_centers(grid, label):
    """返回 (名字文字中线, 箭头中线),用来检查两者是否对齐。"""
    for index, row in enumerate(grid._rows):
        if row["kind"] != "header" or row["label"] != label:
            continue
        tag = f"header:{index}"
        label_item = chevron_item = None
        for item in grid.canvas.find_withtag(tag):
            kind = grid.canvas.type(item)
            if kind == "text" and str(grid.canvas.itemcget(item, "text")) == label:
                label_item = item
            elif kind == "polygon":
                chevron_item = item
        if label_item and chevron_item:
            lb = grid.canvas.bbox(label_item)
            cb = grid.canvas.bbox(chevron_item)
            return (lb[1] + lb[3]) / 2, (cb[1] + cb[3]) / 2
    return None


centers = header_centers(app.grid_view, "编程开发")
check("group header has name and chevron", centers is not None)
if centers:
    check(
        "chevron is vertically aligned with the name",
        abs(centers[0] - centers[1]) <= 2,
        f"name={centers[0]:.1f} chevron={centers[1]:.1f}",
    )
check(
    "same category stays adjacent",
    [video.key for video in app.grid_view._order[:2]] == ["BVG1", "BVG3"],
    str([video.key for video in app.grid_view._order[:4]]),
)

app.group_var.set(False)
app._on_toggle_group()
pump(root, rounds=20)
check("disabling grouping removes headers", header_labels(app.grid_view) == [], str(header_labels(app.grid_view)))
check("grouping pref persisted", service.store.get_pref("auto_group") is False)

app.group_var.set(True)
app._on_toggle_group()
pump(root, rounds=20)
check("re-enabling grouping brings headers back", "编程开发" in header_labels(app.grid_view))
app.videos = labeled
app._render()
pump(root, rounds=20)

# ── 分类收起:点标题旁的箭头,只留类别名 ─────────────────────────────
def chevron_point(grid, group_id):
    for row in grid._rows:
        if row["kind"] == "header" and row.get("group_id") == group_id:
            cx = (row["chevron"][0] + row["chevron"][2]) // 2
            cy = (row["chevron"][1] + row["chevron"][3]) // 2
            return cx, cy - int(grid.canvas.canvasy(0))
    return None


group_header_texts = []
for item in app.grid_view.canvas.find_withtag("group-header"):
    try:
        group_header_texts.append(str(app.grid_view.canvas.itemcget(item, "text")))
    except tk.TclError:
        pass
check("group header shows count", any("3 条" in t or "条" in t for t in group_header_texts), str(group_header_texts))

point = chevron_point(app.grid_view, "code")
check("code group has a chevron", point is not None)
if point is not None:
    app.grid_view._on_click(type("E", (), {"x": point[0], "y": point[1]})())
    pump(root, rounds=20)
check("collapsing keeps the group header", "编程开发" in header_labels(app.grid_view))
check("collapsed group hides its cards", "BVG1" not in app.grid_view._drawn and "BVG3" not in app.grid_view._drawn)
check("other groups stay expanded", "BVG2" in app.grid_view._drawn)
check(
    "collapsed header says 已收起",
    any("已收起" in t for t in header_labels(app.grid_view)) or any(
        "已收起" in str(app.grid_view.canvas.itemcget(item, "text"))
        for item in app.grid_view.canvas.find_withtag("group-header")
        if app.grid_view.canvas.type(item) == "text"
    ),
)
saved = service.store.get_pref("collapsed_groups", [])
check("collapsed state persisted", list(saved or []) == ["code"], str(saved))

point = chevron_point(app.grid_view, "code")
if point is not None:
    app.grid_view._on_click(type("E", (), {"x": point[0], "y": point[1]})())
    pump(root, rounds=20)
check("expanding brings the cards back", "BVG1" in app.grid_view._drawn)
check("expanded state persisted", list(service.store.get_pref("collapsed_groups", []) or []) == [])

app.videos = sample
app._render()
pump(root, rounds=20)

# ── 虚拟化:只画可见范围的卡片;滚轮平滑滚动;点击按坐标命中 ───────────
class _NoCover:
    def get_cached(self, url):
        return None

    def fetch(self, url):
        return None


many = [
    Video(aid=200 + index, bvid=f"BVM{index}", title=f"视频 {index}", upper="u", duration=60, fav_time=0)
    for index in range(30)
]
virtual_grid = ui.CardGrid(root, _NoCover())
virtual_grid.pack(fill="both", expand=True)
virtual_grid.set_videos(many, set(), {})
pump(root, rounds=20)
check(
    "virtualization draws fewer cards than the list",
    len(virtual_grid._drawn) < len(many),
    f"drawn={len(virtual_grid._drawn)} of {len(many)}",
)
check("virtualization draws the visible rows", len(virtual_grid._drawn) > 0)

# 点击坐标 → 命中正确的视频
first_x, first_y = virtual_grid._position(0)
hit = virtual_grid._index_at(first_x + 5, first_y + 5)
check("click maps to the first card", hit == 0, str(hit))
virtual_grid._on_click(type("E", (), {"x": first_x + 5, "y": first_y + 5})())
pump(root)
check("click selects that video", virtual_grid.selected_key == many[0].key, str(virtual_grid.selected_key))

# 滚轮:一格像素级滚动 + 缓动,滚完内容还在
before_y = virtual_grid.canvas.canvasy(0)
virtual_grid._on_wheel(type("E", (), {"delta": -120})())
for _ in range(200):
    pump(root, rounds=1, delay=0.002)
    if virtual_grid._scroll_job is None:
        break
after_y = virtual_grid.canvas.canvasy(0)
check("wheel scrolls a moderate distance", 0 < after_y - before_y < 600, f"moved {after_y - before_y:.0f}px")
check("content still drawn after scrolling", len(virtual_grid._drawn) > 0)

# 键盘跳到很靠后的视频:会自动滚到那里并画出来
virtual_grid.select_key(many[-1].key)
pump(root, rounds=10)
check("reveal scrolls to the far card", virtual_grid.canvas.canvasy(0) > 0)
check("far card is drawn after reveal", many[-1].key in virtual_grid._drawn)

# 悬停与移出:不该抛异常,移出后高亮清掉
card_x, card_y = virtual_grid._pos.get(many[0].key, (10, 10))
virtual_grid._on_motion(
    type("E", (), {"x": card_x + 5, "y": card_y - int(virtual_grid.canvas.canvasy(0)) + 5})()
)
pump(root)
virtual_grid._on_motion_leave()
pump(root)
check("hover leave clears highlight", virtual_grid._hover_key is None)
virtual_grid.destroy()
pump(root)

# ── 超长标题:最多两行、以 … 收尾,而且不能压到下面那行 ─────────────────
long_video = Video(
    aid=7,
    bvid="BV7",
    title="【Kira线代】一夜练成“大秩佬”！15道夯题，矩阵的秩从入门到练吐，这是一个故意写得很长很长的标题用来测试折行",
    upper="一个名字特别长的UP主测试截断效果",
    duration=4987,
    fav_time=0,
)
app.videos = [long_video]
app._render()
pump(root, rounds=20)
long_card = card_state("BV7")
title_text = long_card.get("title", "")
lines = title_text.split("\n")
check("long title clipped to at most 2 lines", len(lines) <= 2, f"{len(lines)} lines")
check("long title ends with ellipsis", lines[-1].endswith("…"), lines[-1][-10:])
check("long title keeps its uploader line", bool(long_card.get("meta")), str(long_card.get("meta")))
check(
    "title font is bold",
    str(app.grid_view._font(9, "bold").actual().get("weight")) == "bold",
    str(app.grid_view._font(9, "bold").actual()),
)

# 恢复成之前的两条,后面的筛选/按键测试依赖它
app.videos = sample
app._render()
pump(root, rounds=20)

# 筛选:不区分大小写
app.search_var.set("serenity")
pump(root, rounds=20)
check("filter is case-insensitive", app.grid_view.count == 1, str(app.grid_view.count))
app.search_var.set("不存在的关键词")
pump(root)
check("no-match shows empty grid", app.grid_view.count == 0)
app.search_var.set("")
pump(root, rounds=20)
check("clearing filter restores cards", app.grid_view.count == 2)

# 键盘:右移一格
app.grid_view.select_key("BV1xx411c7mD", reveal=False)
app._move(1)
pump(root)
check("arrow key moves selection", app.grid_view.selected_key == "BV1yy411c7mE", str(app.grid_view.selected_key))

# 标记已看:只更新那一张卡,不整块重画
app.grid_view.select_key("BV1xx411c7mD", reveal=False)
app.toggle_watched()
pump(root)
check("watched state saved", service.is_watched("BV1xx411c7mD"))
check("grid knows it is watched", card_state("BV1xx411c7mD").get("watched") is True)
check(
    "watched state reflected after rebuild",
    card_state("BV1xx411c7mD").get("watched") is True,
    str(card_state("BV1xx411c7mD")),
)

app.hide_var.set(True)
app._on_toggle_hide()
pump(root, rounds=20)
check("hide-watched filters the grid", app.grid_view.count == 1, str(app.grid_view.count))
app.hide_var.set(False)
app._on_toggle_hide()
pump(root, rounds=20)
check("un-hide restores the grid", app.grid_view.count == 2)

# ── 手动改分类:右键命中卡片 → 归到指定分类 → 分组建模立刻变化 ───────
grid = app.grid_view
app.grid_view.select_key(sample[0].key, reveal=False)
grid._update_window(force=True)
position = grid._pos.get(sample[0].key)
check("card position known for hit test", position is not None, str(position))
hit = None
if position is not None:
    hit = grid.video_at(position[0] + 6, position[1] + 6)
check("right-click hit test finds the card", hit is not None and hit.key == sample[0].key)
app._set_topic(sample[0], "graphics")
pump(root)
forced = service.topic_overrides()
check("topic override persisted", forced.get(sample[0].key) == "graphics", str(forced))
grouped_ids = [group.id for group in __import__("bilifocus.topics", fromlist=["group"]).group(
    app.visible, forced
)]
check("grid regroups with the override", "graphics" in grouped_ids, str(grouped_ids))
app._set_topic(sample[0], None)
pump(root)
check("restoring auto topic clears the override", service.topic_overrides() == {}, str(service.topic_overrides()))

# ── 播放:自动全屏 → 记录进度 → 下次续播 ───────────────────────────────
from bilifocus.player import PlaybackResult  # noqa: E402

played_urls: list[str] = []


class FakePlayer:
    def __init__(self, result):
        self.result = result

    def play(self, url, *, sessdata=None, on_sample=None):
        played_urls.append(url)
        return self.result


def wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, rounds=3)
        if predicate():
            return True
    return False


# 先前那条已经被标成已看,这里改用另一条干净的来测试进度
target = sample[1]
app.player_factory = lambda: FakePlayer(
    PlaybackResult(position=100, duration=600, watched_seconds=98, samples=50)
)
app.grid_view.select_key(target.key, reveal=False)
app._play_video(target)
check(
    "playback records progress",
    wait_until(lambda: service.store.get_progress(target.key) is not None),
)
check("first open has no start offset", "?t=" not in played_urls[-1], played_urls[-1])
check("resume position is saved", service.resume_position(target) == 100, str(service.resume_position(target)))

app._render()
pump(root, rounds=20)
check(
    "card draws progress bar",
    card_has_tag(app.grid_view, target.key, "progress-fill"),
    str(card_state(target.key)),
)
check("card meta shows watched time", "看到" in card_state(target.key).get("meta", ""), str(card_state(target.key).get("meta")))

# 再播一次:应该带上 ?t=100 从上次位置继续
app._play_video(target)
check("resume offset passed to player", "?t=100" in played_urls[-1], played_urls[-1])
wait_until(lambda: app.playing_key is None)

# 看到结尾(≥90%)→ 标已看,进度保留并标成 finished
app.player_factory = lambda: FakePlayer(
    PlaybackResult(position=595, duration=600, watched_seconds=590, samples=300)
)
app._play_video(target)
check(
    "finishing marks watched",
    wait_until(lambda: service.is_watched(target)),
)
finished_record = service.store.get_progress(target.key)
check("finishing keeps progress record", finished_record is not None)
check(
    "finished record is flagged",
    bool(finished_record and finished_record.get("finished")),
    str(finished_record),
)
check("finished video restarts from zero", service.resume_position(target) == 0)
app._render()
pump(root, rounds=20)
check(
    "finished card draws a full progress bar",
    card_has_tag(app.grid_view, target.key, "progress-fill"),
    str(card_state(target.key)),
)
check(
    "finished card says 已看完",
    "已看完" in card_state(target.key).get("meta", ""),
    str(card_state(target.key).get("meta")),
)

# 播放器窗口已在时不该再开一个
app.playing_key = target.key
before = len(played_urls)
app._play_video(sample[0])
pump(root)
check("seconds play is refused while one is open", len(played_urls) == before)
app.playing_key = None
app.player_factory = None

check("prefs persisted", os.path.exists(os.path.join(sandbox, "state.json")))

# ── 已缓存的封面必须立刻贴上 ───────────────────────────────────────────
# 守的 bug:曾经把"URL→卡片"的映射建在缓存分支之后,导致有缓存时
# 图片永远停在「加载中…」。
import base64  # noqa: E402

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class CachedFetcher:
    def get_cached(self, url):
        return PNG_1PX

    def fetch(self, url):
        return PNG_1PX


probe_grid = ui.CardGrid(root, CachedFetcher())
probe_grid.pack()
probe_video = Video(
    aid=9,
    bvid="BV9",
    title="t",
    upper="u",
    duration=61,
    fav_time=0,
    pic="https://i0.hdslb.com/a.jpg",
)
probe_grid.set_videos([probe_video], set())
pump(root, rounds=20)
probe_state = probe_grid.debug_card("BV9")
check("cached cover applied immediately", probe_state.get("has_cover") is True, str(probe_state))
check(
    "cached cover drawn on the canvas",
    any(
        "cover" in probe_grid.canvas.gettags(item)
        for item in probe_grid.canvas.find_withtag("card:BV9")
    ),
)

# 重画之后(登录成功、刷新、筛选都会触发)封面必须还在。
# 守的 bug:图已在内存里就 `continue`,新卡片没人贴图,永远停在「加载中…」。
probe_grid.set_videos([probe_video], set())
pump(root, rounds=20)
rebuilt_state = probe_grid.debug_card("BV9")
check("cover survives grid rebuild", rebuilt_state.get("has_cover") is True, str(rebuilt_state))
check(
    "no duplicate cover images after rebuild",
    sum(
        1
        for item in probe_grid.canvas.find_withtag("card:BV9")
        if "cover" in probe_grid.canvas.gettags(item)
    )
    == 1,
)
probe_grid.destroy()

# ── 扫码登录窗口:注入假会话,不联网 ─────────────────────────────────
from bilifocus.qrlogin import QrPollResult, QrTicket  # noqa: E402


class FakeQrSession:
    def start(self):
        return QrTicket(url="https://account.bilibili.com/x?qrcode_key=k", key="k")

    def poll(self, ticket):
        return QrPollResult(status="waiting", message="请用 B 站手机客户端扫码")

    def make_qr(self, text):
        return [[(x + y) % 2 == 0 for x in range(10)] for y in range(10)]


original_session = ui.QrLoginSession
ui.QrLoginSession = lambda: FakeQrSession()
qr_dialog = None
try:
    qr_dialog = ui.QrLoginDialog(root, service)
    pump(root, rounds=30)
    check("qr dialog draws modules", len(qr_dialog.canvas.find_all()) > 0, str(len(qr_dialog.canvas.find_all())))
    check("qr dialog shows scan hint", "扫码" in qr_dialog.status.cget("text"), qr_dialog.status.cget("text"))
    check("qr dialog keeps ticket", qr_dialog.ticket is not None)
finally:
    if qr_dialog is not None:
        qr_dialog._close()
    ui.QrLoginSession = original_session
    pump(root)

# ── 自动增量:tick 必须真的调用服务,而且不能在 Tk 回调里静默崩 ──────────
# 这里守的是一个真实 bug:曾经在 tick 里用了不存在的 self.busy,
# Tk 回调的异常在 pythonw 下完全看不见,自动更新就一直没生效。
auto_calls = []


def fake_videos(fid, *, force=False, progress=None):
    auto_calls.append((fid, force))
    return FavoritesView(
        fid=fid,
        videos=(Video(aid=1, bvid="BV1", title="一", upper="u", duration=60, fav_time=0),),
        total=1,
        fetched_at=0.0,
        from_cache=True,
        checked_at=0.0,
        new_count=0,
    )


service.videos = fake_videos  # type: ignore[method-assign]
app.videos = [Video(aid=1, bvid="BV1", title="一", upper="u", duration=60, fav_time=0)]
app.busy = False
app._auto_busy = False
app._auto_sync_tick()
pump(root, rounds=20)
check("auto sync tick calls service", auto_calls == [(750450031, False)], str(auto_calls))
check("auto sync reschedules itself", app._auto_job is not None)

auto_calls.clear()
app._auto_busy = True
app._auto_sync_tick()
pump(root)
check("auto sync skips while previous run is in flight", auto_calls == [], str(auto_calls))
# ── 登录态失效:只提示,不删凭证(以前会静默清掉 SESSDATA)───────────
from bilifocus.models import LoginState  # noqa: E402

service.store.set_sessdata("demo-token")
saved_login_state = service.login_state
service.login_state = lambda: LoginState(is_login=False, uname="")
app._verify_and_load()
pump(root, rounds=40)
check(
    "expired session does not clear credentials",
    service.store.sessdata == "demo-token",
    str(service.store.sessdata),
)
check(
    "expired session tells the user to re-login",
    "换登录" in app.status.cget("text"),
    app.status.cget("text"),
)
service.login_state = saved_login_state
service.store.set_sessdata(None)

check("errors are logged instead of vanishing", app.root.report_callback_exception is not None)

# ── 全屏:进入/退出、ESC 行为 ─────────────────────────────────────────
check("starts windowed in tests", app.fullscreen is False)
app._enter_fullscreen()
pump(root, rounds=5)
check("enters fullscreen", app.fullscreen is True, str(app.fullscreen))
check(
    "fullscreen attribute set",
    bool(int(root.attributes("-fullscreen"))),
    str(root.attributes("-fullscreen")),
)
app._on_escape()
pump(root, rounds=5)
check("escape exits fullscreen", app.fullscreen is False)
check("escape keeps the app alive", bool(root.winfo_exists()))

# 回归:反复进出全屏,每次都必须真的没有边框
# (曾经出现过"退出全屏后再进入,边框残留"的问题)
for round_no in range(1, 4):
    app._toggle_fullscreen()
    pump(root, rounds=8)
    check(
        f"re-enter fullscreen removes the frame (round {round_no})",
        not app._frame_present(),
        f"frame={app._frame_present()} borderless={getattr(app, '_borderless_fallback', None)}",
    )
    app._on_escape()
    pump(root, rounds=8)
    check(
        f"leaving fullscreen restores the frame (round {round_no})",
        app._frame_present() or sys.platform != "win32",
    )

# ── 日程:绑定视频 / 重复规则 / 到点提醒 / 面板 ──────────────────────
from bilifocus import schedule as schedule_mod  # noqa: E402

plan_video = sample[0]
text_item = service.add_schedule("20:00", "看高等数学")
video_item = service.add_schedule("21:00", video=plan_video)
weekly_item = service.add_schedule(
    "22:00", video=plan_video, repeat=schedule_mod.REPEAT_WEEKLY, weekdays=[0, 2, 4]
)
check("schedule binds the video", video_item.bvid == plan_video.bvid, video_item.bvid)
check("schedule keeps weekly days", weekly_item.weekdays == (0, 2, 4), str(weekly_item.weekdays))

app._update_schedule_hint()
pump(root)
hint_text = app.schedule_hint.cget("text")
check(
    "toolbar shows next schedule item",
    hint_text.startswith("下一项") and any(t in hint_text for t in ("20:00", "21:00", "22:00")),
    hint_text,
)

dialog = ui.ScheduleDialog(root, service, videos=app.videos, on_change=None)
pump(root, rounds=10)
today_items = service.schedule_today()
check(
    "schedule dialog lists today's items",
    len(dialog.list_inner.winfo_children()) == len(today_items),
    f"{len(dialog.list_inner.winfo_children())} vs {len(today_items)}",
)
check("schedule dialog shows the day", bool(dialog.date_label.cget("text")))
pending = [item for item in today_items if not schedule_mod.is_done(item)]
check(
    "schedule dialog summary counts pending",
    f"待做 {len(pending)}" in dialog.summary.cget("text"),
    dialog.summary.cget("text"),
)
check(
    "schedule dialog shows week record",
    "本周" in dialog.week_label.cget("text"),
    dialog.week_label.cget("text"),
)

dialog._toggle(text_item.id)
pump(root)
check(
    "toggle inside dialog marks done",
    any(item.id == text_item.id and schedule_mod.is_done(item) for item in service.schedule_items()),
)
dialog._remove(text_item.id)
pump(root)
check(
    "delete inside dialog removes item",
    text_item.id not in {item.id for item in service.schedule_items()},
)

dialog._start_edit(video_item)
dialog.time_entry.delete(0, "end")
dialog.time_entry.insert(0, "19:45")
dialog._submit()
pump(root)
check(
    "editing changes the time",
    any(item.time == "19:45" for item in service.schedule_items()),
    str([item.time for item in service.schedule_items()]),
)
dialog.close()
pump(root)

# 到点提醒:00:00 一定"已经到点"
recent_time = time.strftime("%H:%M", time.localtime(time.time() - 300))
due_item = service.add_schedule(recent_time, video=plan_video)
app._check_schedule_reminder(force=True)
pump(root)
check(
    "reminder bar appears when a plan is due",
    bool(app.reminder.winfo_ismapped()),
    str(app.reminder.winfo_ismapped()),
)
check(
    "reminder bar names the plan",
    plan_video.title[:6] in app.reminder_text.cget("text"),
    app.reminder_text.cget("text"),
)
app._dismiss_reminder()
pump(root)
check("dismissing hides the reminder", not app.reminder.winfo_ismapped())

# 从日程点"开始看":走播放器;看完自动勾掉日程
app.videos = sample
app._render()
pump(root, rounds=20)
app.player_factory = lambda: FakePlayer(
    PlaybackResult(position=595, duration=600, watched_seconds=590, samples=300)
)
before_urls = len(played_urls)
app._play_schedule_item(video_item)
check(
    "schedule play opens the bound video",
    wait_until(lambda: len(played_urls) > before_urls) and wait_until(lambda: app.playing_key is None),
    f"played={len(played_urls) - before_urls}",
)
check(
    "watching finishes the bound plan",
    wait_until(
        lambda: any(
            item.id == video_item.id and schedule_mod.is_done(item)
            for item in service.schedule_items()
        )
    ),
    str([(item.id, item.time, item.done_dates) for item in service.schedule_items()]),
)
app.player_factory = None

# "加入日程"按钮:把当前选中的收藏带进面板
app.grid_view.select_key(plan_video.key, reveal=False)
captured = {}
app._open_schedule = lambda preselect_video=None, **_: captured.setdefault("video", preselect_video)
app._add_selected_to_schedule()
check(
    "add-to-schedule passes the selected video",
    captured.get("video") is not None and captured["video"].key == plan_video.key,
    str(captured),
)

# ── 退出小结:亲爱的{用户名} + 观看时间 + 已看完比例 ──────────────────
def collect_texts(widget):
    texts = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
        except tk.TclError:
            text = ""
        if text:
            texts.append(str(text))
        texts.extend(collect_texts(child))
    return texts


app.videos = sample
app.service.record_progress(sample[0], position=570, duration=600, watched_seconds=560)
app.username = "测试用户"
app.session_watch_seconds = 95.0
app.session_plays = 2
# × 的语义:全屏时也应该是"退出应用",而不是只退出全屏
app._enter_fullscreen()
pump(root, rounds=5)
app._on_close_button()
pump(root, rounds=30)
check("close button quits the app from fullscreen", app._closing is True)
check("close button keeps fullscreen while quitting", app.fullscreen is True)
pump(root, rounds=40)

farewell = next(
    (child for child in root.winfo_children() if isinstance(child, ui.FarewellDialog)), None
)
check("farewell dialog appears on close", farewell is not None)
farewell_texts = collect_texts(farewell) if farewell is not None else []
check(
    "farewell greeting starts with 亲爱的",
    any(t.strip() == "亲爱的 测试用户" for t in farewell_texts),
    str(farewell_texts[:3]),
)
check("farewell shows finished count", any("已看完" in t for t in farewell_texts), str(farewell_texts[:6]))
check(
    "farewell shows a digest percentage",
    any(t.strip().endswith("%") for t in farewell_texts),
    str(farewell_texts[:6]),
)
check(
    "farewell shows watch time",
    any(("待了" in t) or ("观看" in t) for t in farewell_texts),
    str(farewell_texts),
)
check("farewell has goodbye button", any("再见" in t for t in farewell_texts))

if farewell is not None:
    farewell._finish()
pump(root)
try:
    still_open = bool(root.winfo_exists())
except tk.TclError:
    still_open = False
check("closing the farewell exits the app", not still_open)


print()
if failures:
    print(f"SMOKE TEST FAILED: {failures}")
    sys.exit(1)
print("SMOKE TEST PASSED")
