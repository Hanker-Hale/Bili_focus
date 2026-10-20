"""桌面界面:一个**可替换**的薄壳。

这里刻意只做三件事:把 `FavoritesService` 的数据画出来、把点击翻译成服务调用、
把错误显示成人话。任何数据获取、缓存、已看标记的逻辑都不在这里——所以你以后
重写界面时,底层不需要动。

替换方式:照着 `FocusApp` 用到的服务方法写一个新前端即可,可用方法见
`favfocus.service.FavoritesService`。
"""

from __future__ import annotations

import os
import queue
import threading
import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from . import __version__ as APP_VERSION
from . import theme
from .covers import CoverFetcher
from .errors import AuthRequiredError, FavFocusError
from .models import Video, format_seconds
from .player import BrowserPlayer
from . import schedule as schedule_mod
from . import topics
from . import update as update_mod
from .qrlogin import STATUS_SCANNED, STATUS_WAITING, QrLoginSession
from .service import FavoritesService, search
from .store import StateStore
from .theme import (
    ACCENT,
    ACCENT_DARK,
    BG,
    BG_ALT,
    BORDER,
    CARD_BG,
    ERROR,
    FG,
    FG_DIM,
    WARN,
    enable_dpi_awareness,
    font,
    init_display,
    init_fonts,
    px,
)
from .ui_cards import CardGrid
from .ui_farewell import FarewellDialog, FarewellSummary
from .ui_schedule import ScheduleDialog
from .ui_splash import SplashOverlay

APP_NAME = "FavFocus"
POLL_INTERVAL_MS = 40

# 自动增量:每隔这么久核对一次收藏夹有没有新内容。只请求第 1 页,很便宜。
# 想关掉:把 state.json 里的 auto_sync_seconds 改成 0。
AUTO_SYNC_SECONDS = 180

# 登录框返回这个值代表"走浏览器自动登录",而不是用户填的 SESSDATA
BROWSER_LOGIN = "__browser_login__"
# 登录框返回这个值代表"用手机扫码登录"
QR_LOGIN = "__qr_login__"


def center_window(window: tk.Misc, width: int, height: int) -> None:
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 3)
    window.geometry(f"{width}x{height}+{x}+{y}")


def center_over(window: tk.Misc, parent: tk.Misc, width: int, height: int) -> None:
    """把弹窗居中到父窗口上,并保证不越出屏幕。

    按屏幕居中会让登录框偏到主窗口外面去——第一次运行时尤其明显。
    """
    parent.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = max(parent.winfo_width(), 1), max(parent.winfo_height(), 1)
    x = px + (pw - width) // 2
    y = py + (ph - height) // 2
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    x = max(0, min(x, screen_w - width))
    y = max(0, min(y, screen_h - height))
    window.geometry(f"{width}x{height}+{x}+{y}")


class LoginDialog(tk.Toplevel):
    """第一次运行时要一次 SESSDATA。凭证由 StateStore 保存在本机。"""

    def __init__(self, parent: tk.Misc, initial: str = ""):
        super().__init__(parent)
        self.title("登录 B 站")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.result: str | None = None

        pad = {"padx": 16}
        tk.Label(
            self,
            text="这个收藏夹需要登录才能读取",
            font=font(11, "bold"),
            bg=BG,
            fg=FG,
        ).pack(anchor="w", pady=(px(16), px(4)), **pad)
        tk.Label(
            self,
            text=(
                "请粘贴你的 SESSDATA:浏览器登录 bilibili.com 后按 F12,\n"
                "Application/应用 → Cookies → https://www.bilibili.com,\n"
                "复制 SESSDATA 的值。\n\n"
                "它等同于登录态,只保存在本机,请求只发往 bilibili.com。"
            ),
            justify="left",
            bg=BG,
            fg=FG_DIM,
            font=font(9),
        ).pack(anchor="w", pady=(px(0), px(10)), **pad)

        self.entry = tk.Entry(
            self, width=52, bg=BG_ALT, fg=FG, insertbackground=FG, relief="flat", font=font(10)
        )
        self.entry.pack(ipady=px(6), **pad)
        self.entry.insert(0, initial)

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", pady=px(14), **pad)
        tk.Button(
            buttons,
            text="手机扫码登录",
            command=self._use_qr,
            bg=ACCENT,
            fg="#ffffff",
            relief="flat",
            activebackground=ACCENT_DARK,
            activeforeground="#ffffff",
            padx=px(14),
            pady=px(4),
        ).pack(side="left")
        tk.Button(
            buttons,
            text="用浏览器登录",
            command=self._use_browser,
            bg=BG_ALT,
            fg=FG,
            relief="flat",
            activebackground="#2a2d33",
            activeforeground=FG,
            padx=px(10),
            pady=px(4),
        ).pack(side="left", padx=px(6))
        tk.Button(
            buttons,
            text="保存粘贴的内容",
            command=self._save,
            bg=BG_ALT,
            fg=FG,
            relief="flat",
            activebackground="#2a2d33",
            activeforeground=FG,
            padx=px(12),
            pady=px(4),
        ).pack(side="right")

        self.entry.focus_set()
        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        center_over(self, parent, px(560), px(320))

    def _use_qr(self) -> None:
        self.result = QR_LOGIN
        self.destroy()

    def _use_browser(self) -> None:
        self.result = BROWSER_LOGIN
        self.destroy()

    def _save(self) -> None:
        value = self.entry.get().strip()
        if not value:
            messagebox.showwarning(APP_NAME, "SESSDATA 不能为空", parent=self)
            return
        self.result = value
        self.destroy()


