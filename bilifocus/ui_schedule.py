"""日程面板:什么时候看哪条视频。

一条日程 = 时间 + 一条收藏视频(或文字计划)+ 重复规则。

界面按"时间轴 + 封面卡片"来做:
- 左边是时间轴(时间、竖线、状态点),右边是封面 + 标题 + 标签;
- "下一项"用强调色描边,已完成的整体变淡并打勾;
- 没绑定视频的纯文字计划用占位封面。

所有数据都通过 FavoritesService 读写,这里不碰存储。
"""

from __future__ import annotations

import base64
import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import schedule as schedule_mod
from .covers import CoverFetcher
from .models import format_seconds
from .service import FavoritesService
from .theme import (
    ACCENT,
    BG,
    BG_ALT,
    BORDER,
    CARD_BG,
    ERROR,
    FG,
    FG_DIM,
    WATCHED_FG,
    font,
    px,
)

WEEKDAY_SHORT = ("一", "二", "三", "四", "五", "六", "日")

# 封面尺寸(设计像素,实际用 px() 换算)
COVER_W_DESIGN = 116
COVER_H_DESIGN = 66
ROW_PAD_Y = 5


def cover_size() -> tuple[int, int]:
    """日程行里封面的实际像素尺寸(截图脚本生成示例封面时也用这个)。"""
    return px(COVER_W_DESIGN), px(COVER_H_DESIGN)


def _center_over(window: tk.Misc, parent: tk.Misc, width: int, height: int) -> None:
    parent.update_idletasks()
    x = parent.winfo_rootx() + (max(parent.winfo_width(), 1) - width) // 2
    y = parent.winfo_rooty() + (max(parent.winfo_height(), 1) - height) // 3
    window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")


def _short(text: str, limit: int = 34) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class VideoPicker(tk.Toplevel):
    """从当前收藏夹里挑一条视频。"""

    WIDTH = 560
    HEIGHT = 520

    def __init__(self, parent: tk.Misc, videos: list, on_pick):
        super().__init__(parent)
        self._videos = list(videos)
        self._on_pick = on_pick

        self.title("选一条收藏视频")
        self.configure(bg=BG)
        self.transient(parent)
        self.resizable(False, True)
        self.bind("<Escape>", lambda _event: self.destroy())
        _center_over(self, parent, px(self.WIDTH), px(self.HEIGHT))

        tk.Label(self, text="这次要看哪条?", bg=BG, fg=FG, font=font(12, "bold")).pack(
            anchor="w", padx=px(18), pady=(px(14), px(4))
        )
        self.search = tk.Entry(
            self, bg=BG_ALT, fg=FG, insertbackground=FG, relief="flat", font=font(10)
        )
        self.search.pack(fill="x", padx=px(18), ipady=px(4))
        self.search.bind("<KeyRelease>", lambda _event: self._filter())

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=px(18), pady=(px(8), px(12)))
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw", width=px(self.WIDTH) - px(46)
        )
        self.inner.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(sequence, self._on_wheel)
        self._filter()
        self.search.focus_set()

    def _on_wheel(self, event) -> None:
        if not self.winfo_exists():
            return
        step = -1 if getattr(event, "delta", 0) > 0 else 1
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        self.canvas.yview_scroll(step, "units")

    def _filter(self) -> None:
        for child in self.inner.winfo_children():
            child.destroy()
        keyword = self.search.get().strip().lower()
        shown = [
            video
            for video in self._videos
            if not keyword
            or keyword in (video.title or "").lower()
            or keyword in (video.upper or "").lower()
        ]
        if not shown:
            tk.Label(
                self.inner, text="没有匹配的视频", bg=BG, fg=FG_DIM, font=font(10), anchor="w"
            ).pack(fill="x", pady=px(10))
            return
        for video in shown:
            row = tk.Frame(
                self.inner, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER
            )
            row.pack(fill="x", pady=px(2))
            tk.Label(row, text="▶", bg=CARD_BG, fg=ACCENT, font=font(9), padx=px(6)).pack(
                side="left"
            )
            tk.Label(
                row, text=_short(video.title, 40), bg=CARD_BG, fg=FG, font=font(10), anchor="w"
            ).pack(side="left", fill="x", expand=True, pady=px(6))
            tk.Label(
                row, text=video.duration_text, bg=CARD_BG, fg=FG_DIM, font=font(8), padx=px(8)
            ).pack(side="right")
            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>", lambda _event, video=video: self._pick(video))
            row.bind("<Enter>", lambda _event, row=row: row.configure(bg=BG_ALT))
            row.bind("<Leave>", lambda _event, row=row: row.configure(bg=CARD_BG))

    def _pick(self, video) -> None:
        self._on_pick(video)
        self.destroy()


