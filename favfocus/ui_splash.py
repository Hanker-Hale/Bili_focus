"""启动页:用户提供的眼睛/logo 动画。

素材是整段视频按 2 倍速抽出的 49 帧(palette PNG,1800×1800);
运行时按帧从磁盘加载,只保留当前一帧,内存占用可控。

启动页只显示这段动画,不再绘制标题、标语、状态或版本号。
素材缺失时退回一个静态灯泡图形。
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from pathlib import Path

from . import theme

TICK_MS = 40
FRAME_MS = 83           # 12fps,整段视频 2 倍速约 4 秒
DOT_COUNT = 3           # 兼容旧代码/测试的常量
FRAME_DIR = Path(__file__).resolve().parent.parent / "assets" / "splash-frames"
SPLASH_BLUE = "#81DAFC"  # 和视频底色一致
FRAME_SIZE = 1800


class SplashOverlay(tk.Frame):
    """覆盖整个窗口的启动画面,只播放视频动画。"""

    def __init__(self, master: tk.Misc, message: str = "正在准备…"):
        super().__init__(master, bg=SPLASH_BLUE)
        self.canvas = tk.Canvas(self, bg=SPLASH_BLUE, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._message = message
        self._shown_at = time.monotonic()
        self._job = None
        self._guard_job = None
        self._size = (0, 0)

        self._frames = self._load_paths()
        self._frame_index = 0
        self._frame_factor = 1
        self._frame_image_id = None
        self._frame_pos = (0, 0)
        self._current_image = None
        self._play_started = time.monotonic()
        self._using_frames = bool(self._frames)
        self._min_visible_ms = max(700, len(self._frames) * FRAME_MS + 250) if self._frames else 700

        self.canvas.bind("<Configure>", self._on_resize)
        self.start()

    # -- 素材 -------------------------------------------------------------
    def _load_paths(self) -> list[Path]:
        if not FRAME_DIR.is_dir():
            return []
        return sorted(FRAME_DIR.glob("frame-*.png"))

    def _load_image(self, index: int, factor: int) -> tk.PhotoImage | None:
        try:
            image = tk.PhotoImage(file=str(self._frames[index]))
        except (tk.TclError, IndexError):
            return None
        if factor > 1:
            image = image.subsample(factor)
        return image

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        if self._job is None:
            self._job = self.after(FRAME_MS if self._using_frames else TICK_MS, self._tick)
        if self._guard_job is None:
            self._guard_job = self.after(300, self._guard)

    def remaining_ms(self, min_visible_ms: int | None = None) -> int:
        """还要至少显示多久(毫秒),保证整段动画能放完。"""
        if min_visible_ms is None:
            min_visible_ms = self._min_visible_ms
        elapsed = (time.monotonic() - self._shown_at) * 1000
        return max(0, int(min_visible_ms - elapsed))

    def stop(self) -> None:
        for attr in ("_job", "_guard_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, attr, None)

    def hide(self) -> None:
        self.stop()
        try:
            self.place_forget()
        except tk.TclError:
            pass

    def show(self, message: str | None = None) -> None:
        if message is not None:
            self.set_status(message)
        self._shown_at = time.monotonic()
        self._play_started = time.monotonic()
        try:
            self.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.lift()
        except tk.TclError:
            pass
        self.start()

    def set_status(self, message: str) -> None:
        # 画面里不显示文字,状态只保存在内部
        self._message = message

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
        self.canvas.delete("logo")
        self.canvas.delete("splash-ray")
        self._frame_image_id = None
        width, height = self._size
        center_x, center_y = width // 2, height // 2

        if not self._frames:
            self._using_frames = False
            size = min(width, height) // 2
            self._draw_fallback(center_x, center_y, size)
            return

        # 大窗口直接显示 1800px 原图,小窗口才降一倍
        factor = 2 if min(width, height) < 800 else 1
        self._frame_factor = factor
        self._using_frames = True
        self._frame_index = min(self._frame_index, len(self._frames) - 1)
        image = self._load_image(self._frame_index, factor)
        if image is None:
            self._using_frames = False
            self._draw_fallback(center_x, center_y, min(width, height) // 2)
            return
        self._current_image = image
        self._frame_pos = (center_x, center_y)
        self._frame_image_id = self.canvas.create_image(
            center_x,
            center_y,
            image=image,
            tags=("logo", "splash-frame"),
        )

    def _draw_fallback(self, center_x: int, center_y: int, size: int) -> None:
        """素材缺失时的静态灯泡,不参与动画。"""
        radius = size // 2
        thickness = max(2, theme.px(4))
        self.canvas.create_oval(
            center_x - radius, center_y - radius,
            center_x + radius, center_y + radius,
            fill=SPLASH_BLUE, outline="",
        )
        self.canvas.create_oval(
            center_x - radius, center_y - radius,
            center_x + radius, center_y + radius,
            outline=theme.ACCENT, width=thickness,
        )

    # -- 动效 -------------------------------------------------------------
    def _tick(self) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        if self._using_frames:
            # 按真实时间对齐:即使单帧渲染慢,也保证整段视频 2 倍速放完
            elapsed_ms = (time.monotonic() - self._play_started) * 1000
            target = min(len(self._frames) - 1, int(elapsed_ms / FRAME_MS))
            if target != self._frame_index:
                image = self._load_image(target, self._frame_factor)
                if image is not None and self._frame_image_id is not None:
                    self._frame_index = target
                    self._current_image = image
                    self.canvas.itemconfigure(self._frame_image_id, image=image)
                    bob = math.sin(time.monotonic() * 2.0) * theme.px(1)
                    self.canvas.coords(
                        self._frame_image_id,
                        self._frame_pos[0],
                        self._frame_pos[1] + bob,
                    )
            if self._frame_index < len(self._frames) - 1:
                self._job = self.after(FRAME_MS, self._tick)
            else:
                # 视频放完:停在最后一帧,直到应用准备好
                self._job = self.after(250, self._hold)
        else:
            self._job = self.after(TICK_MS, self._tick)

    def _guard(self) -> None:
        self._guard_job = None
        if not self.winfo_exists() or not self.visible:
            return
        self.lift()
        self._guard_job = self.after(800, self._guard)

    def _hold(self) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        if self.winfo_ismapped():
            self.lift()
        self._job = self.after(250, self._hold)


def draw_bulb_mark(
    canvas: tk.Canvas, center_x: int, center_y: int, size: int, tags: tuple = ()
) -> list[int]:
    """在任意 Canvas 上画一个静态的小灯泡(没有底片)。"""
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
