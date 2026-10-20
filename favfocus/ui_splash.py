"""启动页:灯泡 + 光线的手绘动画。

和手绘图标同一套语言:

- 浅蓝天蓝底(badge)+ 米白灯泡(玻璃罩、灯颈、灯座、灯丝);
- **光线那几条短杠从灯泡周围依次亮起、拉长、变白**,像在"充能",
  用这个代替普通转圈/点状加载器;
- 整个图形做 ±2px 的呼吸浮动,不抢眼。

动画只用 tkinter Canvas,不依赖图片和 Pillow,任何 DPI 都清晰。
"""

from __future__ import annotations

import math
import time
import tkinter as tk

from . import __version__ as APP_VERSION
from . import theme
from .theme import BG, FG, FG_DIM, font

TICK_MS = 40
DOT_COUNT = 3          # 兼容旧代码/测试的常量;新动画不再用圆点
RAY_COUNT = 9          # 灯泡周围的光线数量
BADGE_BLUE = "#64C0FF"  # 和新图标底色一致
CREAM = "#FFFAF0"
RAY_DIM = "#A9DDFF"
RAY_BRIGHT = CREAM


def blend(color_a: str, color_b: str, ratio: float) -> str:
    """按比例混合两个 #RRGGBB 颜色。"""
    ratio = max(0.0, min(1.0, ratio))
    a = [int(color_a[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(x + (y - x) * ratio) for x, y in zip(a, b)]
    return "#{:02X}{:02X}{:02X}".format(*mixed)


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

        # 静态元素在 _layout() 里创建一次,动效只改它们的坐标/颜色
        self._logo_ids: list[int] = []      # 底片 + 灯泡本体(一起做呼吸浮动)
        self._ray_ids: list[int] = []       # 光线短杠(加载动画主角)
        self._ray_angles: list[float] = []
        self._bulb_center = (0, 0)
        self._bulb_radius = 0
        self._title_id = self.canvas.create_text(0, 0, text="", font=font(20, "bold"), fill=FG)
        self._tagline_id = self.canvas.create_text(0, 0, text="", font=font(10), fill=FG_DIM)
        self._status_id = self.canvas.create_text(0, 0, text=message, font=font(9), fill=FG_DIM)
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
        # 尺寸变化会重新布局:必须先把上一套图形删掉,否则会和旧的叠在一起
        self.canvas.delete("logo")
        self.canvas.delete("splash-ray")
        self.canvas.delete("splash-dots")
        self._logo_ids = []
        self._ray_ids = []
        self._ray_angles = []

        width, height = self._size
        center_x = width // 2
        # 全部按窗口高度取百分比,窗口矮时整体缩小,文字不会叠在一起
        center_y = int(height * 0.38)
        size = min(theme.px(150), int(height * 0.34))

        self._draw_bulb_scene(center_x, center_y, size)

        title_y = center_y + size // 2 + theme.px(26)
        self.canvas.coords(self._title_id, center_x, title_y)
        self.canvas.itemconfigure(self._title_id, text="FavFocus")

        tagline_y = title_y + theme.px(30)
        self.canvas.coords(self._tagline_id, center_x, tagline_y)
        self.canvas.itemconfigure(self._tagline_id, text="只看收藏夹,不被推荐流带走")

        self.canvas.coords(self._status_id, center_x, tagline_y + theme.px(34))
        self.canvas.coords(self._footer_id, center_x, height - theme.px(28))
        self.canvas.itemconfigure(self._footer_id, text=f"v{APP_VERSION} · 只看不发,打开即走")

        self._paint(0.0)

    def _draw_bulb_scene(self, center_x: int, center_y: int, size: int) -> None:
        """画天蓝底片 + 米白灯泡 + 一圈光线短杠。"""
        radius = size // 2
        left, top = center_x - radius, center_y - radius
        right, bottom = center_x + radius, center_y + radius

        # 圆角方底(用平滑多边形近似圆角矩形)
        corner = int(size * 0.22)
        points = [
            left + corner, top, right - corner, top, right, top + corner,
            right, bottom - corner, right - corner, bottom, left + corner, bottom,
            left, bottom - corner, left, top + corner,
        ]
        self._logo_ids.append(
            self.canvas.create_polygon(
                points, smooth=True, splinesteps=18, fill=BADGE_BLUE, outline="",
                tags=("logo",),
            )
        )

        thickness = max(2, theme.px(3))
        glass_radius = int(size * 0.20)
        glass_center = (center_x, int(center_y - size * 0.02))

        # 玻璃罩
        self._logo_ids.append(
            self.canvas.create_oval(
                glass_center[0] - glass_radius, glass_center[1] - glass_radius,
                glass_center[0] + glass_radius, glass_center[1] + glass_radius,
                outline=CREAM, width=thickness, tags=("logo",),
            )
        )
        # 灯颈(两条斜线)
        neck_top = glass_center[1] + glass_radius - theme.px(2)
        neck_bottom = int(center_y + size * 0.17)
        self._logo_ids.append(
            self.canvas.create_line(
                glass_center[0] - glass_radius * 0.42, neck_top,
                glass_center[0] - glass_radius * 0.26, neck_bottom,
                fill=CREAM, width=thickness, capstyle="round", tags=("logo",),
            )
        )
        self._logo_ids.append(
            self.canvas.create_line(
                glass_center[0] + glass_radius * 0.42, neck_top,
                glass_center[0] + glass_radius * 0.26, neck_bottom,
                fill=CREAM, width=thickness, capstyle="round", tags=("logo",),
            )
        )
        # 灯座:三条逐渐变短的横线
        base_widths = (0.44, 0.34, 0.24)
        for index, ratio in enumerate(base_widths):
            y = neck_bottom + int(size * 0.035) * (index + 1)
            half = int(glass_radius * ratio)
            self._logo_ids.append(
                self.canvas.create_line(
                    center_x - half, y, center_x + half, y,
                    fill=CREAM, width=thickness, capstyle="round", tags=("logo",),
                )
            )
        # 灯丝:灯罩里的一个小波浪
        filament_y = glass_center[1] + int(glass_radius * 0.30)
        filament = [
            glass_center[0] - int(glass_radius * 0.34), filament_y,
            glass_center[0] - int(glass_radius * 0.12), filament_y - int(glass_radius * 0.34),
            glass_center[0] + int(glass_radius * 0.12), filament_y,
            glass_center[0] + int(glass_radius * 0.34), filament_y - int(glass_radius * 0.34),
        ]
        self._logo_ids.append(
            self.canvas.create_line(
                *filament, fill=CREAM, width=thickness, capstyle="round",
                joinstyle="round", smooth=True, tags=("logo",),
            )
        )

        # 光线:绕灯泡一圈的短杠,加载时依次点亮
        self._bulb_center = glass_center
        self._bulb_radius = glass_radius
        ray_inner = glass_radius + int(size * 0.075)
        ray_outer = glass_radius + int(size * 0.155)
        for index in range(RAY_COUNT):
            angle = math.radians(-150 + index * (300 / (RAY_COUNT - 1)))
            x1 = glass_center[0] + math.cos(angle) * ray_inner
            y1 = glass_center[1] + math.sin(angle) * ray_inner
            x2 = glass_center[0] + math.cos(angle) * ray_outer
            y2 = glass_center[1] + math.sin(angle) * ray_outer
            self._ray_ids.append(
                self.canvas.create_line(
                    x1, y1, x2, y2, fill=RAY_DIM, width=max(2, theme.px(3)),
                    capstyle="round", tags=("splash-ray",),
                )
            )
            self._ray_angles.append(angle)

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

        # 1) 底片 + 灯泡整体轻微上下呼吸(±2px)
        bob = math.sin(phase) * theme.px(2)
        for item in self._logo_ids:
            self.canvas.move(item, 0, bob - getattr(self, "_last_bob", 0.0))
        self._last_bob = bob

        # 2) 光线依次亮起:从暗蓝到米白、长度和粗细一起变化
        center_x, center_y = self._bulb_center
        inner = self._bulb_radius + theme.px(18)
        outer = self._bulb_radius + theme.px(34)
        for index, item in enumerate(self._ray_ids):
            wave = math.sin(phase * 2.2 - index * 0.75)
            level = (wave + 1) / 2            # 0..1
            level = level ** 1.6              # 让"亮"的那一下更集中
            angle = self._ray_angles[index]
            length = inner + (outer - inner) * (0.55 + 0.45 * level)
            x1 = center_x + math.cos(angle) * (inner - theme.px(2))
            y1 = center_y + math.sin(angle) * (inner - theme.px(2))
            x2 = center_x + math.cos(angle) * length
            y2 = center_y + math.sin(angle) * length
            self.canvas.coords(item, x1, y1 + bob, x2, y2 + bob)
            self.canvas.itemconfigure(
                item,
                fill=blend(RAY_DIM, RAY_BRIGHT, level),
                width=max(2, int(theme.px(3) * (0.8 + 0.6 * level))),
            )


def draw_bulb_mark(
    canvas: tk.Canvas, center_x: int, center_y: int, size: int, tags: tuple = ()
) -> list[int]:
    """在任意 Canvas 上画一个静态的小灯泡(没有底片,给需要品牌图形的地方用)。"""
    ids: list[int] = []
    thickness = max(2, theme.px(3))
    glass_radius = int(size * 0.24)
    glass_center = (center_x, int(center_y - size * 0.06))
    ids.append(
        canvas.create_oval(
            glass_center[0] - glass_radius, glass_center[1] - glass_radius,
            glass_center[0] + glass_radius, glass_center[1] + glass_radius,
            outline=theme.ACCENT, width=thickness, tags=tags,
        )
    )
    neck_bottom = int(center_y + size * 0.20)
    for direction in (-1, 1):
        ids.append(
            canvas.create_line(
                glass_center[0] + direction * glass_radius * 0.42,
                glass_center[1] + glass_radius - theme.px(2),
                glass_center[0] + direction * glass_radius * 0.26, neck_bottom,
                fill=theme.ACCENT, width=thickness, capstyle="round", tags=tags,
            )
        )
    for index, ratio in enumerate((0.44, 0.34, 0.24)):
        y = neck_bottom + int(size * 0.045) * (index + 1)
        half = int(glass_radius * ratio)
        ids.append(
            canvas.create_line(
                center_x - half, y, center_x + half, y,
                fill=theme.ACCENT, width=thickness, capstyle="round", tags=tags,
            )
        )
    return ids