class QrLoginDialog(tk.Toplevel):
    """扫码登录窗口:应用内显示二维码,手机扫完自动完成登录。"""

    POLL_MS = 1800

    def __init__(self, parent: tk.Misc, service: FavoritesService):
        super().__init__(parent)
        self.service = service
        self.session = QrLoginSession()
        self.ticket = None
        self.result = None            # 成功后是 LoginState
        self._closed = False
        self._poll_job = None
        self._drain_job = None
        self._pending = False
        self._queue: "queue.Queue[tuple]" = queue.Queue()

        self.title("扫码登录 B 站")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        tk.Label(
            self, text="用 B 站手机客户端扫码登录", bg=BG, fg=FG, font=font(12, "bold")
        ).pack(padx=px(20), pady=(px(18), px(6)))
        tk.Label(
            self,
            text="打开 B 站 App → 右上角「扫一扫」",
            bg=BG,
            fg=FG_DIM,
            font=font(9),
        ).pack(pady=(0, px(12)))

        self.canvas = tk.Canvas(self, bg="#FFFFFF", highlightthickness=0)
        self.canvas.pack(padx=px(20))

        self.status = tk.Label(self, text="正在生成二维码…", bg=BG, fg=FG_DIM, font=font(10))
        self.status.pack(pady=(px(12), px(6)))

        row = tk.Frame(self, bg=BG)
        row.pack(pady=(0, px(16)))
        tk.Button(
            row,
            text="刷新二维码",
            command=self._refresh,
            bg=BG_ALT,
            fg=FG,
            relief="flat",
            activebackground="#2a2d33",
            activeforeground=FG,
            padx=px(12),
            pady=px(4),
        ).pack(side="left", padx=px(4))
        tk.Button(
            row,
            text="取消",
            command=self._close,
            bg=BG_ALT,
            fg=FG,
            relief="flat",
            activebackground="#2a2d33",
            activeforeground=FG,
            padx=px(12),
            pady=px(4),
        ).pack(side="left", padx=px(4))

        self._drain_job = self.after(50, self._drain)
        self._refresh()
        self.update_idletasks()
        center_over(self, parent, self.winfo_reqwidth() + px(20), self.winfo_reqheight() + px(20))

    # -- 线程帮手(本窗口独立于主界面,自己管一个队列)-------------------
    def _run(self, work, done) -> None:
        def runner():
            try:
                payload = (True, work())
            except Exception as exc:  # noqa: BLE001 - 网络/风控什么都可能抛
                payload = (False, exc)
            self._queue.put((done, payload))

        threading.Thread(target=runner, daemon=True).start()

    def _drain(self) -> None:
        if self._closed:
            return
        try:
            while True:
                done, (ok, value) = self._queue.get_nowait()
                done(value if ok else None, None if ok else value)
        except queue.Empty:
            pass
        self._drain_job = self.after(50, self._drain)

    # -- 二维码 -----------------------------------------------------------
    def _refresh(self) -> None:
        if self._closed:
            return
        self._cancel_poll()
        self.ticket = None
        self.canvas.delete("all")
        self.canvas.configure(width=px(200), height=px(200))
        self._set_status("正在生成二维码…", FG_DIM)
        self._run(self.session.start, self._on_ticket)

    def _on_ticket(self, ticket, error) -> None:
        if self._closed:
            return
        if error is not None:
            self._set_status(f"生成二维码失败:{error}", ERROR)
            return
        self.ticket = ticket
        matrix = self.session.make_qr(ticket.url)
        self._draw(matrix)
        self._set_status("请用 B 站手机客户端扫码", FG_DIM)
        self._schedule_poll()

    def _draw(self, matrix: list[list[bool]]) -> None:
        modules = len(matrix)
        cell = max(px(4), px(200) // modules)
        size = modules * cell
        self.canvas.configure(width=size, height=size)
        dark = "#000000"
        for y, row in enumerate(matrix):
            for x, is_dark in enumerate(row):
                if is_dark:
                    self.canvas.create_rectangle(
                        x * cell, y * cell, (x + 1) * cell, (y + 1) * cell,
                        fill=dark, outline="",
                    )
        self.update_idletasks()
        center_over(self, self.master, self.winfo_reqwidth() + px(20), self.winfo_reqheight() + px(20))

    # -- 轮询 -------------------------------------------------------------
    def _schedule_poll(self) -> None:
        self._cancel_poll()
        self._poll_job = self.after(self.POLL_MS, self._poll)

    def _cancel_poll(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None

    def _poll(self) -> None:
        self._poll_job = None
        if self._closed or self.ticket is None or self._pending:
            return
        self._pending = True
        ticket = self.ticket
        self._run(lambda: self.session.poll(ticket), self._on_poll)

    def _on_poll(self, result, error) -> None:
        self._pending = False
        if self._closed:
            return
        if error is not None:
            self._set_status(f"检查状态失败:{error}", WARN)
            self._schedule_poll()
            return
        if result.done:
            self._set_status("登录成功,正在验证…", ACCENT)
            self._run(lambda: self.service.login(result.sessdata), self._on_login)
            return
        if result.expired:
            self._set_status("二维码已失效,正在刷新…", WARN)
            self._refresh()
            return
        if result.status == STATUS_SCANNED:
            self._set_status(result.message, ACCENT)
        elif result.status == STATUS_WAITING:
            self._set_status(result.message, FG_DIM)
        else:
            self._set_status(result.message, WARN)
        self._schedule_poll()

    def _on_login(self, state, error) -> None:
        if self._closed:
            return
        if error is not None:
            self._set_status(f"扫码成功但登录校验失败:{error}", ERROR)
            return
        self.result = state
        self.destroy()

    def _set_status(self, text: str, color: str = FG_DIM) -> None:
        self.status.configure(text=text, fg=color)

    def _close(self) -> None:
        self._closed = True
        self._cancel_poll()
        if self._drain_job is not None:
            try:
                self.after_cancel(self._drain_job)
            except tk.TclError:
                pass
            self._drain_job = None
        self.destroy()


class FocusApp:
    """主窗口。只依赖 FavoritesService 的公开方法。"""

    def __init__(
        self,
        root: tk.Tk,
        fid_override: int | None = None,
        mid: int = 0,
        bootstrap: bool = True,
        service: FavoritesService | None = None,
        fullscreen: bool = True,
    ):
        self.root = root
        self.service = service or FavoritesService(store=StateStore())
        self.mid = int(mid or 0) or (self.service.default_mid or 0)
        if fid_override:
            self.service.set_default_fid(fid_override)
        # 自动增量的节奏由这一个设置决定;服务层的节流也用同一个值,
        # 否则界面每 15 秒问一次、服务层 180 秒才允许联网,就永远查不到新内容。
        auto_seconds = self._auto_sync_seconds()
        if auto_seconds > 0:
            self.service.sync_interval = float(auto_seconds)

        self.videos: list[Video] = []
        self.visible: list[Video] = []
        self.folders: list = []
        self.from_cache = False
        self.fetched_at = 0.0
        self.cover_fetcher = CoverFetcher(self.service.store.home / "cache" / "covers")

        # 后台线程只往这里写,主线程轮询消费。线程不碰任何 Tk 对象。
        self._results: dict[object, tuple] = {}
        self._progress_text = ""
        self._closing = False
        self._poll_job = None
        self._auto_job = None
        self._auto_busy = False
        self.busy = False
        self.username: str = str(self.service.store.get_pref("last_uname", "") or "")
        self.playing_key: str | None = None
        self._reminder_item = None
        self._reminder_checked_at = 0.0
        self._update_info = None
        self._update_installer = None
        self._update_state = "idle"
        self._update_checked = False
        self._update_overlay = None
        self._update_progress = None
        self._update_progress_text = ""
        self._auto_install_var = None
        self.session_watch_seconds: float = 0.0
        self.session_plays: int = 0

        self._build_ui()
        self._bind_keys()
        self.fullscreen = False
        if fullscreen:
            # 等窗口真正映射到屏幕上再切全屏,否则有些系统会切不干净
            self.root.after(120, self._enter_fullscreen)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._install_error_log()
        self._install_fatal_log()
        # 启动画面已经盖在内容区上了,不需要再往网格里写占位文字
        self._update_schedule_hint()
        self._poll_job = self.root.after(POLL_INTERVAL_MS, self._poll_results)
        self.root.after(2500, self._maybe_check_updates)
        if bootstrap:
            self.root.after(120, self._bootstrap)
        self._schedule_auto_sync()

    # -- 界面构建 ---------------------------------------------------------
    def _build_ui(self) -> None:
        init_fonts(self.root)   # 先定字体,后面的控件才用得上
        init_display(self.root)  # 再按 DPI 定尺寸
        self.root.title(f"{APP_NAME} — 只看收藏夹")
        self.root.configure(bg=BG)
        self.root.minsize(px(620), px(420))
        self._apply_window_icon()
        # 存的是逻辑尺寸,这里按 DPI 换算;再夹一下,防止旧偏好(按不感知 DPI 存的)
        # 让窗口超出屏幕
        logical_w = int(self.service.store.get_pref("width", 780) or 780)
        logical_h = int(self.service.store.get_pref("height", 560) or 560)
        max_w = int(self.root.winfo_screenwidth() / max(theme.SCALE, 1.0)) - 40
        max_h = int(self.root.winfo_screenheight() / max(theme.SCALE, 1.0)) - 90
        width = px(max(620, min(logical_w, max_w)))
        height = px(max(420, min(logical_h, max_h)))
        center_window(self.root, width, height)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Focus.TCombobox", fieldbackground=BG_ALT, background=BG_ALT, foreground=FG, arrowcolor=FG
        )

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=px(14), pady=(px(12), px(6)))
        tk.Label(bar, text="收藏夹", bg=BG, fg=FG_DIM, font=font(9)).pack(side="left")
        self.folder_var = tk.StringVar()
        self.folder_box = ttk.Combobox(
            bar, textvariable=self.folder_var, state="readonly", width=30, style="Focus.TCombobox"
        )
        self.folder_box.pack(side="left", padx=(px(6), px(10)))
        self.folder_box.bind("<<ComboboxSelected>>", self._on_folder_change)
        self.refresh_btn = self._button(bar, "刷新", self.refresh)
        self.refresh_btn.pack(side="left")
        self._button(bar, "日程", self._open_schedule).pack(side="left", padx=(px(6), 0))
        self.schedule_hint = tk.Label(bar, text="", bg=BG, fg=FG_DIM, font=font(9))
        self.schedule_hint.pack(side="left", padx=(px(10), 0))

        # 右上角的 ×:和背景同色系,只有一点色差,不抢视线。
        # 全屏时没有窗口边框,它是"退出全屏"的鼠标入口(键盘按 ESC 也行)。
        self.close_btn = tk.Label(
            bar,
            text="×",
            bg=BG,
            fg=FG_DIM,
            font=font(14),
            padx=px(10),
            cursor="hand2",
        )
        self.close_btn.pack(side="right", padx=(px(6), 0))
        self.close_btn.bind("<Button-1>", lambda _event: self._on_close_button())
        self.close_btn.bind(
            "<Enter>",
            lambda _event: self.close_btn.configure(bg=BG_ALT, fg=FG),
        )
        self.close_btn.bind(
            "<Leave>",
            lambda _event: self.close_btn.configure(bg=BG, fg=FG_DIM),
        )

        self.group_var = tk.BooleanVar(value=bool(self.service.store.get_pref("auto_group", True)))
        tk.Checkbutton(
            bar,
            text="自动分类",
            variable=self.group_var,
            command=self._on_toggle_group,
            bg=BG,
            fg=FG_DIM,
            selectcolor=BG_ALT,
            activebackground=BG,
            activeforeground=FG,
            font=font(9),
        ).pack(side="right", padx=(0, px(12)))

        self.hide_var = tk.BooleanVar(value=bool(self.service.store.get_pref("hide_watched", False)))
        tk.Checkbutton(
            bar,
            text="隐藏已看",
            variable=self.hide_var,
            command=self._on_toggle_hide,
            bg=BG,
            fg=FG_DIM,
            selectcolor=BG_ALT,
            activebackground=BG,
            activeforeground=FG,
            font=font(9),
        ).pack(side="right")

        search_row = tk.Frame(self.root, bg=BG)
        search_row.pack(fill="x", padx=px(14), pady=(px(0), px(8)))
        tk.Label(search_row, text="筛选", bg=BG, fg=FG_DIM, font=font(9)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render())
        self.search_entry = tk.Entry(
            search_row,
            textvariable=self.search_var,
            bg=BG_ALT,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            font=font(10),
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(px(6), px(10)), ipady=px(4))

        # 到点提醒条:只在"该看视频了"的时候出现,平时不占地方
        self.reminder = tk.Frame(self.root, bg=BG_ALT, highlightthickness=1,
                                 highlightbackground=BORDER)
        accent = tk.Frame(self.reminder, bg=ACCENT, width=px(3))
        accent.pack(side="left", fill="y")
        self.reminder_text = tk.Label(
            self.reminder, text="", bg=BG_ALT, fg=FG, font=font(10), anchor="w"
        )
        self.reminder_text.pack(side="left", padx=(px(10), px(6)), pady=px(7))
        self._button(self.reminder, "开始看", self._play_reminder_item, primary=True).pack(
            side="left", padx=(0, px(6)), pady=px(5)
        )
        self._button(self.reminder, "稍后", self._dismiss_reminder).pack(
            side="left", padx=(0, px(8)), pady=px(5)
        )

        # 内容区:卡片网格 + 盖在上面的启动页(splash)
        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(fill="both", expand=True)

        # 卡片网格:Canvas + Frame 实现,Listbox 放不进图片。
        self.grid_view = CardGrid(
            self.content,
            self.cover_fetcher,
            on_activate=self._on_card_activate,
            on_group_toggle=self._on_group_toggle,
            on_context=self._on_card_context,
        )
        self.grid_view.pack(fill="both", expand=True, padx=px(14))
        self.grid_view.canvas.bind("<Return>", lambda _event: self.play_selected())
        self.grid_view.canvas.bind("<space>", lambda _event: self.toggle_watched())

        self.splash = SplashOverlay(self.content, "正在检查登录状态…")
        self.splash.place(relx=0, rely=0, relwidth=1, relheight=1)

        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", padx=px(14), pady=(px(8), px(4)))
        self.play_btn = self._button(foot, "▶ 播放", self.play_selected, primary=True)
        self.play_btn.pack(side="left")
        self._button(foot, "标记已看", self.toggle_watched).pack(side="left", padx=px(6))
        self._button(foot, "随机一条", self.play_random).pack(side="left")
        self._button(foot, "加入日程", self._add_selected_to_schedule).pack(
            side="left", padx=(px(6), 0)
        )
        self._button(foot, "换登录", self.change_login).pack(side="right")

        self.status = tk.Label(
            self.root, text="启动中…", bg=BG, fg=FG_DIM, anchor="w", font=font(9)
        )
        self.status.pack(fill="x", padx=px(14), pady=(px(0), px(4)))
        hint_row = tk.Frame(self.root, bg=BG)
        hint_row.pack(fill="x", padx=px(14), pady=(px(0), px(10)))
        tk.Label(
            hint_row,
            text="双击或回车播放 · 空格标记已看 · 右键改分类 · Ctrl+F 筛选 · Ctrl+R 刷新 · Esc 清空筛选",
            bg=BG,
            fg="#5c5e64",
            anchor="w",
            font=font(8),
        ).pack(side="left")
        self.version_label = tk.Label(
            hint_row,
            text=f"v{APP_VERSION} · 检查更新",
            bg=BG,
            fg=FG_DIM,
            font=font(8),
            cursor="hand2",
        )
        self.version_label.pack(side="right")
        self.version_label.bind("<Button-1>", lambda _event: self._check_updates_now())

    def _apply_window_icon(self) -> None:
        """把窗口/任务栏图标换成 assets/favfocus-icon.ico(失败就保持默认)。"""
        icon = Path(__file__).resolve().parent.parent / "assets" / "favfocus-icon.ico"
        if not icon.is_file():
            return
        try:
            self.root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass

    def _install_fatal_log(self) -> None:
        """把"逃出 Tk 回调"的致命异常也写进日志。

        pythonw 没有 stderr,主线程里未捕获的异常会让程序**静默消失**。
        这里兜住并在状态栏提示,方便排查"用着用着就没了"的问题。
        """

        def hook(exc_type, exc_value, exc_tb):
            import traceback

            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            try:
                path = self.service.store.home / "ui-errors.log"
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] FATAL\n{text}")
            except OSError:
                pass
            try:
                self._set_status(f"程序遇到错误(已记录 ui-errors.log):{exc_value}", ERROR)
            except Exception:  # noqa: BLE001
                pass

        sys.excepthook = hook

    def _install_error_log(self) -> None:
        """Tk 回调里的异常默认只打到 stdout,用 pythonw 启动时等于消失。

        这里改成追加到日志文件,下次“点了没反应”有据可查。
        """

        def handler(exc_type, exc_value, exc_tb):
            import traceback

            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            try:
                path = self.service.store.home / "ui-errors.log"
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n{text}")
            except OSError:
                pass
            self._set_status(f"界面出错(已记录到 ui-errors.log):{exc_value}", ERROR)

        self.root.report_callback_exception = handler

    def _button(self, parent, text, command, primary: bool = False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=ACCENT if primary else BG_ALT,
            fg="#ffffff" if primary else FG,
            relief="flat",
            activebackground=ACCENT_DARK if primary else "#2a2d33",
            activeforeground="#ffffff" if primary else FG,
            padx=px(14) if primary else 10,
            pady=px(4),
            font=font(9),
            cursor="hand2",
        )

    def _bind_keys(self) -> None:
        self.root.bind("<Control-f>", lambda _event: self._focus_search())
        self.root.bind("<Control-r>", lambda _event: self.refresh())
        self.root.bind("<Escape>", lambda _event: self._on_escape())
        # 网格里上下是一整行(按当前列数),左右是一个
        self.root.bind("<Up>", lambda _event: self._move_vertical(-1))
        self.root.bind("<Down>", lambda _event: self._move_vertical(1))
        self.root.bind("<Left>", lambda _event: self._move(-1))
        self.root.bind("<Right>", lambda _event: self._move(1))
        self.root.bind("<Prior>", lambda _event: self._move(-self.grid_view._columns * 3))
        self.root.bind("<Next>", lambda _event: self._move(self.grid_view._columns * 3))
        self.root.bind("<Return>", lambda _event: self.play_selected())
        self.root.bind("<space>", lambda _event: self.toggle_watched())

    # -- 线程 ↔ 主线程 ----------------------------------------------------
    def _run_async(self, work, done) -> None:
        import threading

        ticket = object()

        def runner():
            try:
                payload = (True, work())
            except Exception as exc:  # noqa: BLE001 - 网络层什么都可能抛
                payload = (False, exc)
            self._results[ticket] = (done, payload)

        threading.Thread(target=runner, daemon=True).start()

    def _poll_results(self) -> None:
        if self._closing:
            return
        self._check_schedule_reminder()
        if self._progress_text:
            self._set_status(self._progress_text)
            self._progress_text = ""
        if self._update_progress_text:
            if self._update_progress is not None:
                try:
                    self._update_progress.configure(text=self._update_progress_text)
                except tk.TclError:
                    self._update_progress = None
            self._update_progress_text = ""
        for ticket in list(self._results.keys()):
            entry = self._results.pop(ticket, None)
            if entry is None:
                continue
            done, (ok, value) = entry
            try:
                done(value if ok else None, None if ok else value)
            except Exception as exc:  # noqa: BLE001 - 回调异常不该杀死轮询
                self._set_status(f"界面更新出错:{exc}", ERROR)
        self._update_schedule_hint()
        self._poll_job = self.root.after(POLL_INTERVAL_MS, self._poll_results)

    def _set_status(self, text: str, color: str = FG_DIM) -> None:
        self.status.configure(text=text, fg=color)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.refresh_btn.configure(text="…" if busy else "刷新", state="disabled" if busy else "normal")

    # -- 启动 -------------------------------------------------------------
    def _bootstrap(self) -> None:
        if not self.service.is_logged_in:
            self._prompt_login()
            return
        self._verify_and_load()

    def _prompt_login(self) -> None:
        dialog = LoginDialog(self.root, self.service.store.sessdata or "")
        self.root.wait_window(dialog)
        if dialog.result == QR_LOGIN:
            self._qr_login()
            return
        if dialog.result == BROWSER_LOGIN:
            self._browser_login()
            return
        if not dialog.result:
            self._set_status("未登录。点「换登录」用手机扫码登录。", WARN)
            self._show_placeholder("还没有登录。\n\n点右下角「换登录」→「手机扫码登录」。")
            return
        try:
            state = self.service.login(dialog.result)
        except (FavFocusError, ValueError) as error:
            self._set_status(f"登录失败:{error}", ERROR)
            self._show_placeholder(f"登录失败:\n\n{error}")
            return
        self._set_status(f"已登录:{state.uname or ''}")
        self._load_folders()

    def _qr_login(self) -> None:
        """应用内显示二维码,手机扫码后自动完成登录。"""
        self._set_status("等待扫码…")
        self._show_placeholder("已打开扫码窗口。\n\n用 B 站手机客户端扫描二维码即可。")
        dialog = QrLoginDialog(self.root, self.service)
        self.root.wait_window(dialog)
        if dialog.result is None:
            self._set_status("扫码登录已取消。", WARN)
            self._show_placeholder("登录已取消。\n\n点右下角「换登录」→「手机扫码登录」重试。")
            return
        self._remember_user(dialog.result.uname, getattr(dialog.result, "mid", None))
        self._set_status(f"已登录:{dialog.result.uname or ''}")
        self._load_folders()

    def _browser_login(self) -> None:
        """打开独立浏览器让你登录,登录完成后自动取走 SESSDATA。"""
        self._set_status("正在打开浏览器…")
        self._show_placeholder(
            "已打开一个独立的浏览器窗口\n\n"
            "（不是你日常用的那个，登录态只属于本软件）\n\n"
            "请在窗口里登录 B 站，登录完成后这里会自动继续。\n"
            "关闭那个窗口会取消本次登录。"
        )

        def work():
            def progress(message: str) -> None:
                # 只赋值,由主线程轮询显示
                self._progress_text = message

            return self.service.login_via_browser(progress=progress)

        def done(state, error):
            if error is not None:
                self._set_status(f"浏览器登录失败:{error}", ERROR)
                self._show_placeholder(
                    f"没能自动获取登录态:\n\n{error}\n\n"
                    "可以再点「换登录」→「用浏览器登录」重试,\n"
                    "或者手动粘贴 SESSDATA。"
                )
                return
            self._remember_user(state.uname, state.mid)
            self._remember_user(state.uname, state.mid)
            self._set_status(f"已登录:{state.uname or ''}")
            self._load_folders()

        self._run_async(work, done)

    def _verify_and_load(self) -> None:
        self._splash_status("正在验证登录…")
        self._set_status("正在验证登录…")

        def work():
            return self.service.login_state()

        def done(state, error):
            if error is not None:
                self._set_status(f"验证登录失败:{error}", ERROR)
                return
            if not state.is_login:
                # 不要自动删凭证:B 站偶发抽风、或用户只是临时没网,
                # 一删就得重新扫码,代价太大。留着,让用户自己决定要不要重登。
                self._set_status("登录态已失效,请点右下角「换登录」重新扫码。", ERROR)
                self._show_placeholder(
                    "登录态已失效。\n\n点右下角「换登录」→「手机扫码登录」重新扫码即可。"
                )
                return
            self._remember_user(state.uname, state.mid)
            self._remember_user(state.uname, state.mid)
            self._set_status(f"已登录:{state.uname or ''}")
            self._load_folders()

        self._run_async(work, done)

    def _load_folders(self) -> None:
        if not self.mid:
            self._set_status("不知道账号 mid:请用 --mid 指定,或点「换登录」重新登录。", WARN)
            self._show_placeholder("不知道账号 mid。\n\n点右下角「换登录」重新登录即可自动记住。")
            return
        self._splash_status("正在读取收藏夹…")
        self._set_busy(True)
        self._set_status("正在读取收藏夹列表…")

        def work():
            return self.service.folders(self.mid)

        def done(folders, error):
            self._set_busy(False)
            if error is not None:
                self._set_status(f"读取收藏夹失败:{error}", ERROR)
                return
            self.folders = list(folders)
            self.folder_box.configure(
                values=[f"{folder.title} ({folder.media_count})" for folder in self.folders]
            )
            if not self.folders:
                self._set_status("这个账号下没有可见的收藏夹。", WARN)
                self._show_placeholder("没有读到收藏夹。\n\n可能 mid 不对,或私密收藏夹不属于当前登录账号。")
                return
            index = 0
            target = self.service.default_fid
            if target:
                for position, folder in enumerate(self.folders):
                    if folder.fid == target:
                        index = position
                        break
            self.folder_box.current(index)
            self._on_folder_change(force=True)

        self._run_async(work, done)

    def _on_folder_change(self, _event=None, force: bool = False) -> None:
        index = self.folder_box.current()
        if index < 0 or index >= len(self.folders):
            return
        fid = self.folders[index].fid
        if not force and fid == self.service.default_fid and self.videos:
            return
        self.service.set_default_fid(fid)
        self.refresh()

    # -- 数据 -------------------------------------------------------------
    def refresh(self) -> None:
        fid = self.service.default_fid
        if not fid:
            self._set_status("请先选择收藏夹。", WARN)
            return
        self._splash_status("正在拉取收藏内容…")
        self._set_busy(True)
        self._set_status("正在拉取收藏内容…")

        def progress(page: int, count: int, total: int) -> None:
            # 只有赋值,不碰 Tk;由轮询线程负责显示
            self._progress_text = f"正在拉取… 第 {page} 页,已取 {count}" + (f"/{total}" if total else "")

        def work():
            return self.service.refresh(fid, progress=progress)

        def done(view, error):
            self._set_busy(False)
            if error is not None:
                if isinstance(error, AuthRequiredError):
                    self._set_status("登录态失效,请点「换登录」重新粘贴。", ERROR)
                else:
                    self._set_status(f"读取失败:{error}", ERROR)
                return
            self.videos = list(view.videos)
            self.from_cache = view.from_cache
            self.fetched_at = view.fetched_at
            self._render()
            stats = self.service.stats(view)
            digest = self.service.digest(self.videos)
            source = "缓存" if view.from_cache else "刚拉取"
            self._set_status(
                f"共 {stats['count']} 条,已看完 {digest['finished']},"
                f"未看完 {digest['remaining']}({source})。双击播放。"
            )

        self._run_async(work, done)

    # -- 自动增量 ---------------------------------------------------------
    def _auto_sync_seconds(self) -> int:
        """自动核对间隔(秒)。写成 0 就关掉自动增量。"""
        try:
            value = int(self.service.store.get_pref("auto_sync_seconds", AUTO_SYNC_SECONDS) or 0)
        except (TypeError, ValueError):
            value = AUTO_SYNC_SECONDS
        return max(0, value)

    def _schedule_auto_sync(self) -> None:
        if self._closing:
            return
        seconds = self._auto_sync_seconds()
        if seconds <= 0:
            return
        self._auto_job = self.root.after(seconds * 1000, self._auto_sync_tick)

    def _auto_sync_tick(self) -> None:
        self._auto_job = None
        if self._closing:
            return
        fid = self.service.default_fid
        # 正在手动刷新、或者上一次自动检查还没回来,就先跳过这一轮
        if fid and not self.busy and not self._auto_busy and self.videos:
            self._auto_sync(fid)
        self._schedule_auto_sync()

    def _auto_sync(self, fid: int) -> None:
        """静默核对一次:有新内容才重画,没变化不打扰你。"""
        self._auto_busy = True

        def work():
            return self.service.videos(fid)

        def done(view, error):
            self._auto_busy = False
            if error is not None:
                # 自动检查失败不该弹窗打断,只更新状态栏
                self._set_status(f"自动检查失败:{error}", WARN)
                return
            if not view.new_count:
                return
            keep = self._selected_key()
            self.videos = list(view.videos)
            self.from_cache = view.from_cache
            self.fetched_at = view.fetched_at
            self._render()
            self._restore_selection(keep)
            self._set_status(f"已自动更新:新增 {view.new_count} 条", ACCENT)

        self._run_async(work, done)

    def _selected_key(self) -> str | None:
        video = self._selected()
        return video.key if video else None

    def _restore_selection(self, key: str | None) -> None:
        """重画之后把选中项还原,免得列表一跳就"跳走"。"""
        if key:
            self.grid_view.select_key(key, reveal=False)

    def _filtered(self) -> list[Video]:
        items = search(self.videos, self.search_var.get())
        if self.hide_var.get():
            items = [video for video in items if not self.service.is_watched(video)]
        return items

    def _render(self) -> None:
        # 真的有内容要画了 → 启动画面让位
        self._hide_splash()
        keep = self.grid_view.selected_key
        self.visible = self._filtered()
        watched = {video.key for video in self.visible if self.service.is_watched(video)}
        progress = self.service.progress_view(self.visible)
        groups = (
            topics.group(self.visible, self.service.topic_overrides())
            if self.group_var.get()
            else None
        )
        collapsed = set(self.service.store.get_pref("collapsed_groups", []) or [])
        self.grid_view.set_videos(self.visible, watched, progress, groups, collapsed)
        if self.visible:
            # 尽量保持原来的选中;原来那条被筛掉了就落到第一条
            if keep and keep in {video.key for video in self.visible}:
                self.grid_view.select_key(keep, reveal=False)
            elif self.grid_view.selected() is None:
                self.grid_view.select_key(self.visible[0].key, reveal=False)
        elif self.videos:
            self._show_placeholder("这里没有内容。\n\n换个筛选词,或者取消「隐藏已看」。")

    def _hide_splash(self) -> None:
        splash = getattr(self, "splash", None)
        if splash is None or not splash.visible:
            return
        # 启动太快时别闪一下就消失:至少让动画完整跑 0.6 秒
        remaining = splash.remaining_ms()
        if remaining > 0:
            self.root.after(remaining, self._hide_splash)
            return
        splash.hide()

    def _splash_status(self, text: str) -> None:
        """启动阶段的进度文案:只更新启动页,不打扰底部状态栏。"""
        splash = getattr(self, "splash", None)
        if splash is not None and splash.visible:
            splash.set_status(text)

    def _show_placeholder(self, text: str) -> None:
        # 走到这里说明启动流程结束了(登录/失败/空列表),启动画面该退场
        self._hide_splash()
        self.grid_view.set_message(text)

    def _focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, tk.END)
        return "break"

    def _clear_search(self) -> None:
        self.search_var.set("")
        return "break"

    def _move(self, delta: int) -> None:
        """左右移动(上下由 _move_vertical 处理)。"""
        self.grid_view.move(delta)
        return "break"

    def _move_vertical(self, direction: int) -> None:
        self.grid_view.move_vertical(direction)
        return "break"

    def _selected(self) -> Video | None:
        return self.grid_view.selected()

    def _on_card_activate(self, video: Video) -> None:
        """双击卡片。"""
        self.play_selected()

    # -- 动作 -------------------------------------------------------------
    def play_selected(self) -> None:
        video = self._selected()
        if video is None:
            self._set_status("先选中一条。", WARN)
            return
        self._play_video(video)

    def play_random(self) -> None:
        video = self.service.random_unwatched(self._filtered())
        if video is None:
            self._set_status("没有可随机的内容。", WARN)
            return
        if video in self.visible:
            self.grid_view.select_key(video.key, reveal=False)
        self._play_video(video)

    # -- 播放(自动全屏 + 进度跟踪)----------------------------------------
    def _player(self) -> BrowserPlayer:
        """可替换的播放器工厂:测试里注入假实现。"""
        factory = getattr(self, "player_factory", None)
        if factory is not None:
            return factory()
        return BrowserPlayer(self.service.store.home / "player-profile")

    def _play_video(self, video: Video) -> None:
        if not video.is_openable:
            self._set_status("这条已失效,没有可打开的链接。", WARN)
            return
        if self.playing_key:
            self._set_status("已经有一个播放器窗口了,先关掉它。", WARN)
            return

        start_at = self.service.resume_position(video)
        self.playing_key = video.key
        hint = f",从 {format_seconds(start_at)} 继续" if start_at else ""
        self._set_status(f"正在打开播放器…{hint}")
        url = video.play_url(start_at)

        def work():
            return self._player().play(url, sessdata=self.service.store.sessdata)

        def done(result, error):
            self.playing_key = None
            self._set_busy(False)
            if error is not None:
                # 播放器起不来时退回默认浏览器:能看,但这次记不到进度
                webbrowser.open(url)
                self._set_status(f"无法跟踪进度,已用默认浏览器打开:{error}", WARN)
                return
            self._after_playback(video, result)

        self._set_busy(True)
        self._run_async(work, done)

    def _after_playback(self, video: Video, result) -> None:
        """播放结束:保存进度,刷新卡片。"""
        if not result.samples:
            self._set_status(f"没有检测到播放,已取消进度记录:{video.title}", WARN)
            return
        self.session_watch_seconds += max(0.0, float(result.watched_seconds or 0))
        self.session_plays += 1
        record = self.service.record_progress(
            video,
            position=result.position,
            duration=result.duration,
            watched_seconds=result.watched_seconds,
        )
        if record["finished"]:
            self.grid_view.set_watched(video.key, True)
            try:
                touched = self.service.finish_schedule_for_video(video)
            except Exception:  # noqa: BLE001 - 日程出错不该影响播放结算
                touched = 0
            if touched:
                self._set_status(f"已看完,并勾掉 {touched} 条日程:{video.title}")
                self._hide_reminder()
            # 看完也保留进度记录:退出小结的"已看完"要按它统计
            self.grid_view.set_progress(
                video.key,
                {"ratio": 1.0, "position": int(record["duration"]), "finished": True},
            )
            self._set_status(
                f"已看完(本次观看 {format_seconds(result.watched_seconds)}):{video.title}"
            )
        else:
            self.grid_view.set_progress(
                video.key,
                {"ratio": record["ratio"], "position": int(record["position"])},
            )
            self._set_status(
                f"看到 {format_seconds(record['position'])} / "
                f"{format_seconds(record['duration'])},本次观看 "
                f"{format_seconds(result.watched_seconds)},下次从这里继续"
            )
        self._update_schedule_hint()
        if self.hide_var.get():
            self._render()

    def toggle_watched(self) -> None:
        video = self._selected()
        if video is None:
            return
        watched = self.service.toggle_watched(video)
        self.grid_view.set_watched(video.key, watched)
        self._update_schedule_hint()
        if self.hide_var.get():
            self._render()
        self._set_status(f"{'已标记看过' if watched else '已取消标记'}:{video.title}")

    def _on_toggle_hide(self) -> None:
        self.service.store.set_pref("hide_watched", bool(self.hide_var.get()))
        self._render()

    # -- 全屏与退出 -------------------------------------------------------
    def _window_handle(self) -> int:
        """Windows 上取顶层窗口句柄(考虑 Tk 的包装窗口)。"""
        try:
            import ctypes

            user32 = ctypes.windll.user32
            inner = self.root.winfo_id()
            parent = user32.GetParent(inner)
            return int(parent) or int(inner)
        except Exception:  # noqa: BLE001 - 非 Windows 或取不到句柄
            return 0

    def _frame_present(self) -> bool:
        """窗口是否还带着标题栏/可调边框(Windows 专用判断)。"""
        if sys.platform != "win32":
            return False
        handle = self._window_handle()
        if not handle:
            return False
        try:
            import ctypes

            style = ctypes.windll.user32.GetWindowLongW(handle, -16) & 0xFFFFFFFF
        except Exception:  # noqa: BLE001
            return False
        return bool(style & 0x00C00000) or bool(style & 0x00040000)

    def _enter_fullscreen(self) -> None:
        """进入无边框全屏。

        只依赖 Tk 的 `-fullscreen` 不够稳:某些窗口状态下(比如之前最大化过、
        或刚退出过一次全屏)系统可能残留边框。所以这里做三件事:
        1. 先把最大化状态复位(Tk/Windows 在 zoomed 下切全屏容易留边框);
        2. 切 `-fullscreen`;
        3. **真的去查窗口样式**,确认边框没了;还有边框就改用 overrideredirect
           自己铺满屏幕 —— 宁可多一层兜底,也不让用户看到难看的边框。
        """
        if not getattr(self, "fullscreen", False):
            try:
                self._windowed_geometry = (
                    self.root.winfo_width(),
                    self.root.winfo_height(),
                )
            except tk.TclError:
                self._windowed_geometry = None

        try:
            if self.root.state() == "zoomed":
                self.root.state("normal")
                self.root.update_idletasks()
        except tk.TclError:
            pass

        self._borderless_fallback = False
        try:
            self.root.attributes("-fullscreen", True)
        except tk.TclError:
            pass
        self.root.update_idletasks()

        if self._frame_present():
            self._borderless_fallback = True
            try:
                self.root.attributes("-fullscreen", False)
            except tk.TclError:
                pass
            try:
                self.root.overrideredirect(True)
                screen_w = self.root.winfo_screenwidth()
                screen_h = self.root.winfo_screenheight()
                self.root.geometry(f"{screen_w}x{screen_h}+0+0")
            except tk.TclError:
                pass

        self.fullscreen = True
        try:
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def _exit_fullscreen(self) -> None:
        try:
            self.root.attributes("-fullscreen", False)
        except tk.TclError:
            pass
        if getattr(self, "_borderless_fallback", False):
            try:
                self.root.overrideredirect(False)
            except tk.TclError:
                pass
            self._borderless_fallback = False
        self.fullscreen = False
        # 回到普通窗口:用进全屏前记住的尺寸重新居中
        try:
            remembered = getattr(self, "_windowed_geometry", None)
            if remembered:
                width, height = remembered
                logical_w = int(width / max(theme.SCALE, 1.0))
                logical_h = int(height / max(theme.SCALE, 1.0))
            else:
                logical_w = int(self.service.store.get_pref("width", 780) or 780)
                logical_h = int(self.service.store.get_pref("height", 560) or 560)
            max_w = int(self.root.winfo_screenwidth() / max(theme.SCALE, 1.0)) - 40
            max_h = int(self.root.winfo_screenheight() / max(theme.SCALE, 1.0)) - 90
            center_window(
                self.root,
                px(max(620, min(logical_w, max_w))),
                px(max(420, min(logical_h, max_h))),
            )
            self.root.update_idletasks()
        except Exception:  # noqa: BLE001 - 恢复尺寸失败不影响使用
            pass
        self._set_status("已退出全屏,按 F11 可重新全屏")

    def _toggle_fullscreen(self) -> None:
        if getattr(self, "fullscreen", False):
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()
            self._set_status("已进入全屏,按 ESC 退出")

    def _on_escape(self) -> None:
        if getattr(self, "fullscreen", False):
            self._exit_fullscreen()
            return
        self._clear_search()

    def _on_close_button(self) -> None:
        """右上角 ×:**直接退出应用,不退出全屏**。

        退出全屏是 ESC 的职责。点 × 就是"关掉它":界面保持全屏,
        弹出退出小结,点"再见"后进程结束——不会先闪一下窗口模式。
        """
        self._on_close()

    # -- 日程 -------------------------------------------------------------
    def _open_schedule(self, preselect_video=None) -> None:
        dialog = ScheduleDialog(
            self.root,
            self.service,
            videos=self.videos,
            on_play=self._play_schedule_item,
            on_change=self._after_schedule_change,
            preselect_video=preselect_video,
        )
        self.root.wait_window(dialog)
        self._after_schedule_change()

    def _add_selected_to_schedule(self) -> None:
        """把当前选中的收藏加进日程:选时间 + 重复规则。"""
        video = self._selected()
        if video is None:
            self._set_status("先选中一条,再加日程。", WARN)
            return
        self._open_schedule(preselect_video=video)

    def _play_schedule_item(self, item) -> None:
        """从日程里点"开始看":找到绑定的那条收藏再播放。"""
        video = self._video_for_schedule(item)
        if video is None:
            self._set_status("这条日程绑定的视频不在当前收藏夹里(可能被移除了)。", WARN)
            return
        self._hide_reminder()
        self._play_video(video)

    def _video_for_schedule(self, item):
        key = getattr(item, "bvid", "")
        if not key:
            return None
        for video in self.videos:
            if video.bvid == key:
                return video
        return None

    def _after_schedule_change(self) -> None:
        self._update_schedule_hint()
        self._check_schedule_reminder(force=True)

    def _update_schedule_hint(self) -> None:
        """工具栏上的一行小提示:今天还剩几项 / 下一项是什么。"""
        label = getattr(self, "schedule_hint", None)
        if label is None:
            return
        try:
            today_items = self.service.schedule_today()
        except Exception:  # noqa: BLE001 - 日程读失败不该影响主界面
            return
        if not today_items:
            label.configure(text="")
            return
        pending = [item for item in today_items if not schedule_mod.is_done(item)]
        if not pending:
            label.configure(
                text=f"今日 {len(today_items)} 项已看完", fg=ACCENT
            )
            return
        nxt = self.service.schedule_next()
        if nxt is None:
            label.configure(text="")
            return
        title = nxt.title if len(nxt.title) <= 12 else nxt.title[:11] + "…"
        label.configure(text=f"下一项 {nxt.time} · {title}", fg=FG_DIM)

    # -- 检查更新 ---------------------------------------------------------
    UPDATE_CHECK_INTERVAL = 10 * 60  # 启动时检查;10 分钟内重复启动不重复查

    def _maybe_check_updates(self, force: bool = False, manual: bool = False) -> None:
        """查有没有新版本。

        - 启动时自动查:10 分钟内查过就跳过,不打扰用户;
        - 手动查(点右下角版本号):总是真的去查,并反馈结果。
        """
        if self._closing:
            return
        if os.environ.get("FAVFOCUS_NO_UPDATE_CHECK") or os.environ.get("BILI_FOCUS_NO_UPDATE_CHECK"):
            return
        if not manual and self._update_checked:
            return
        if not force and not manual:
            try:
                last = float(self.service.store.get_pref("update_checked_at", 0) or 0)
            except (TypeError, ValueError):
                last = 0.0
            if time.time() - last < self.UPDATE_CHECK_INTERVAL:
                return
        self._update_checked = True
        if manual:
            self._set_status("正在检查更新…")

        def work():
            return update_mod.check_for_update(APP_VERSION)

        def done(result, error):
            try:
                self.service.store.set_pref("update_checked_at", time.time())
            except Exception:  # noqa: BLE001 - 记不住时间不影响使用
                pass
            if error is not None or not result:
                if manual:
                    self._set_status("检查更新失败,请检查网络后重试", WARN)
                return
            info, newer = result
            if not newer:
                if manual:
                    self._set_status(f"已是最新版本(v{APP_VERSION})")
                return
            if not manual:
                try:
                    skipped = str(self.service.store.get_pref("update_skipped_version", "") or "")
                except Exception:  # noqa: BLE001
                    skipped = ""
                if skipped == info.version:
                    return
            self._show_update_prompt(info)
            if manual:
                self._set_status(f"发现新版本 v{info.version},点上方横幅更新")

        self._run_async(work, done)

    def _check_updates_now(self) -> None:
        """点右下角版本号手动检查(下载过程中不重复触发)。"""
        if self._update_state == "downloading":
            return
        self._maybe_check_updates(force=True, manual=True)

    # -- 更新提示(全屏) ---------------------------------------------------
    def _show_update_prompt(self, info) -> None:
        """整窗覆盖的更新提示:图标 / 版本 / 更新内容 / 自动安装 / 两个按钮。"""
        self._hide_update_prompt()
        self._update_info = info
        self._update_state = "available"
        self._update_progress_text = ""

        overlay = tk.Frame(self.root, bg=BG)
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()
        self._update_overlay = overlay
        overlay.bind("<Escape>", lambda _event: self._dismiss_update(remember=False))

        content = tk.Frame(overlay, bg=BG)
        content.place(relx=0.5, rely=0.5, anchor="center")

        icon = self._load_update_icon()
        if icon is not None:
            self._update_icon = icon
            tk.Label(content, image=icon, bg=BG).pack(pady=(0, px(12)))

        tk.Label(content, text="FavFocus 有新版本", bg=BG, fg=FG, font=font(18, "bold")).pack()
        self._update_version_label = tk.Label(
            content,
            text=f"当前 v{APP_VERSION}  ·  最新 v{info.version}",
            bg=BG,
            fg=FG_DIM,
            font=font(10),
        )
        self._update_version_label.pack(pady=(px(6), px(14)))

        tk.Frame(content, bg=BORDER, height=1).pack(fill="x")
        tk.Label(
            content, text="更新内容", bg=BG, fg=FG, font=font(10, "bold"), anchor="w"
        ).pack(fill="x", pady=(px(12), px(6)))

        notes_box = tk.Frame(content, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        notes_box.pack(fill="both", expand=True)
        self._update_notes = tk.Text(
            notes_box,
            height=7,
            width=52,
            bg=CARD_BG,
            fg=FG,
            relief="flat",
            font=font(9),
            wrap="word",
            cursor="arrow",
            padx=px(10),
            pady=px(8),
        )
        self._update_notes.insert("1.0", self._format_release_notes(info))
        self._update_notes.configure(state="disabled")
        scrollbar = tk.Scrollbar(notes_box, orient="vertical", command=self._update_notes.yview)
        self._update_notes.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._update_notes.pack(side="left", fill="both", expand=True)

        self._auto_install_var = tk.BooleanVar(
            value=bool(self.service.store.get_pref("update_auto_install", False))
        )
        tk.Checkbutton(
            content,
            text="启动 FavFocus 时自动下载并安装更新",
            variable=self._auto_install_var,
            command=self._save_auto_install_pref,
            bg=BG,
            fg=FG_DIM,
            activebackground=BG,
            activeforeground=FG,
            selectcolor=CARD_BG,
            font=font(9),
            bd=0,
            highlightthickness=0,
        ).pack(anchor="w", pady=(px(12), px(2)))

        self._update_progress = tk.Label(content, text="", bg=BG, fg=ACCENT, font=font(9))
        self._update_progress.pack(pady=(px(4), 0))

        buttons = tk.Frame(content, bg=BG)
        buttons.pack(pady=(px(12), 0))
        self._update_secondary = tk.Button(
            buttons,
            text="稍后提醒我",
            command=lambda: self._dismiss_update(remember=False),
            bg=BG_ALT,
            fg=FG,
            relief="flat",
            activebackground=BORDER,
            activeforeground=FG,
            font=font(10),
            padx=px(22),
            pady=px(6),
        )
        self._update_secondary.pack(side="left", padx=px(6))
        self._update_primary = tk.Button(
            buttons,
            text="立即更新",
            command=self._on_update_primary,
            bg=ACCENT,
            fg="#ffffff",
            relief="flat",
            activebackground=ACCENT_DARK,
            activeforeground="#ffffff",
            font=font(10),
            padx=px(28),
            pady=px(6),
        )
        self._update_primary.pack(side="left", padx=px(6))

        self._update_skip_link = tk.Label(
            content, text="不再提示这个版本", bg=BG, fg=FG_DIM, font=font(8), cursor="hand2"
        )
        self._update_skip_link.pack(pady=(px(10), 0))
        self._update_skip_link.bind("<Button-1>", lambda _event: self._dismiss_update(remember=True))

        # 勾了"自动安装"就自动开始下载(用户自己选的)
        if self._auto_install_var.get() and update_mod.can_one_click():
            self.root.after(800, self._auto_start_update_download)

    def _format_release_notes(self, info) -> str:
        """把 GitHub Release 正文整理成短列表。"""
        raw = (getattr(info, "notes", "") or "").strip()
        lines: list[str] = []
        for line in raw.splitlines():
            clean = line.strip().lstrip("-*·• ").replace("**", "").strip()
            if not clean or clean.lower().startswith("favfocus v"):
                continue
            if clean.startswith(("安装包", "绿色版", "覆盖安装", "数据只存", "登录方式")):
                continue
            lines.append(f"· {clean}")
        if not lines:
            lines = ["· 包含功能改进与问题修复,建议更新到最新版本。"]
        return "\n".join(lines[:8])

    def _load_update_icon(self):
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "icon-128.png"
        try:
            image = tk.PhotoImage(file=str(icon_path))
        except tk.TclError:
            return None
        target = max(1, px(72))
        if image.width() > target:
            image = image.subsample(max(1, round(image.width() / target)))
        return image

    def _save_auto_install_pref(self) -> None:
        try:
            self.service.store.set_pref(
                "update_auto_install", bool(self._auto_install_var.get())
            )
        except Exception:  # noqa: BLE001 - 记不住也不影响本次更新
            pass

    def _auto_start_update_download(self) -> None:
        if self._update_state != "available" or self._update_info is None:
            return
        self._start_update_download()

    def _dismiss_update(self, *, remember: bool) -> None:
        if remember and self._update_info is not None:
            try:
                self.service.store.set_pref("update_skipped_version", self._update_info.version)
            except Exception:  # noqa: BLE001 - 记不住也不影响使用
                pass
        self._hide_update_prompt()

    def _hide_update_prompt(self) -> None:
        overlay = getattr(self, "_update_overlay", None)
        self._update_overlay = None
        self._update_progress = None
        if overlay is not None:
            try:
                overlay.destroy()
            except tk.TclError:
                pass
        self._update_state = "idle"

    # 兼容旧名字(内部/冒烟脚本可能还在用)
    def _hide_update_banner(self) -> None:
        self._hide_update_prompt()

    def _on_update_primary(self) -> None:
        info = self._update_info
        if info is None:
            return
        if self._update_state == "downloaded":
            self._install_update()
            return
        if not update_mod.can_one_click():
            # 绿色版用户:直接把发布页打开,让他自己下
            webbrowser.open(info.page_url)
            self._dismiss_update(remember=False)
            return
        self._start_update_download()

    def _start_update_download(self) -> None:
        info = self._update_info
        if info is None or self._update_state == "downloading":
            return
        self._update_state = "downloading"
        self._update_primary.configure(state="disabled", text="下载中…")
        if self._update_progress is not None:
            self._update_progress.configure(text="正在下载更新 · 0%")

        def work():
            def progress(percent: int) -> None:
                self._update_progress_text = f"正在下载更新 · {percent}%"

            return update_mod.download_installer(info, progress=progress)

        def done(path, error):
            if error is not None or path is None:
                self._update_state = "available"
                if self._update_progress is not None:
                    self._update_progress.configure(text=f"下载失败:{error}")
                if self._update_primary is not None:
                    self._update_primary.configure(state="normal", text="重试")
                return
            self._update_installer = path
            self._update_state = "downloaded"
            if self._update_progress is not None:
                self._update_progress.configure(text="下载完成 · 安装会关闭并重新打开应用")
            if self._update_primary is not None:
                self._update_primary.configure(state="normal", text="安装并重启")

        self._run_async(work, done)

    def _install_update(self) -> None:
        path = self._update_installer
        if path is None:
            return
        if not update_mod.launch_installer(path):
            self._set_status("启动安装程序失败,请到 Releases 手动下载。", ERROR)
            return
        self._quit_for_update()

    def _quit_for_update(self) -> None:
        """更新时直接退出:安装器会在退出后覆盖安装并重新打开。"""
        self._closing = True
        try:
            if self._poll_job is not None:
                self.root.after_cancel(self._poll_job)
                self._poll_job = None
        except tk.TclError:
            pass
        self.root.destroy()

    # -- 到点提醒(只在应用内,不弹系统窗口) ------------------------------
    def _check_schedule_reminder(self, force: bool = False) -> None:
        """每隔一小会儿看一次:有没有到点还没看的日程。"""
        now = time.time()
        last = getattr(self, "_reminder_checked_at", 0.0)
        if not force and now - last < 15:
            return
        self._reminder_checked_at = now
        if self.playing_key:
            return  # 正在播放,别打扰
        try:
            item = self.service.schedule_due(now)
        except Exception:  # noqa: BLE001
            return
        if item is None:
            self._hide_reminder()
            return
        if getattr(self, "_reminder_item", None) is not None and self._reminder_item.id == item.id:
            return
        self._show_reminder(item)

    def _show_reminder(self, item) -> None:
        self._reminder_item = item
        title = item.title if len(item.title) <= 22 else item.title[:21] + "…"
        self.reminder_text.configure(text=f"{item.time} 到了 · 该看《{title}》了")
        self.reminder.pack(fill="x", padx=px(14), pady=(0, px(6)), before=self.content)

    def _hide_reminder(self) -> None:
        self._reminder_item = None
        try:
            self.reminder.pack_forget()
        except tk.TclError:
            pass

    def _play_reminder_item(self) -> None:
        item = getattr(self, "_reminder_item", None)
        if item is None:
            return
        self._play_schedule_item(item)

    def _dismiss_reminder(self) -> None:
        item = getattr(self, "_reminder_item", None)
        if item is not None:
            try:
                self.service.dismiss_schedule_reminder(item)
            except Exception:  # noqa: BLE001 - 记不住也不该报错
                pass
        self._hide_reminder()

    # -- 手动分类 ---------------------------------------------------------
    def _on_card_context(self, video, x_root: int, y_root: int) -> None:
        """右键卡片:把这条视频固定到某个分类,或恢复自动分类。"""
        menu = tk.Menu(self.root, tearoff=0)
        overrides = self.service.topic_overrides()
        current = overrides.get(video.key) or topics.classify(video)
        menu.add_command(
            label=f"《{video.title[:18]}》" + ("…" if len(video.title) > 18 else ""),
            state="disabled",
        )
        menu.add_separator()
        for topic in topics.all_topics():
            mark = "✓ " if topic.id == current else "　 "
            menu.add_command(
                label=mark + topic.name,
                command=lambda topic_id=topic.id: self._set_topic(video, topic_id),
            )
        menu.add_separator()
        menu.add_command(
            label="恢复自动分类",
            state="normal" if video.key in overrides else "disabled",
            command=lambda: self._set_topic(video, None),
        )
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()
            self.root.after(200, menu.destroy)

    def _set_topic(self, video, topic_id: str | None) -> None:
        try:
            self.service.set_topic_override(video, topic_id)
        except ValueError as error:
            self._set_status(str(error), WARN)
            return
        if topic_id is None:
            self._set_status(f"已恢复自动分类:{video.title}")
        else:
            self._set_status(
                f"已把《{video.title[:16]}》归到「{topics.name_of(topic_id)}」"
            )
        self._render()

    def _on_group_toggle(self, collapsed: set[str]) -> None:
        """记住哪些分类被收起了,下次打开保持。"""
        try:
            self.service.store.set_pref("collapsed_groups", sorted(collapsed))
        except Exception:  # noqa: BLE001 - 记不住也不该影响界面
            pass

    def _on_toggle_group(self) -> None:
        self.service.store.set_pref("auto_group", bool(self.group_var.get()))
        self._render()
        state = "已开启自动分类" if self.group_var.get() else "已关闭自动分类"
        self._set_status(state)

    def change_login(self) -> None:
        self._prompt_login()

    # -- 收尾 -------------------------------------------------------------
    def _remember_user(self, uname: str | None, mid: int | None = None) -> None:
        """记住用户名与 mid:退出小结要靠名字打招呼,mid 用来读取收藏夹。"""
        if mid:
            self.mid = int(mid)
            try:
                self.service.set_default_mid(mid)
            except Exception:  # noqa: BLE001 - 记不住也不该影响登录
                pass
        name = (uname or "").strip()
        if not name:
            return
        self.username = name
        try:
            self.service.store.set_pref("last_uname", name)
        except Exception:  # noqa: BLE001 - 记住名字失败不该影响登录
            pass

    def _build_farewell(self) -> FarewellSummary | None:
        videos = list(self.videos)
        if not videos and not self.username:
            return None  # 从没用过,没什么好总结的
        digest = self.service.digest(videos) if videos else {}
        return FarewellSummary(
            username=self.username,
            session_watch_seconds=self.session_watch_seconds,
            session_plays=self.session_plays,
            total_watch_seconds=self.service.total_watch_seconds(),
            digest=digest,
            progress_count=len(self.service.store.progress),
        )

    def _on_close(self) -> None:
        if self._closing:
            return  # 已经在关了(小结窗口开着时又点了关闭)
        self._closing = True
        splash = getattr(self, "splash", None)
        if splash is not None:
            splash.stop()
        for job in (self._poll_job, self._auto_job):
            if job is None:
                continue
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._poll_job = None
        self._auto_job = None
        try:
            # 存"逻辑尺寸"(除以 DPI 缩放),换显示器时窗口大小才不会翻倍。
            # 全屏状态下不能记当前尺寸,否则下次退出全屏会得到一个满屏窗口。
            scale = max(theme.SCALE, 1.0)
            if getattr(self, "fullscreen", False):
                remembered = getattr(self, "_windowed_geometry", None)
                if remembered:
                    self.service.store.set_pref("width", int(remembered[0] / scale))
                    self.service.store.set_pref("height", int(remembered[1] / scale))
            else:
                self.service.store.set_pref("width", int(self.root.winfo_width() / scale))
                self.service.store.set_pref("height", int(self.root.winfo_height() / scale))
        except Exception:  # noqa: BLE001 - 关闭时不该再抛
            pass

        summary = self._build_farewell()
        if summary is None:
            self.root.destroy()
            return
        try:
            FarewellDialog(self.root, summary, on_close=self.root.destroy)
        except Exception:  # noqa: BLE001 - 小结失败不能拦住退出
            self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="favfocus.ui_tk", description="FavFocus 桌面界面")
    parser.add_argument("--fid", type=int, help="直接打开这个收藏夹")
    parser.add_argument("--mid", type=int, default=0, help="收藏夹所属用户 mid(默认用本机登录时记住的)")
    args = parser.parse_args(argv)

    # 必须在建 Tk 之前声明 DPI 感知,否则 Windows 会把窗口位图放大,字会发虚
    enable_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError as error:
        print(f"无法创建窗口:{error}")
        print("请确认已安装 Python 的 tkinter 组件。")
        return 2

    FocusApp(root, fid_override=args.fid, mid=args.mid)
    try:
        root.mainloop()
    except Exception:  # noqa: BLE001 - 记下来再退出,别静默消失
        import traceback

        try:
            from pathlib import Path as _Path

            from .store import default_home

            log = _Path(default_home()) / "ui-errors.log"
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] MAINLOOP\n")
                handle.write(traceback.format_exc())
        except Exception:  # noqa: BLE001
            pass
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
