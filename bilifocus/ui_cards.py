"""卡片网格:虚拟化 + 单画布绘制。

## 为什么要重写成这样

最初版本是"每条视频一套 Tk 控件"(Frame + 封面 Canvas + 3 个 Label)。
实测滚动一帧要 **47ms**(56 张卡,约 280 个控件)——Tk 每次滚动都要重画整棵
内嵌窗口的控件树,开销和**卡片总数**成正比,而不是和可见数量,所以越滚越卡。
再加上一次滚轮跳 480px(视口的 30%),观感就是"又僵又卡"。

现在改成两条:

1. **一张卡片 = 一个 Canvas**,所有元素(封面、时长角标、标题、UP主、进度条、
   选中框)都画在这个 Canvas 的 item 上。控件从 280 个降到几十个。
2. **虚拟化**:只为"当前视口 ±1 行"准备画布,滚动时按行号复用。
   滚到别的视频才重画;同一行平移时几乎零成本。

滚轮改成像素级 + 90ms 缓动,不再是"一格跳半屏"。
"""

from __future__ import annotations

import base64
import math
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from . import theme
from .covers import CoverFetcher
from .models import Video, format_seconds
from .topics import Group
from .theme import (
    ACCENT,
    BADGE_BG,
    BADGE_FG,
    BG,
    BORDER,
    CARD_BG,
    CARD_HOVER,
    FG,
    FG_DIM,
    PLACEHOLDER_BG,
    WATCHED_FG,
    px,
)

TITLE_LINES = 2
WHEEL_STEP = 96          # 滚轮一格滚多少"设计像素"
SCROLL_FRAME_MS = 16     # 缓动动画帧间隔(约 60fps)
BUFFER_ROWS = 1          # 视口上下各多准备几行


def fit_lines(text: str, font, max_width: int, max_lines: int) -> list[str]:
    """把文字折成"最多 max_lines 行、每行不超过 max_width 像素"。

    画布上的文字不会自动换行,所以要自己按字体实际宽度二分折行,
    超出部分用 … 收尾。
    """
    text = " ".join((text or "").split())
    if not text or max_width <= 0:
        return []
    lines: list[str] = []
    rest = text
    while rest and len(lines) < max_lines:
        low, high = 0, len(rest)
        while low < high:
            middle = (low + high + 1) // 2
            if font.measure(rest[:middle]) <= max_width:
                low = middle
            else:
                high = middle - 1
        take = max(1, low)
        last_line = len(lines) == max_lines - 1
        if last_line and take < len(rest):
            while take > 1 and font.measure(rest[:take] + "…") > max_width:
                take -= 1
            lines.append(rest[:take] + "…")
            rest = ""
        else:
            lines.append(rest[:take])
            rest = rest[take:].lstrip()
    return lines


