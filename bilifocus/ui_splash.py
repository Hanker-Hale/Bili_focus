"""启动页:极简动画,启动完成后让位给卡片网格。

设计上不引入任何图片资源——全部用 Canvas 画,所以：

- 在任何 DPI 下都是清晰的矢量图形(和之前的位图缩放问题说再见);
- 不依赖 Pillow,系统 Python 也能跑;
- 换配色只改 theme.py。

三个动效,都很轻,加起来只占几毫秒:

1. **取景框上下轻浮**:logo 整体以 ±3px 正弦浮动,像在"呼吸"。
2. **三个点依次脉动**:像输入中的省略号,暗示"正在处理"。
3. **状态文案**:随启动阶段变化(验证登录 → 读收藏夹 → 拉取内容)。
"""

from __future__ import annotations

import math
import time
import tkinter as tk

from . import theme
from .theme import ACCENT, BG, BORDER, FG, FG_DIM, font

TICK_MS = 33           # 约 30fps,足够顺滑又几乎不占 CPU
DOT_COUNT = 3


def blend(color_a: str, color_b: str, ratio: float) -> str:
    """在两个 #rrggbb 之间取中间色(Tk 没有透明度,用插值假装)。"""
    ratio = max(0.0, min(1.0, ratio))
    left = [int(color_a[i : i + 2], 16) for i in (1, 3, 5)]
    right = [int(color_b[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(left[i] + (right[i] - left[i]) * ratio) for i in range(3)]
    return "#" + "".join(f"{value:02x}" for value in mixed)


class SplashOverlay(tk.Frame):
    """覆盖在内容区上的启动画面。"""

    def __init__(self, master: tk.Misc, message: str = "正在准备…"):
        super().__init__(master, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._message = message
        self._phase = 0.0
        self._shown_at = time.monotonic()
        self._job = None
        self._size = (0, 0)

        # 静态元素在 _layout() 里创建一次,动效只改它们的坐标
        self._logo_ids: list[int] = []
        self._title_id = self.canvas.create_text(0, 0, text="", font=font(20, "bold"), fill=FG)
        self._tagline_id = self.canvas.create_text(0, 0, text="", font=font(10), fill=FG_DIM)
        self._status_id = self.canvas.create_text(0, 0, text=message, font=font(9), fill=FG_DIM)
        self._dot_ids: list[int] = []
        self._footer_id = self.canvas.create_text(0, 0, text="", font=font(8), fill=FG_DIM)

        self.canvas.bind("<Configure>", self._on_resize)
        self.start()

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        if self._job is None:
            self._job = self.after(TICK_MS, self._tick)

    def remaining_ms(self, min_visible_ms: int = 600) -> int:
        """还要至少显示多久(毫秒)。用来避免启动太快时闪一下就没了。"""
        elapsed = (time.monotonic() - self._shown_at) * 1000
        return max(0, int(min_visible_ms - elapsed))

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def hide(self) -> None:
        """让位给真实内容(调用方负责 place_forget 或 destroy)。"""
        self.stop()
        try:
            self.place_forget()
        except tk.TclError:
            pass

    def show(self, message: str | None = None) -> None:
        if message is not None:
            self.set_status(message)
        self._shown_at = time.monotonic()
        try:
            self.place(relx=0, rely=0, relwidth=1, relheight=1)
        except tk.TclError:
            pass
        self.start()

    def set_status(self, message: str) -> None:
        self._message = message
        self.canvas.itemconfigure(self._status_id, text=message)

    @property
    def message(self) -> str:
        return self._message

    @property
    def visible(self) -> bool:
        return bool(self.winfo_manager())

    # -- 布局 -------------------------------------------------------------
    def _on_resize(self, _event=None) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if (width, height) == self._size or width <= 1 or height <= 1:
            return
        self._size = (width, height)
        self._layout()

    def _layout(self) -> None:
        # 尺寸变化会重新布局:必须先把上一套图形删掉,
        # 否则会和旧的叠在一起(启动时窗口从小变全屏就会触发)
        self.canvas.delete("logo")
        self.canvas.delete("splash-dots")
        self._last_bob = 0.0
        width, height = self._size
        center_x = width // 2
        # 视觉重心略高于几何中心,看起来更稳
        center_y = int(height * 0.42)

        size = theme.px(76)
        self._logo_ids = self._draw_logo(center_x, center_y, size)

        self.canvas.coords(self._title_id, center_x, center_y + size // 2 + theme.px(34))
        self.canvas.itemconfigure(self._title_id, text="BiliFocus")

        self.canvas.coords(self._tagline_id, center_x, center_y + size // 2 + theme.px(66))
        self.canvas.itemconfigure(self._tagline_id, text="只看收藏夹,不被推荐流带走")

        dot_y = center_y + size // 2 + theme.px(104)
        gap = theme.px(16)
        self._dot_centers = [
            (center_x + int((index - (DOT_COUNT - 1) / 2) * gap), dot_y)
            for index in range(DOT_COUNT)
        ]
        self._dot_ids = [
            self.canvas.create_oval(0, 0, 0, 0, fill=BORDER, outline="", tags=("splash-dots",))
            for _ in range(DOT_COUNT)
        ]

        self.canvas.coords(self._status_id, center_x, dot_y + theme.px(34))
        self.canvas.coords(self._footer_id, center_x, height - theme.px(28))
        self.canvas.itemconfigure(self._footer_id, text="v2.0.0 · 只看不发,打开即走")

        self._paint(0.0)

    def _draw_logo(self, center_x: int, center_y: int, size: int) -> list[int]:
        return draw_logo_mark(self.canvas, center_x, center_y, size, tags=("logo",))

    # -- 动效 -------------------------------------------------------------
    def _tick(self) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        self._phase += 0.12
        self._paint(self._phase)
        self._job = self.after(TICK_MS, self._tick)

    def _paint(self, phase: float) -> None:
        if not self._logo_ids:
            return
        # 1) logo 整体轻浮:正弦 ±3px
        bob = math.sin(phase) * theme.px(3)
        for item in self._logo_ids:
            self.canvas.move(item, 0, bob - getattr(self, "_last_bob", 0.0))
        self._last_bob = bob

        # 2) 三个点依次脉动:当前那个放大并变蓝,其余渐隐
        radius = theme.px(5)
        for index, (center_x, center_y) in enumerate(getattr(self, "_dot_centers", [])):
            wave = math.sin(phase * 1.9 - index * 0.9)   # 依次起跳
            level = max(0.0, wave)                        # 只有波峰时"亮"
            dot_radius = radius + level * theme.px(2)
            color = blend(BORDER, ACCENT, level)
            self.canvas.coords(
                self._dot_ids[index],
                center_x - dot_radius, center_y - dot_radius,
                center_x + dot_radius, center_y + dot_radius,
            )
            self.canvas.itemconfigure(self._dot_ids[index], fill=color)


def draw_logo_mark(
    canvas: tk.Canvas, center_x: int, center_y: int, size: int, tags: tuple = ()
) -> list[int]:
    """在任意 Canvas 上画"取景框 + 播放三角"的品牌标记。

    启动页和退出小结都用它,保证两处的图形语言完全一致。
    """
    ids: list[int] = []
    half = size // 2
    thickness = max(2, theme.px(4))
    arm = int(size * 0.34)

    # 四个角:每个角两条线段拼成 L 形
    corners = (
        (center_x - half, center_y - half, 1, 1),
        (center_x + half, center_y - half, -1, 1),
        (center_x - half, center_y + half, 1, -1),
        (center_x + half, center_y + half, -1, -1),
    )
    for origin_x, origin_y, dir_x, dir_y in corners:
        ids.append(
            canvas.create_line(
                origin_x, origin_y, origin_x + dir_x * arm, origin_y,
                fill=ACCENT, width=thickness, capstyle="round", tags=tags,
            )
        )
        ids.append(
            canvas.create_line(
                origin_x, origin_y, origin_x, origin_y + dir_y * arm,
                fill=ACCENT, width=thickness, capstyle="round", tags=tags,
            )
        )

    # 播放三角(实心,视觉重心往右偏一点才像"播放")
    play = theme.px(26)
    offset = theme.px(3)
    ids.append(
        canvas.create_polygon(
            center_x - play // 2 + offset, center_y - play // 2,
            center_x - play // 2 + offset, center_y + play // 2,
            center_x + play // 2 + offset, center_y,
            fill=FG, outline="", tags=tags,
        )
    )
    return ids
