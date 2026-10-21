"""退出时的小结卡片。

不是 messagebox:它要有点仪式感,但又不打扰人——一块米白卡片,
一句称呼开头,一串数字,一条消化率进度条,一个「再见」。

数据全部来自 `FarewellSummary`,界面这一层不做任何计算。
"""

from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass, field

from pathlib import Path

from . import theme
from .models import format_seconds
from .theme import ACCENT, BG, BORDER, CARD_BG, FG, FG_DIM, WATCHED_FG, font, px


@dataclass
class FarewellSummary:
    """退出小结需要的一切。"""

    username: str = ""
    session_watch_seconds: float = 0.0
    session_plays: int = 0
    total_watch_seconds: float = 0.0
    digest: dict = field(default_factory=dict)
    progress_count: int = 0

    @property
    def greeting(self) -> str:
        name = (self.username or "").strip()
        return f"亲爱的 {name}" if name else "亲爱的朋友"


def _center_over(window: tk.Misc, parent: tk.Misc, width: int, height: int) -> None:
    parent.update_idletasks()
    x = parent.winfo_rootx() + (max(parent.winfo_width(), 1) - width) // 2
    y = parent.winfo_rooty() + (max(parent.winfo_height(), 1) - height) // 2
    window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")




def breathe_close(windows, on_done, duration_ms: int = 1200, step_ms: int = 20) -> None:
    """把窗口透明度平滑地降到 0(1 → 0,中途不回升),再执行收尾。

    用余弦缓动:开始和结束慢、中间稍快,看起来是慢慢淡出,
    不会出现忽明忽暗的闪烁。
    """
    frames = max(1, duration_ms // step_ms)

    def alpha_at(progress: float) -> float:
        eased = 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, progress)))
        return max(0.0, 1.0 - eased)

    def apply(value: float) -> None:
        for window in windows:
            try:
                window.attributes("-alpha", value)
            except tk.TclError:
                pass

    def step(index: int = 0) -> None:
        try:
            apply(alpha_at(min(1.0, index / frames)))
        except tk.TclError:
            on_done()
            return
        if index >= frames:
            apply(0.0)
            on_done()
            return
        try:
            windows[0].after(step_ms, step, index + 1)
        except tk.TclError:
            on_done()

    apply(1.0)
    step(0)