class CardGrid(tk.Frame):
    """可滚动的封面卡片网格(虚拟化)。"""

    def __init__(
        self,
        master: tk.Misc,
        fetcher: CoverFetcher,
        *,
        on_activate=None,
        on_select=None,
        on_group_toggle=None,
        on_context=None,
    ):
        super().__init__(master, bg=BG)
        self.fetcher = fetcher
        self.on_activate = on_activate
        self.on_select = on_select
        self.on_group_toggle = on_group_toggle
        self.on_context = on_context

        self._videos: list[Video] = []
        self._watched: set[str] = set()
        self._progress: dict[str, dict] = {}
        self._photos: dict[str, tk.PhotoImage] = {}
        self._url_keys: dict[str, set[str]] = {}
        self._wanted: dict[str, set[str]] = {}
        self._queue: "queue.Queue[tuple[str, bytes | None]]" = queue.Queue()
        self._fetching = False
        self._drain_job = None

        self._selected: str | None = None
        self._columns = 1
        self._message_label: tk.Label | None = None
        self._fonts: dict[tuple, object] = {}
        self._fit_cache: dict[tuple, list[str]] = {}

        # 虚拟化状态:只保留可见行的图形,数据在 _videos 里
        self._drawn: set[str] = set()
        self._card_state: dict[str, dict] = {}
        self._row_height = 1
        self._card_height = 1
        self._total_height = 1
        self._groups: list[Group] | None = None
        self._collapsed: set[str] = set()
        self._scroll_restore: float | None = None
        self._rows: list[dict] = []
        self._pos: dict[str, tuple[int, int]] = {}
        self._drawn_headers: set[int] = set()
        self._hover_key: str | None = None
        self._scroll_job = None
        self._scroll_target = 0.0

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._on_scrollbar)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _event: self._on_motion_leave())
        self._measure()

    # -- 尺寸与字体 -------------------------------------------------------
    def _font(self, size: int, weight: str = "normal"):
        key = (size, weight)
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        try:
            cached = tkfont.Font(root=self, family=theme.FONT_FAMILY, size=size, weight=weight)
        except Exception:  # noqa: BLE001
            cached = theme.font(size, weight)
        self._fonts[key] = cached
        return cached

    def _measure(self) -> None:
        self._cover_w = theme.CARD_WIDTH
        self._cover_h = theme.COVER_HEIGHT
        self._title_font = self._font(9, "bold")
        self._meta_font = self._font(8)
        self._title_lh = self._title_font.metrics("linespace")
        self._meta_lh = self._meta_font.metrics("linespace")
        self._pad = px(8)
        self._card_height = (
            self._cover_h
            + px(6)
            + self._title_lh * TITLE_LINES
            + px(2)
            + self._meta_lh
            + px(8)
        )
        self._row_height = self._card_height + theme.CARD_GAP
        self._col_width = self._cover_w + theme.CARD_GAP

    # -- 对外接口 ---------------------------------------------------------
    def set_videos(
        self,
        videos: list[Video],
        watched: set[str],
        progress: dict[str, dict] | None = None,
        groups: list[Group] | None = None,
        collapsed: set[str] | None = None,
    ) -> None:
        """groups 不为空时按分组排版(每组一个标题行);为空则是普通网格。

        collapsed 里是被收起的类别 id,收起后只保留标题行。
        """
        self._videos = list(videos)
        self._watched = set(watched)
        self._progress = dict(progress or {})
        self._groups = groups or None
        if collapsed is not None:
            self._collapsed = set(collapsed)
        self._rebuild()

    def set_watched(self, key: str, watched: bool) -> None:
        if watched:
            self._watched.add(key)
        else:
            self._watched.discard(key)
        self._repaint_key(key)

    def set_progress(self, key: str, info: dict | None) -> None:
        if info and (info.get("position") or info.get("finished")):
            self._progress[key] = dict(info)
        else:
            self._progress.pop(key, None)
        self._repaint_key(key)

    def set_message(self, text: str) -> None:
        self._videos = []
        self._watched = set()
        self._progress = {}
        for key in list(self._drawn):
            self.canvas.delete(self._tags(key))
        self._drawn.clear()
        self._card_state.clear()
        self._message_label = text
        self._selected = None
        self._total_height = max(px(120), self.canvas.winfo_height())
        self.canvas.delete("message")
        self.canvas.create_text(
            theme.GRID_PAD,
            theme.GRID_PAD,
            text=text,
            anchor="nw",
            fill=FG_DIM,
            font=self._font(10),
            justify="left",
            tags="message",
        )
        self._sync_scrollregion()

    def selected(self) -> Video | None:
        if self._selected is None:
            return None
        for video in self._videos:
            if video.key == self._selected:
                return video
        return None

    @property
    def count(self) -> int:
        return len(self._videos)

    @property
    def selected_key(self) -> str | None:
        return self._selected

    def select_key(self, key: str | None, *, reveal: bool = True) -> None:
        if key is None:
            previous, self._selected = self._selected, None
            self._paint_selection(previous)
            return
        if not any(video.key == key for video in self._videos):
            return
        previous, self._selected = self._selected, key
        self._paint_selection(previous)
        if reveal:
            self._reveal(key)
        if self.on_select:
            self.on_select(self.selected())

    def move(self, delta: int) -> None:
        if not self._videos:
            return
        ordered = self._order or self._videos
        keys = [video.key for video in ordered]
        try:
            index = keys.index(self._selected) if self._selected else 0
        except ValueError:
            index = 0
        index = max(0, min(len(keys) - 1, index + delta))
        self.select_key(keys[index])

    def move_vertical(self, direction: int) -> None:
        self.move(direction * max(1, self._columns))

    def destroy(self) -> None:
        self._stop_scroll()
        if self._drain_job is not None:
            try:
                self.after_cancel(self._drain_job)
            except tk.TclError:
                pass
            self._drain_job = None
        super().destroy()

    # -- 供测试/调试读取的渲染状态 ---------------------------------------
    def debug_card(self, key: str) -> dict:
        """某张卡片最后一次渲染出来的内容(测试用,不参与界面逻辑)。"""
        return dict(self._card_state.get(key) or {})

    # -- 布局 -------------------------------------------------------------
    def _visible_rows(self) -> int:
        height = max(self.canvas.winfo_height(), self._row_height)
        return max(1, math.ceil(height / self._row_height))

    def _columns_for_width(self, width: int) -> int:
        usable = max(self._cover_w, width - 2 * theme.GRID_PAD - self.scrollbar.winfo_width())
        return max(1, int(usable // self._col_width))

    def _layout_rows(self) -> None:
        """把视频排成"行"序列:标题行或卡片行。

        分组模式下每组 = 一个标题行 + 若干卡片行;普通模式只有卡片行。
        这样虚拟化可以在同一条时间轴上处理两种行。
        """
        self._rows = []
        self._pos = {}
        self._order: list[Video] = []
        y = theme.GRID_PAD
        header_h = self._title_lh + px(22)
        columns = max(1, self._columns)

        if self._groups:
            for group in self._groups:
                if not group.videos:
                    continue
                collapsed = group.id in self._collapsed
                mid = y + header_h / 2
                chevron_x0 = theme.GRID_PAD + px(10)
                chevron_w = px(18)
                chevron_h = px(18)
                self._rows.append(
                    {
                        "kind": "header",
                        "y": y,
                        "height": header_h,
                        "label": group.name,
                        "count": len(group.videos),
                        "group_id": group.id,
                        "collapsed": collapsed,
                        "chevron": (
                            chevron_x0,
                            mid - chevron_h / 2,
                            chevron_x0 + chevron_w,
                            mid + chevron_h / 2,
                        ),
                    }
                )
                y += header_h
                if collapsed:
                    continue  # 收起:只留标题行
                for offset in range(0, len(group.videos), columns):
                    chunk = group.videos[offset:offset + columns]
                    self._rows.append({"kind": "cards", "y": y, "height": self._row_height,
                                       "videos": chunk})
                    y += self._row_height
        else:
            for offset in range(0, len(self._videos), columns):
                chunk = self._videos[offset:offset + columns]
                self._rows.append({"kind": "cards", "y": y, "height": self._row_height,
                                   "videos": chunk})
                y += self._row_height

        for row in self._rows:
            if row["kind"] != "cards":
                continue
            for column, video in enumerate(row["videos"]):
                x = theme.GRID_PAD + column * self._col_width + theme.CARD_GAP // 2
                self._pos[video.key] = (x, row["y"] + theme.CARD_GAP // 2)
                self._order.append(video)
        self._total_height = y + theme.GRID_PAD

    def _row_at(self, content_y: float) -> dict | None:
        for row in self._rows:
            if row["y"] <= content_y < row["y"] + row["height"]:
                return row
        return None

    def _position(self, index: int) -> tuple[int, int]:
        return self._pos.get(self._videos[index].key, (theme.GRID_PAD, theme.GRID_PAD))

    def _on_canvas_configure(self, _event=None) -> None:
        columns = self._columns_for_width(self.canvas.winfo_width())
        if columns != self._columns:
            self._columns = columns
            self._rebuild()
        else:
            self._sync_scrollregion()
            self._update_window()

    def _rebuild(self) -> None:
        if self._message_label is not None:
            self._message_label = None
        for key in list(self._drawn):
            self.canvas.delete(self._tags(key))
        self._drawn.clear()
        self._card_state.clear()
        self.canvas.delete("message")
        self.canvas.delete("group-header")

        if not self._videos:
            self._selected = None
            self._rows = []
            self._pos = {}
            self._order = []
            self._total_height = max(1, self.canvas.winfo_height())
            self._sync_scrollregion()
            return
        columns = self._columns_for_width(
            self.canvas.winfo_width() or (self._cover_w + theme.CARD_GAP)
        )
        self._columns = columns
        self._layout_rows()
        self._sync_scrollregion()
        self._prime_covers()
        self._update_window(force=True)
        self._fetch_covers()

    def _sync_scrollregion(self) -> None:
        self.canvas.configure(
            scrollregion=(0, 0, self.canvas.winfo_width(), max(1, self._total_height))
        )

    # -- 滚轮:像素级 + 缓动 ----------------------------------------------
    def _on_scrollbar(self, *args) -> None:
        self.canvas.yview(*args)
        self._stop_scroll()
        self._update_window()

    def _scroll_to(self, offset: float) -> None:
        total = max(1, self._total_height)
        view = max(1, self.canvas.winfo_height())
        offset = max(0.0, min(max(0, total - view), float(offset)))
        self.canvas.yview_moveto(offset / total)

    def _on_wheel(self, event) -> str:
        if not self._videos:
            return "break"
        notches = -1 if event.delta > 0 else 1
        target = self.canvas.canvasy(0) + notches * px(WHEEL_STEP)
        self._animate_scroll(target)
        return "break"

    def _animate_scroll(self, target: float) -> None:
        total = max(1, self._total_height)
        view = max(1, self.canvas.winfo_height())
        target = max(0.0, min(max(0, total - view), target))
        self._stop_scroll()
        if abs(self.canvas.canvasy(0) - target) < 1:
            self._update_window()
            return
        self._scroll_target = target
        self._scroll_job = self.after(SCROLL_FRAME_MS, self._scroll_tick)

    def _scroll_tick(self) -> None:
        self._scroll_job = None
        remaining = self._scroll_target - self.canvas.canvasy(0)
        if abs(remaining) <= 2:
            self._scroll_to(self._scroll_target)
            self._update_window()
            return
        self._scroll_to(self.canvas.canvasy(0) + remaining * 0.45)
        self._update_window()
        self._scroll_job = self.after(SCROLL_FRAME_MS, self._scroll_tick)

    def _stop_scroll(self) -> None:
        if self._scroll_job is not None:
            try:
                self.after_cancel(self._scroll_job)
            except tk.TclError:
                pass
            self._scroll_job = None

    # -- 虚拟窗口:只保留可见行的图形 ------------------------------------
    def _tags(self, key: str) -> str:
        return f"card:{key}"

    def _update_window(self, *, force: bool = False) -> None:
        if not self._videos or not self._rows:
            return
        view = max(1, self.canvas.winfo_height())
        top = self.canvas.canvasy(0) - BUFFER_ROWS * self._row_height
        bottom = self.canvas.canvasy(0) + view + BUFFER_ROWS * self._row_height
        needed: set[str] = set()
        needed_headers: set[int] = set()

        for row_index, row in enumerate(self._rows):
            if row["y"] + row["height"] < top or row["y"] > bottom:
                continue
            if row["kind"] == "header":
                needed_headers.add(row_index)
                if force or row_index not in self._drawn_headers:
                    self._draw_header(row_index, row)
            else:
                for video in row["videos"]:
                    needed.add(video.key)
                    if force or video.key not in self._drawn:
                        self._draw_card(video, self._videos.index(video))

        for key in [key for key in self._drawn if key not in needed]:
            self.canvas.delete(self._tags(key))
            self._drawn.discard(key)
        for row_index in [index for index in self._drawn_headers if index not in needed_headers]:
            self.canvas.delete(f"header:{row_index}")
            self._drawn_headers.discard(row_index)

    def _draw_header(self, row_index: int, row: dict) -> None:
        """分组标题:[强调竖线] [箭头] 类别名 条数。点箭头收起/展开。

        所有元素都以"行的垂直中线"为基准定位:文字用 anchor="w"(垂直居中),
        箭头图形用中心点推算。这样楷体的行高差异不会造成错位。
        """
        tag = f"header:{row_index}"
        self.canvas.delete(tag)
        x = theme.GRID_PAD
        mid = row["y"] + row["height"] / 2

        # 强调竖线:与文字高度一致,垂直居中
        bar_h = self._title_lh
        self.canvas.create_rectangle(
            x, mid - bar_h / 2, x + px(4), mid + bar_h / 2,
            fill=ACCENT, outline="", tags=(tag, "group-header"),
        )

        chevron = row["chevron"]
        cx = (chevron[0] + chevron[2]) / 2
        size = px(5)
        if row["collapsed"]:
            points = (cx - size * 0.7, mid - size, cx - size * 0.7, mid + size, cx + size, mid)
        else:
            points = (cx - size, mid - size * 0.7, cx + size, mid - size * 0.7, cx, mid + size)
        self.canvas.create_polygon(
            *points, fill=FG_DIM, outline="", tags=(tag, "group-header", "group-chevron")
        )

        name_x = chevron[2] + px(8)
        self.canvas.create_text(
            name_x, mid, text=row["label"], anchor="w", fill=FG,
            font=self._title_font, tags=(tag, "group-header"),
        )
        count_text = f"{row['count']} 条" + ("(已收起)" if row["collapsed"] else "")
        self.canvas.create_text(
            name_x + self._title_font.measure(row["label"]) + px(10), mid,
            text=count_text, anchor="w", fill=FG_DIM,
            font=self._meta_font, tags=(tag, "group-header"),
        )
        self._drawn_headers.add(row_index)

    def _fit(self, text: str, font, max_width: int, max_lines: int, kind: str) -> list[str]:
        """带缓存的折行:同一段文字反复重画时不必每次都测量。"""
        cache_key = (kind, text, max_width, max_lines)
        cached = self._fit_cache.get(cache_key)
        if cached is None:
            cached = fit_lines(text, font, max_width, max_lines)
            if len(self._fit_cache) > 800:
                self._fit_cache.clear()
            self._fit_cache[cache_key] = cached
        return cached

    # -- 绘制一张卡片(直接画在主画布上,不用子窗口)----------------------
    def _draw_card(self, video: Video, index: int) -> None:
        canvas = self.canvas
        tag = self._tags(video.key)
        canvas.delete(tag)
        x, y = self._pos.get(video.key, (theme.GRID_PAD, theme.GRID_PAD))
        watched = video.key in self._watched
        progress = self._progress.get(video.key) or {}
        inset = px(2)
        x0, y0 = x + inset, y + inset
        x1, y1 = x + self._cover_w - inset, y + self._card_height - inset

        outline = self._outline_color(video.key)
        canvas.create_rectangle(
            x0, y0, x1, y1, fill=CARD_BG, outline=outline, width=px(2), tags=(tag, "card-bg")
        )

        photo = self._photos.get(video.cover_url(self._cover_w, self._cover_h))
        cover_top = y + inset
        if photo is not None:
            canvas.create_image(x + inset, cover_top, anchor="nw", image=photo, tags=(tag, "cover"))
        else:
            canvas.create_rectangle(
                x + inset, cover_top, x + self._cover_w - inset, cover_top + self._cover_h,
                fill=PLACEHOLDER_BG, outline="", tags=(tag, "cover-placeholder"),
            )
            canvas.create_text(
                x + self._cover_w // 2, cover_top + self._cover_h // 2,
                text="无封面" if not video.cover_url() else "加载中…",
                fill=FG_DIM, font=self._meta_font, tags=(tag, "placeholder-text"),
            )

        ratio = float(progress.get("ratio") or 0)
        if ratio > 0 or progress.get("finished"):
            bar_h = max(2, px(4))
            bar_bottom = cover_top + self._cover_h
            canvas.create_rectangle(
                x + inset, bar_bottom - bar_h, x + self._cover_w - inset, bar_bottom,
                fill=BORDER, outline="", tags=(tag, "progress-track"),
            )
            fill_w = int((self._cover_w - inset * 2) * min(1.0, max(0.0, ratio)))
            if fill_w > 0:
                canvas.create_rectangle(
                    x + inset, bar_bottom - bar_h, x + inset + fill_w, bar_bottom,
                    fill=ACCENT, outline="", tags=(tag, "progress-fill"),
                )

        badge = video.duration_text
        text_w = self._meta_font.measure(badge)
        bx2 = x + self._cover_w - inset - px(6)
        by2 = cover_top + self._cover_h - px(8)
        bx1 = bx2 - text_w - px(8)
        by1 = by2 - self._meta_lh - px(2)
        canvas.create_rectangle(bx1, by1, bx2, by2, fill=BADGE_BG, outline="", tags=(tag, "badge"))
        canvas.create_text(
            bx1 + px(4), (by1 + by2) // 2, text=badge, anchor="w", fill=BADGE_FG,
            font=self._meta_font, tags=(tag, "badge-text"),
        )

        title_top = y + self._cover_h + px(6)
        lines = self._fit(
            video.title, self._title_font, self._cover_w - self._pad * 2, TITLE_LINES, "title"
        )
        for line_index, line in enumerate(lines):
            canvas.create_text(
                x + self._pad, title_top + line_index * self._title_lh,
                text=line, anchor="nw", fill=WATCHED_FG if watched else FG,
                font=self._title_font, tags=(tag, "title"),
            )

        meta_top = title_top + self._title_lh * TITLE_LINES + px(2)
        meta_line = self._fit(
            self._meta_text(video, progress),
            self._meta_font,
            self._cover_w - self._pad * 2,
            1,
            "meta",
        )
        canvas.create_text(
            x + self._pad, meta_top, text=meta_line[0] if meta_line else "", anchor="nw",
            fill=FG_DIM, font=self._meta_font, tags=(tag, "meta"),
        )

        self._drawn.add(video.key)
        self._card_state[video.key] = {
            "title": "\n".join(lines),
            "meta": meta_line[0] if meta_line else "",
            "ratio": ratio,
            "finished": bool(progress.get("finished")),
            "watched": watched,
            "has_cover": photo is not None,
        }

    def _meta_text(self, video: Video, progress: dict) -> str:
        if progress.get("finished"):
            return f"{video.upper} · 已看完"
        position = int(progress.get("position") or 0)
        if position > 0:
            return f"{video.upper} · 看到 {format_seconds(position)}"
        return video.upper

    def _repaint_key(self, key: str) -> None:
        if key not in self._drawn:
            return
        video = next((item for item in self._videos if item.key == key), None)
        if video is None:
            return
        self._draw_card(video, self._videos.index(video))

    def _index_at(self, x: int, y: int) -> int | None:
        content_y = y + int(self.canvas.canvasy(0))
        row = self._row_at(content_y)
        if row is None or row["kind"] != "cards":
            return None
        column = int((x - theme.GRID_PAD) // max(1, self._col_width))
        if column < 0 or column >= len(row["videos"]):
            return None
        video = row["videos"][column]
        return next((i for i, item in enumerate(self._videos) if item.key == video.key), None)

    def video_at(self, x: int, y: int) -> Video | None:
        """屏幕/画布坐标下是哪张卡片(没有就是 None)。"""
        index = self._index_at(x, y)
        return None if index is None else self._videos[index]

    def _on_right_click(self, event) -> None:
        video = self.video_at(event.x, event.y)
        if video is None:
            return
        self.select_key(video.key, reveal=False)
        if self.on_context:
            self.on_context(video, event.x_root, event.y_root)

    def _on_click(self, event) -> None:
        content_y = event.y + int(self.canvas.canvasy(0))
        row = self._row_at(content_y)
        if row is not None and row["kind"] == "header":
            chevron = row.get("chevron")
            if chevron and chevron[0] <= event.x <= chevron[2] and chevron[1] <= content_y <= chevron[3]:
                self.toggle_group(row["group_id"])
            return
        index = self._index_at(event.x, event.y)
        if index is not None:
            self.select_key(self._videos[index].key, reveal=False)

    def toggle_group(self, group_id: str | None) -> None:
        """收起/展开一个分类。保持当前滚动位置,不至于跳回顶部。"""
        if not group_id:
            return
        offset = self.canvas.canvasy(0)
        if group_id in self._collapsed:
            self._collapsed.discard(group_id)
        else:
            self._collapsed.add(group_id)
        self._rebuild()
        self._scroll_to(offset)
        self._update_window()
        if self.on_group_toggle:
            self.on_group_toggle(set(self._collapsed))

    def _on_double(self, event) -> None:
        index = self._index_at(event.x, event.y)
        if index is None:
            return
        video = self._videos[index]
        self.select_key(video.key, reveal=False)
        if self.on_activate:
            self.on_activate(video)

    def _on_motion(self, event) -> None:
        index = self._index_at(event.x, event.y)
        hover = self._videos[index].key if index is not None else None
        if hover == getattr(self, "_hover_key", None):
            return
        previous = getattr(self, "_hover_key", None)
        self._hover_key = hover
        for key in (previous, hover):
            if not key:
                continue
            index = next((i for i, video in enumerate(self._videos) if video.key == key), None)
            slot_outline = self._outline_color(key)
            for item in self.canvas.find_withtag(self._tags(key)):
                if "card-bg" in self.canvas.gettags(item):
                    self.canvas.itemconfigure(item, outline=slot_outline)

    def _on_motion_leave(self) -> None:
        """鼠标移出网格:清掉悬停高亮。"""
        previous = getattr(self, "_hover_key", None)
        if not previous:
            return
        self._hover_key = None
        color = self._outline_color(previous)
        for item in self.canvas.find_withtag(self._tags(previous)):
            if "card-bg" in self.canvas.gettags(item):
                self.canvas.itemconfigure(item, outline=color)

    def _outline_color(self, key: str) -> str:
        if key == self._selected:
            return ACCENT
        if key == getattr(self, "_hover_key", None):
            return CARD_HOVER
        return CARD_BG

    def _paint_selection(self, previous_key: str | None = None) -> None:
        for key in {previous_key, self._selected, getattr(self, "_hover_key", None)}:
            if not key:
                continue
            color = self._outline_color(key)
            for item in self.canvas.find_withtag(self._tags(key)):
                if "card-bg" in self.canvas.gettags(item):
                    self.canvas.itemconfigure(item, outline=color)

    def _reveal(self, key: str) -> None:
        position = self._pos.get(key)
        if position is None:
            return
        _x, card_y = position
        view = max(1, self.canvas.winfo_height())
        target = card_y - view // 2 + self._card_height // 2
        self._stop_scroll()
        self._scroll_to(max(0, target))
        self._update_window()

    # -- 封面加载 ---------------------------------------------------------
    def _prime_covers(self) -> None:
        """把内存和磁盘里已有的封面先放进 _photos(不联网)。"""
        self._url_keys = {}
        self._missing: list[str] = []
        for video in self._videos:
            url = video.cover_url(self._cover_w, self._cover_h)
            if not url:
                continue
            self._url_keys.setdefault(url, set()).add(video.key)
            if url in self._photos:
                continue
            data = self.fetcher.get_cached(url)
            if data is not None:
                try:
                    self._photos[url] = tk.PhotoImage(data=base64.b64encode(data))
                except tk.TclError:
                    pass
            elif url not in self._missing:
                self._missing.append(url)

    def _fetch_covers(self) -> None:
        """后台补齐还没拿到的封面。"""
        to_fetch = list(getattr(self, "_missing", []))
        self._wanted = {url: self._url_keys.get(url, set()) for url in to_fetch}
        if not to_fetch:
            return
        if not self._fetching:
            self._fetching = True
            threading.Thread(target=self._fetch_worker, args=(to_fetch,), daemon=True).start()
        self._schedule_drain()

    def _fetch_worker(self, urls: list[str]) -> None:
        try:
            for url in urls:
                self._queue.put((url, self.fetcher.fetch(url)))
        finally:
            self._fetching = False

    def _schedule_drain(self) -> None:
        if self._drain_job is None:
            self._drain_job = self.after(40, self._drain)

    def _drain(self) -> None:
        self._drain_job = None
        try:
            while True:
                url, data = self._queue.get_nowait()
                if data is not None:
                    self._apply_cover(url, data)
        except queue.Empty:
            pass
        if self._fetching or not self._queue.empty():
            self._schedule_drain()

    def _apply_cover(self, url: str, data: bytes) -> None:
        if url not in self._photos:
            try:
                self._photos[url] = tk.PhotoImage(data=base64.b64encode(data))
            except tk.TclError:
                return
        self._apply_photo(url)

    def _apply_photo(self, url: str) -> None:
        for key in self._url_keys.get(url, ()):
            if key in self._drawn:
                self._repaint_key(key)