class ScheduleDialog(tk.Toplevel):
    """今日日程:时间轴 + 封面卡片。"""

    WIDTH = 760

    def __init__(
        self,
        parent: tk.Misc,
        service: FavoritesService,
        videos: list | None = None,
        on_play=None,
        on_change=None,
        preselect_video=None,
    ):
        super().__init__(parent)
        self.service = service
        self.videos = list(videos or [])
        self._on_play = on_play
        self._on_change = on_change
        self._editing_id: str | None = None
        self._picked_video = None
        self._weekday_vars = [tk.BooleanVar(value=False) for _ in range(7)]
        self._items: list = []
        self._next_id: str | None = None

        # 封面:跟卡片网格共用同一份缓存目录,但用日程专用的尺寸
        self.fetcher = CoverFetcher(service.store.home / "cache" / "covers")
        self._photos: dict[str, tk.PhotoImage] = {}
        self._failed: set[str] = set()
        self._cover_urls: dict[str, str] = {}
        self._queue: "queue.Queue[tuple[str, bytes | None]]" = queue.Queue()
        self._fetching = False
        self._drain_job = None
        self._tooltip = None
        self._tooltip_job = None

        self.title("日程")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.bind("<Escape>", lambda _event: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        _center_over(self, parent, px(self.WIDTH), px(520))

        self._build()
        self._sync_repeat()
        self._refresh()
        self._resize()
        if preselect_video is not None:
            self._show_picked(preselect_video)
            self.time_entry.focus_set()

    # -- 构建 -------------------------------------------------------------
    def _build(self) -> None:
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=px(22), pady=(px(18), px(2)))
        tk.Label(head, text="今日日程", bg=BG, fg=FG, font=font(16, "bold")).pack(side="left")
        self.summary = tk.Label(head, text="", bg=BG, fg=FG_DIM, font=font(9))
        self.summary.pack(side="left", padx=(px(10), 0))
        self.date_label = tk.Label(head, text="", bg=BG, fg=FG_DIM, font=font(9))
        self.date_label.pack(side="right")
        self.week_label = tk.Label(head, text="", bg=BG, fg=ACCENT, font=font(9, "bold"))
        self.week_label.pack(side="right", padx=(0, px(14)))

        tk.Label(
            self,
            text="按时间排;到点会在主界面提示,点封面就能开始看",
            bg=BG,
            fg=FG_DIM,
            font=font(8),
        ).pack(anchor="w", padx=px(22), pady=(0, px(8)))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="x", padx=px(22))
        self.list_canvas = tk.Canvas(body, bg=BG, highlightthickness=0, height=px(220))
        self.list_canvas.pack(side="left", fill="x", expand=True)
        scrollbar = tk.Scrollbar(body, orient="vertical", command=self.list_canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.list_canvas.configure(yscrollcommand=scrollbar.set)
        self.list_inner = tk.Frame(self.list_canvas, bg=BG)
        self._list_window = self.list_canvas.create_window(
            (0, 0), window=self.list_inner, anchor="nw"
        )
        self.list_inner.bind(
            "<Configure>",
            lambda _event: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")),
        )
        self.list_canvas.bind(
            "<Configure>",
            lambda event: self.list_canvas.itemconfigure(self._list_window, width=event.width),
        )

        # 本周记录
        self.week_strip = tk.Frame(self, bg=BG)
        self.week_strip.pack(fill="x", padx=px(22), pady=(px(10), px(2)))

        # 添加 / 编辑表单(做成一块独立卡片)
        form_card = tk.Frame(self, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        form_card.pack(fill="x", padx=px(22), pady=(px(8), px(0)))
        form = tk.Frame(form_card, bg=CARD_BG)
        form.pack(fill="x", padx=px(12), pady=(px(10), px(4)))

        self.form_title = tk.Label(form, text="添加一条", bg=CARD_BG, fg=FG, font=font(9, "bold"))
        self.form_title.pack(side="left", padx=(0, px(10)))

        self.time_entry = tk.Entry(
            form, width=6, bg=BG, fg=FG, insertbackground=FG, relief="flat", font=font(11)
        )
        self.time_entry.pack(side="left", padx=(0, px(8)), ipady=px(4))
        self.time_entry.insert(0, self._suggest_time())

        self._flat_button(form, "选收藏视频", self._open_picker).pack(side="left", padx=(0, px(8)))

        self.repeat_box = ttk.Combobox(
            form,
            values=[schedule_mod.REPEAT_LABELS[key] for key in schedule_mod.REPEAT_CHOICES],
            state="readonly",
            width=7,
            font=font(9),
        )
        self.repeat_box.current(0)
        self.repeat_box.pack(side="left", padx=(0, px(8)))
        self.repeat_box.bind("<<ComboboxSelected>>", lambda _event: self._sync_repeat())

        self.submit_btn = tk.Button(
            form,
            text="添加",
            command=self._submit,
            bg=ACCENT,
            fg="#ffffff",
            relief="flat",
            activebackground="#0A6A8B",
            activeforeground="#ffffff",
            font=font(9, "bold"),
            padx=px(16),
            pady=px(3),
        )
        self.submit_btn.pack(side="left")
        self.cancel_edit_btn = self._flat_button(form, "取消编辑", self._cancel_edit)

        detail = tk.Frame(form_card, bg=CARD_BG)
        detail.pack(fill="x", padx=px(12), pady=(0, px(8)))
        self.picked_label = tk.Label(
            detail,
            text="还没选视频(也可以只写计划)",
            bg=CARD_BG,
            fg=FG_DIM,
            font=font(8),
            anchor="w",
        )
        self.picked_label.pack(side="left")
        self.weekday_row = tk.Frame(detail, bg=CARD_BG)

        self.error = tk.Label(self, text="", bg=BG, fg=ERROR, font=font(9))
        self.error.pack(pady=(px(6), px(10)))
        self.time_entry.bind("<Return>", lambda _event: self._submit())

    def _flat_button(self, parent, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG,
            fg=FG,
            relief="flat",
            activebackground=BORDER,
            activeforeground=FG,
            font=font(9),
            padx=px(12),
            pady=px(3),
        )

    def _suggest_time(self) -> str:
        import time as _time

        return f"{(_time.localtime().tm_hour + 1) % 24:02d}:00"

    # -- 封面 -------------------------------------------------------------
    def _video_for(self, item):
        key = getattr(item, "bvid", "")
        if not key:
            return None
        for video in self.videos:
            if video.bvid == key:
                return video
        return None

    def _prime_covers(self) -> None:
        self._cover_urls = {}
        missing: list[str] = []
        width, height = cover_size()
        for item in self._items:
            video = self._video_for(item)
            if video is None:
                continue
            url = video.cover_url(width, height)
            if not url:
                continue
            self._cover_urls[item.id] = url
            if url in self._photos or url in missing:
                continue
            data = self.fetcher.get_cached(url)
            if data:
                try:
                    self._photos[url] = tk.PhotoImage(data=base64.b64encode(data))
                except tk.TclError:
                    missing.append(url)
            else:
                missing.append(url)
        if missing and not self._fetching:
            self._fetching = True
            threading.Thread(target=self._fetch_worker, args=(missing,), daemon=True).start()
            self._drain_job = self.after(80, self._drain)

    def _fetch_worker(self, urls: list[str]) -> None:
        try:
            for url in urls:
                try:
                    data = self.fetcher.fetch(url)
                except Exception:  # noqa: BLE001 - 网络问题就当没封面
                    data = None
                self._queue.put((url, data))
        finally:
            self._fetching = False

    def _drain(self) -> None:
        self._drain_job = None
        changed = False
        try:
            while True:
                url, data = self._queue.get_nowait()
                if data is None:
                    self._failed.add(url)
                    changed = True
                elif url not in self._photos:
                    try:
                        self._photos[url] = tk.PhotoImage(data=base64.b64encode(data))
                        changed = True
                    except tk.TclError:
                        self._failed.add(url)
                        changed = True
        except queue.Empty:
            pass
        if changed:
            self._refresh()
        if self._fetching or not self._queue.empty():
            self._drain_job = self.after(100, self._drain)

    def _cover_for(self, item) -> tk.PhotoImage | None:
        url = self._cover_urls.get(item.id)
        return self._photos.get(url) if url else None

    # -- 表单 -------------------------------------------------------------
    def _open_picker(self) -> None:
        if not self.videos:
            self.error.configure(text="还没读到收藏夹内容,先刷新一下主界面")
            return
        VideoPicker(self, self.videos, on_pick=self._show_picked)

    def _show_picked(self, video) -> None:
        self._picked_video = video
        self.picked_label.configure(text=f"已选:{_short(video.title, 34)}", fg=ACCENT)

    def _sync_repeat(self) -> None:
        is_weekly = self._repeat_choice() == schedule_mod.REPEAT_WEEKLY
        if is_weekly:
            self._build_weekdays()
            self.weekday_row.pack(side="right")
        else:
            self.weekday_row.pack_forget()

    def _build_weekdays(self) -> None:
        for child in self.weekday_row.winfo_children():
            child.destroy()
        for index, var in enumerate(self._weekday_vars):
            tk.Checkbutton(
                self.weekday_row,
                text=WEEKDAY_SHORT[index],
                variable=var,
                bg=CARD_BG,
                fg=FG_DIM,
                activebackground=CARD_BG,
                activeforeground=FG,
                selectcolor=BG,
                relief="flat",
                font=font(9),
                padx=0,
            ).pack(side="left", padx=px(1))

    def _repeat_choice(self) -> str:
        label = self.repeat_box.get()
        for key, text in schedule_mod.REPEAT_LABELS.items():
            if text == label:
                return key
        return schedule_mod.REPEAT_DAILY

    def _weekday_choice(self) -> list[int]:
        return [index for index, var in enumerate(self._weekday_vars) if var.get()]

    def _submit(self) -> None:
        try:
            if self._editing_id is None:
                self.service.add_schedule(
                    self.time_entry.get().strip(),
                    None if self._picked_video is not None else "",
                    video=self._picked_video,
                    repeat=self._repeat_choice(),
                    weekdays=self._weekday_choice(),
                )
            else:
                self.service.update_schedule(
                    self._editing_id,
                    time_text=self.time_entry.get().strip(),
                    repeat=self._repeat_choice(),
                    weekdays=self._weekday_choice(),
                )
        except ValueError as error:
            self.error.configure(text=str(error))
            return
        self.error.configure(text="")
        self._cancel_edit()
        self._refresh()

    def _start_edit(self, item: schedule_mod.ScheduleItem) -> None:
        self._editing_id = item.id
        self.form_title.configure(text="修改")
        self.submit_btn.configure(text="保存")
        self.cancel_edit_btn.pack(side="left", padx=(px(6), 0))
        self.time_entry.delete(0, tk.END)
        self.time_entry.insert(0, item.time)
        self._picked_video = None
        self.picked_label.configure(text=f"内容:{_short(item.title, 34)}", fg=FG_DIM)
        for index, key in enumerate(schedule_mod.REPEAT_CHOICES):
            if key == item.repeat:
                self.repeat_box.current(index)
        for index, var in enumerate(self._weekday_vars):
            var.set(index in item.weekdays)
        self._sync_repeat()
        self.time_entry.focus_set()

    def _cancel_edit(self) -> None:
        self._editing_id = None
        self._picked_video = None
        self.form_title.configure(text="添加一条")
        self.submit_btn.configure(text="添加")
        self.cancel_edit_btn.pack_forget()
        self.time_entry.delete(0, tk.END)
        self.time_entry.insert(0, self._suggest_time())
        self.repeat_box.current(0)
        self._sync_repeat()
        for var in self._weekday_vars:
            var.set(False)
        self.picked_label.configure(text="还没选视频(也可以只写计划)", fg=FG_DIM)

    # -- 列表 -------------------------------------------------------------
    def _refresh(self) -> None:
        scroll = self.list_canvas.yview()[0] if self._items else 0.0
        for child in self.list_inner.winfo_children():
            child.destroy()
        self._items = self.service.schedule_today()
        day = schedule_mod.today()
        self.date_label.configure(text=day)
        pending = [item for item in self._items if not schedule_mod.is_done(item, day)]
        self.summary.configure(text=f"{len(self._items)} 项 · 待做 {len(pending)}")
        done_week, planned_week = self.service.schedule_week_done()
        self.week_label.configure(text=f"本周 {done_week}/{planned_week}")

        next_item = self.service.schedule_next()
        self._next_id = next_item.id if next_item else None
        self._prime_covers()

        if not self._items:
            empty = tk.Frame(self.list_inner, bg=BG)
            empty.pack(fill="x", pady=px(22))
            tk.Label(
                empty, text="今天还没有安排", bg=BG, fg=FG, font=font(11, "bold")
            ).pack(anchor="w")
            tk.Label(
                empty,
                text="在下面选一条收藏视频、填个时间,到点主界面会提示你。",
                bg=BG,
                fg=FG_DIM,
                font=font(9),
            ).pack(anchor="w", pady=(px(2), 0))
        for item in self._items:
            self._build_row(item, day)

        self._refresh_week()
        self._resize()
        try:
            self.list_canvas.yview_moveto(scroll)
        except tk.TclError:
            pass
        if self._on_change is not None:
            self._on_change()

    def _build_row(self, item: schedule_mod.ScheduleItem, day: str) -> None:
        done = schedule_mod.is_done(item, day)
        is_next = item.id == self._next_id
        video = self._video_for(item)

        border = ACCENT if is_next else BORDER
        row = tk.Frame(
            self.list_inner, bg=CARD_BG, highlightthickness=1, highlightbackground=border
        )
        row.pack(fill="x", pady=(px(ROW_PAD_Y), px(2)), padx=px(1))

        def hover_in(_event, row=row, is_next=is_next):
            row.configure(highlightbackground=ACCENT if not is_next else ACCENT)

        def hover_out(_event, row=row, border=border):
            row.configure(highlightbackground=border)

        row.bind("<Enter>", hover_in)
        row.bind("<Leave>", hover_out)

        # 左:时间轴(时间 + 竖线 + 状态点)
        rail_height = cover_size()[1] + px(14)
        rail = tk.Canvas(row, width=px(112), height=rail_height, bg=CARD_BG, highlightthickness=0)
        rail.pack(side="left", fill="y", padx=(px(8), px(6)))
        midy = rail_height // 2
        rail_line_x = px(96)
        rail.create_line(rail_line_x, 0, rail_line_x, rail_height, fill=BORDER, width=1)
        time_color = WATCHED_FG if done else ACCENT
        rail.create_text(
            rail_line_x - px(12),
            midy,
            text=item.time,
            anchor="e",
            fill=time_color,
            font=font(12, "bold"),
        )
        if done:
            rail.create_oval(
                rail_line_x - px(6), midy - px(6), rail_line_x + px(6), midy + px(6),
                fill=ACCENT, outline=ACCENT,
            )
            rail.create_text(
                rail_line_x, midy, text="✓", fill="#FFFFFF", font=font(7, "bold")
            )
        elif is_next:
            rail.create_oval(
                rail_line_x - px(7), midy - px(7), rail_line_x + px(7), midy + px(7),
                fill=CARD_BG, outline=ACCENT, width=2,
            )
            rail.create_oval(
                rail_line_x - px(3), midy - px(3), rail_line_x + px(3), midy + px(3),
                fill=ACCENT, outline=ACCENT,
            )
        else:
            rail.create_oval(
                rail_line_x - px(5), midy - px(5), rail_line_x + px(5), midy + px(5),
                fill=CARD_BG, outline=BORDER, width=2,
            )
        rail.configure(cursor="hand2")
        rail.bind("<Button-1>", lambda _event, item_id=item.id: self._toggle(item_id))

        # 封面(或纯计划占位)
        cover_w, cover_h = cover_size()
        photo = self._cover_for(item)
        if photo is not None:
            cover = tk.Label(row, image=photo, bg=CARD_BG, bd=0, cursor="hand2")
            cover.image = photo  # 防止被回收
            cover.pack(side="left", pady=px(7))
        else:
            cover = tk.Canvas(
                row, width=cover_w, height=cover_h, bg=BG_ALT, highlightthickness=0,
                cursor="hand2" if video is not None else "arrow",
            )
            cover.pack(side="left", pady=px(7))
            cover.create_rectangle(
                px(1), px(1), cover_w - px(1), cover_h - px(1), outline=BORDER
            )
            if video is not None:
                url = self._cover_urls.get(item.id)
                loading_text = "暂无封面" if url in self._failed else "封面加载中"
                cover.create_text(
                    cover_w // 2, cover_h // 2, text=loading_text, fill=FG_DIM, font=font(8)
                )
            else:
                cover.create_text(
                    cover_w // 2, cover_h // 2 - px(5), text="计", fill=FG_DIM, font=font(14, "bold")
                )
                cover.create_text(
                    cover_w // 2, cover_h // 2 + px(13), text="纯计划", fill=WATCHED_FG, font=font(7)
                )
        if video is not None and self._on_play is not None:
            cover.bind("<Button-1>", lambda _event, item=item: self._play(item))

        # 中:标题 + 标签
        text_block = tk.Frame(row, bg=CARD_BG)
        text_block.pack(side="left", fill="both", expand=True, padx=(px(12), px(6)), pady=px(8))

        title_row = tk.Frame(text_block, bg=CARD_BG)
        title_row.pack(fill="x", anchor="w")
        title_text = _short(item.title, 28)
        title = tk.Label(
            title_row,
            text=title_text,
            bg=CARD_BG,
            fg=WATCHED_FG if done else FG,
            font=font(10, "bold" if not done else "normal"),
            anchor="w",
            cursor="hand2" if video is not None and self._on_play else "arrow",
        )
        title.pack(side="left")
        if len(item.title) > 28:
            self._bind_tooltip(title, item.title)
        if video is not None and self._on_play:
            title.bind("<Button-1>", lambda _event, item=item: self._play(item))
        title.bind("<Double-Button-1>", lambda _event, item=item: self._start_edit(item))

        if video is not None:
            detail = " · ".join(
                part for part in (getattr(video, "upper", ""), getattr(video, "duration_text", "")) if part
            )
            if detail:
                tk.Label(
                    text_block, text=detail, bg=CARD_BG, fg=WATCHED_FG, font=font(8), anchor="w"
                ).pack(fill="x", anchor="w", pady=(px(3), 0))

        meta = tk.Frame(text_block, bg=CARD_BG)
        meta.pack(fill="x", anchor="w", pady=(px(6), 0))
        self._chip(meta, schedule_mod.repeat_text(item), ACCENT if is_next else FG_DIM).pack(
            side="left", padx=(0, px(6))
        )
        if video is not None:
            self._chip(meta, self.service.topic_name(video), FG_DIM).pack(
                side="left", padx=(0, px(6))
            )
            progress = self._progress_text(video)
            if progress:
                tk.Label(
                    meta, text=progress, bg=CARD_BG, fg=FG_DIM, font=font(8)
                ).pack(side="left")
        if is_next:
            self._chip(meta, "下一项", ACCENT, solid=True).pack(side="left", padx=(px(4), 0))

        # 右:操作
        actions = tk.Frame(row, bg=CARD_BG)
        actions.pack(side="right", padx=(0, px(10)))
        if video is not None and self._on_play:
            play = tk.Label(
                actions, text="▶", bg=CARD_BG, fg=ACCENT, font=font(11), padx=px(6), cursor="hand2"
            )
            play.pack(side="left")
            play.bind("<Button-1>", lambda _event, item=item: self._play(item))
        edit = tk.Label(
            actions, text="改", bg=CARD_BG, fg=FG_DIM, font=font(8), padx=px(5), cursor="hand2"
        )
        edit.pack(side="left")
        edit.bind("<Button-1>", lambda _event, item=item: self._start_edit(item))
        delete = tk.Label(
            actions, text="×", bg=CARD_BG, fg=FG_DIM, font=font(11), padx=px(6), cursor="hand2"
        )
        delete.pack(side="left")
        delete.bind("<Button-1>", lambda _event, item_id=item.id: self._remove(item_id))

    # -- 悬停提示 ---------------------------------------------------------
    def _bind_tooltip(self, widget, text: str) -> None:
        def schedule(_event):
            self._cancel_tooltip()
            self._tooltip_job = self.after(450, lambda: self._show_tooltip(widget, text))

        def hide(_event):
            self._cancel_tooltip()
            self._hide_tooltip()

        widget.bind("<Enter>", schedule, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _show_tooltip(self, widget, text: str) -> None:
        self._tooltip_job = None
        if not widget.winfo_exists():
            return
        self._hide_tooltip()
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tip.configure(bg=BORDER)
        label = tk.Label(
            tip,
            text=text,
            bg=CARD_BG,
            fg=FG,
            font=font(9),
            justify="left",
            wraplength=px(420),
            padx=px(8),
            pady=px(5),
        )
        label.pack(padx=1, pady=1)
        x = widget.winfo_pointerx() + px(10)
        y = widget.winfo_pointery() + px(14)
        tip.geometry(f"+{x}+{y}")
        self._tooltip = tip

    def _hide_tooltip(self) -> None:
        tip = getattr(self, "_tooltip", None)
        self._tooltip = None
        if tip is not None:
            try:
                tip.destroy()
            except tk.TclError:
                pass

    def _cancel_tooltip(self) -> None:
        job = getattr(self, "_tooltip_job", None)
        self._tooltip_job = None
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass

    def _chip(self, parent, text: str, color: str, *, solid: bool = False) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=ACCENT if solid else BG_ALT,
            fg="#FFFFFF" if solid else color,
            font=font(7, "bold" if solid else "normal"),
            padx=px(6),
            pady=px(1),
        )

    def _progress_text(self, video) -> str:
        try:
            record = self.service.store.get_progress(video.key)
        except Exception:  # noqa: BLE001
            return ""
        if not record:
            return ""
        if record.get("finished"):
            return "已看完"
        position = float(record.get("position") or 0)
        if position <= 0:
            return ""
        return f"看到 {format_seconds(position)}"

    def _refresh_week(self) -> None:
        for child in self.week_strip.winfo_children():
            child.destroy()
        tk.Label(self.week_strip, text="本周", bg=BG, fg=FG_DIM, font=font(8)).pack(side="left")
        for entry in self.service.schedule_week():
            background = BG_ALT if entry["is_today"] else BG
            cell = tk.Frame(self.week_strip, bg=background)
            cell.pack(side="left", padx=px(3), fill="y")
            tk.Label(
                cell,
                text=entry["label"][1],
                bg=background,
                fg=FG if entry["is_today"] else FG_DIM,
                font=font(8, "bold" if entry["is_today"] else "normal"),
            ).pack()
            text = f"{entry['done']}/{entry['planned']}" if entry["planned"] else "—"
            all_done = entry["planned"] and entry["done"] >= entry["planned"]
            tk.Label(
                cell,
                text=text,
                bg=background,
                fg=ACCENT if all_done else (FG_DIM if entry["planned"] else WATCHED_FG),
                font=font(8),
            ).pack()
            bar = tk.Canvas(cell, width=px(34), height=px(5), bg=BORDER, highlightthickness=0)
            bar.pack(pady=(px(2), px(3)))
            if entry["planned"]:
                width = max(1, int(px(34) * entry["done"] / entry["planned"]))
                bar.create_rectangle(0, 0, width, px(5), fill=ACCENT, outline="")

    def _resize(self) -> None:
        """按今天有几条自动定高,不留一大块空白。"""
        try:
            self.update_idletasks()
            needed = self.list_inner.winfo_reqheight()
            height = max(px(96), min(px(430), needed + px(8)))
            self.list_canvas.configure(height=height)
            self.update_idletasks()
            width = px(self.WIDTH)
            total = self.winfo_reqheight()
            parent = self.master
            parent.update_idletasks()
            x = parent.winfo_rootx() + (max(parent.winfo_width(), 1) - width) // 2
            y = parent.winfo_rooty() + (max(parent.winfo_height(), 1) - total) // 3
            self.geometry(f"{width}x{total}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    # -- 行为 -------------------------------------------------------------
    def _toggle(self, item_id: str) -> None:
        try:
            self.service.toggle_schedule_done(item_id)
        except KeyError:
            pass
        self._refresh()

    def _remove(self, item_id: str) -> None:
        self.service.remove_schedule(item_id)
        if self._editing_id == item_id:
            self._cancel_edit()
        self._refresh()

    def _play(self, item: schedule_mod.ScheduleItem) -> None:
        if self._on_play is not None:
            self._on_play(item)

    def close(self) -> None:
        self._cancel_tooltip()
        self._hide_tooltip()
        if self._drain_job is not None:
            try:
                self.after_cancel(self._drain_job)
            except tk.TclError:
                pass
            self._drain_job = None
        if self._on_change is not None:
            self._on_change()
        self.destroy()