class FarewellDialog(tk.Toplevel):
    """今日小结。点「再见」(或回车/ESC)才真正退出程序。"""

    def __init__(
        self,
        parent: tk.Misc,
        summary: FarewellSummary,
        on_close,
        auto_close_ms: int | None = None,
    ):
        super().__init__(parent)
        self.summary = summary
        self._on_close = on_close
        self._finished = False
        self._auto_close_job = None
        self._auto_close_seconds = round(auto_close_ms / 1000) if auto_close_ms else None

        self.title("FavFocus — 今日小结")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._finish)
        self.bind("<Escape>", lambda _event: self._finish())
        self.bind("<Return>", lambda _event: self._finish())

        self._build()
        self.update_idletasks()
        _center_over(
            self,
            parent,
            self.winfo_reqwidth() + px(4),
            self.winfo_reqheight() + px(4),
        )
        if auto_close_ms:
            self._auto_close_job = self.after(auto_close_ms, self._finish)

    # -- 图标 -------------------------------------------------------------
    def _load_icon(self):
        """加载新图标(assets/icon-128.png),按当前 DPI 取整数倍缩小。"""
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "icon-128.png"
        try:
            image = tk.PhotoImage(file=str(icon_path))
        except tk.TclError:
            return None
        target = max(1, px(64))
        if image.width() > target:
            factor = max(1, round(image.width() / target))
            image = image.subsample(factor)
        self._icon_photo = image  # 防止被回收
        return image

    # -- 构建 -------------------------------------------------------------
    def _build(self) -> None:
        body = tk.Frame(self, bg=BG)
        body.pack(padx=px(30), pady=px(24))

        self._icon_photo = self._load_icon()
        if self._icon_photo is not None:
            tk.Label(body, image=self._icon_photo, bg=BG).pack()
        else:
            # 图标文件缺失时退回纯文字,不让小结打不开
            tk.Label(body, text="FavFocus", bg=BG, fg=ACCENT, font=font(14, "bold")).pack()

        tk.Label(
            body,
            text=self.summary.greeting,
            bg=BG,
            fg=FG,
            font=font(15, "bold"),
        ).pack(pady=(px(12), px(2)))

        session = self.summary.session_watch_seconds
        if session >= 60:
            subtitle = f"今天在收藏夹里待了 {format_seconds(session)}"
        elif session > 0:
            subtitle = f"今天看了 {int(session)} 秒,也算数"
        else:
            subtitle = "今天只是来看看,没有播放"
        tk.Label(body, text=subtitle, bg=BG, fg=FG_DIM, font=font(10)).pack()

        self._build_digest(body)
        self._build_stats(body)

        tk.Button(
            body,
            text="再见",
            command=self._finish,
            bg=ACCENT,
            fg="#ffffff",
            relief="flat",
            activebackground=theme.ACCENT_DARK,
            activeforeground="#ffffff",
            padx=px(28),
            pady=px(5),
            font=font(10),
        ).pack(pady=(px(18), 0))
        if self._auto_close_seconds:
            tk.Label(
                body,
                text=f"{self._auto_close_seconds} 秒后自动关闭",
                bg=BG,
                fg=WATCHED_FG,
                font=font(9),
            ).pack(pady=(px(8), 0))

    def _build_digest(self, parent: tk.Frame) -> None:
        digest = self.summary.digest
        if not digest or not digest.get("total"):
            return
        card = tk.Frame(parent, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(px(16), 0), ipadx=px(14), ipady=px(10))

        title_row = tk.Frame(card, bg=CARD_BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text="已看完", bg=CARD_BG, fg=FG_DIM, font=font(9)).pack(side="left")
        percent = digest.get("rate_percent", 0.0)
        tk.Label(
            title_row,
            text=f"{percent:.0f}%",
            bg=CARD_BG,
            fg=ACCENT,
            font=font(14, "bold"),
        ).pack(side="right")

        numbers = tk.Frame(card, bg=CARD_BG)
        numbers.pack(fill="x", pady=(px(2), px(8)))
        tk.Label(
            numbers,
            text=f"{digest.get('finished', 0)} / {digest.get('total', 0)} 条",
            bg=CARD_BG,
            fg=FG,
            font=font(11, "bold"),
        ).pack(side="left")

        bar = tk.Canvas(
            card,
            height=max(3, px(6)),
            bg=CARD_BG,
            highlightthickness=0,
        )
        bar.pack(fill="x")
        width = px(300)
        bar.configure(width=width)
        bar.create_rectangle(0, 0, width, max(3, px(6)), fill=theme.PLACEHOLDER_BG, outline="")
        filled = int(width * min(1.0, max(0.0, digest.get("rate", 0.0))))
        if filled > 0:
            bar.create_rectangle(0, 0, filled, max(3, px(6)), fill=ACCENT, outline="")

    def _build_stats(self, parent: tk.Frame) -> None:
        lines: list[str] = []
        total = self.summary.total_watch_seconds
        if total > 0:
            lines.append(f"累计观看 {format_seconds(int(total))}")
        if self.summary.session_plays:
            lines.append(f"本次播放 {self.summary.session_plays} 条")

        digest = self.summary.digest
        if digest and digest.get("total"):
            tail = f"还有 {digest.get('remaining', 0)} 条没看完"
            if digest.get("in_progress"):
                tail += f" · {digest['in_progress']} 条看了一半"
            lines.append(tail)

        if not lines:
            return
        tk.Label(
            parent,
            text=" · ".join(lines),
            bg=BG,
            fg=WATCHED_FG if not self.summary.session_plays else FG_DIM,
            font=font(9),
        ).pack(pady=(px(12), 0))

    # -- 收尾 -------------------------------------------------------------
    def _finish(self, animate: bool = True) -> None:
        if self._finished:
            return
        self._finished = True
        if self._auto_close_job is not None:
            try:
                self.after_cancel(self._auto_close_job)
            except tk.TclError:
                pass
            self._auto_close_job = None
        try:
            self.grab_release()
        except tk.TclError:
            pass

        windows = [self]
        parent = self.master
        if isinstance(parent, tk.Misc):
            windows.append(parent)

        def done() -> None:
            try:
                self.destroy()
            except tk.TclError:
                pass
            if self._on_close is not None:
                self._on_close()

        if animate:
            alpha_ok = True
            try:
                self.attributes("-alpha", 1.0)
                if isinstance(parent, tk.Misc):
                    parent.attributes("-alpha", 1.0)
            except tk.TclError:
                alpha_ok = False
            if alpha_ok:
                breathe_close(windows, done)
                return

        done()
