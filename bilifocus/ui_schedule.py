"""日程面板:什么时候看哪条视频。

一条日程 = 时间 + 一条收藏视频(或文字计划)+ 重复规则。
面板里做四件事:看今天要看的、加/改、勾完成、回看本周记录。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import schedule as schedule_mod
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


def _center_over(window: tk.Misc, parent: tk.Misc, width: int, height: int) -> None:
    parent.update_idletasks()
    x = parent.winfo_rootx() + (max(parent.winfo_width(), 1) - width) // 2
    y = parent.winfo_rooty() + (max(parent.winfo_height(), 1) - height) // 3
    window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")


def _short(text: str, limit: int = 34) -> str:
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
    """今日日程。"""

    WIDTH = 680

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

        self.title("日程")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        self.bind("<Escape>", lambda _event: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        _center_over(self, parent, px(self.WIDTH), px(540))

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
        head.pack(fill="x", padx=px(20), pady=(px(16), px(4)))
        tk.Label(head, text="今日日程", bg=BG, fg=FG, font=font(15, "bold")).pack(side="left")
        self.summary = tk.Label(head, text="", bg=BG, fg=FG_DIM, font=font(9))
        self.summary.pack(side="left", padx=(px(10), 0))
        self.date_label = tk.Label(head, text="", bg=BG, fg=FG_DIM, font=font(9))
        self.date_label.pack(side="right")
        self.week_label = tk.Label(head, text="", bg=BG, fg=ACCENT, font=font(9, "bold"))
        self.week_label.pack(side="right", padx=(0, px(16)))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="x", padx=px(20))
        self.list_canvas = tk.Canvas(body, bg=BG, highlightthickness=0, height=px(230))
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

        self.week_strip = tk.Frame(self, bg=BG)
        self.week_strip.pack(fill="x", padx=px(20), pady=(px(6), px(2)))

        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=px(20), pady=(px(8), px(2)))
        self.form_title = tk.Label(form, text="添加", bg=BG, fg=FG_DIM, font=font(9))
        self.form_title.pack(side="left")
        self.time_entry = tk.Entry(
            form, width=6, bg=BG_ALT, fg=FG, insertbackground=FG, relief="flat", font=font(10)
        )
        self.time_entry.pack(side="left", padx=(px(8), px(6)), ipady=px(4))
        self.time_entry.insert(0, self._suggest_time())

        self._flat_button(form, "选收藏视频", self._open_picker).pack(side="left", padx=(0, px(6)))

        self.repeat_box = ttk.Combobox(
            form,
            values=[schedule_mod.REPEAT_LABELS[key] for key in schedule_mod.REPEAT_CHOICES],
            state="readonly",
            width=7,
            font=font(9),
        )
        self.repeat_box.current(0)
        self.repeat_box.pack(side="left", padx=(0, px(6)))
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
            font=font(9),
            padx=px(14),
            pady=px(3),
        )
        self.submit_btn.pack(side="left")
        self.cancel_edit_btn = self._flat_button(form, "取消编辑", self._cancel_edit)

        self.detail = tk.Frame(self, bg=BG)
        self.detail.pack(fill="x", padx=px(20), pady=(px(4), 0))
        self.picked_label = tk.Label(
            self.detail,
            text="还没选视频(也可以只写计划)",
            bg=BG,
            fg=FG_DIM,
            font=font(9),
            anchor="w",
        )
        self.picked_label.pack(side="left")
        self.weekday_row = tk.Frame(self.detail, bg=BG)

        self.hint = tk.Label(
            self,
            text="选一条收藏视频 + 时间;到点主界面会提示,点日程就能开始看",
            bg=BG,
            fg=FG_DIM,
            font=font(8),
        )
        self.hint.pack(pady=(px(4), px(2)))
        self.error = tk.Label(self, text="", bg=BG, fg=ERROR, font=font(9))
        self.error.pack(pady=(0, px(8)))
        self.time_entry.bind("<Return>", lambda _event: self._submit())

    def _flat_button(self, parent, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG_ALT,
            fg=FG,
            relief="flat",
            activebackground=BORDER,
            activeforeground=FG,
            font=font(9),
            padx=px(10),
            pady=px(3),
        )

    def _suggest_time(self) -> str:
        import time as _time

        return f"{(_time.localtime().tm_hour + 1) % 24:02d}:00"

    # -- 表单 -------------------------------------------------------------
    def _open_picker(self) -> None:
        if not self.videos:
            self.error.configure(text="还没读到收藏夹内容,先刷新一下主界面")
            return
        VideoPicker(self, self.videos, on_pick=self._show_picked)

    def _show_picked(self, video) -> None:
        self._picked_video = video
        self.picked_label.configure(text=f"已选:{_short(video.title, 30)}", fg=ACCENT)

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
                bg=BG,
                fg=FG_DIM,
                activebackground=BG,
                activeforeground=FG,
                selectcolor=BG_ALT,
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
        self.picked_label.configure(text=f"内容:{_short(item.title, 30)}", fg=FG_DIM)
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
        self.form_title.configure(text="添加")
        self.submit_btn.configure(text="添加")
        self.cancel_edit_btn.pack_forget()
        self.time_entry.delete(0, tk.END)
        self.time_entry.insert(0, self._suggest_time())
        self.repeat_box.current(0)
        self._sync_repeat()
        for var in self._weekday_vars:
            var.set(False)
        self.picked_label.configure(text="还没选视频(也可以只写计划)", fg=FG_DIM)

    def _resize(self) -> None:
        """按今天有几条自动定高:两条就别撑出一屏空白。"""
        try:
            self.update_idletasks()
            needed = self.list_inner.winfo_reqheight()
            list_h = max(px(80), min(px(320), needed + px(6)))
            self.list_canvas.configure(height=list_h)
            self.update_idletasks()

            # 窗口尺寸完全按内容需求来(不再强行给一个高度,避免列表下面留白)
            width = px(self.WIDTH)
            height = self.winfo_reqheight()
            parent = self.master
            parent.update_idletasks()
            x = parent.winfo_rootx() + (max(parent.winfo_width(), 1) - width) // 2
            y = parent.winfo_rooty() + (max(parent.winfo_height(), 1) - height) // 3
            self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    # -- 列表 -------------------------------------------------------------
    def _refresh(self) -> None:
        for child in self.list_inner.winfo_children():
            child.destroy()
        items = self.service.schedule_today()
        day = schedule_mod.today()
        self.date_label.configure(text=day)
        pending = [item for item in items if not schedule_mod.is_done(item, day)]
        self.summary.configure(text=f"{len(items)} 项 · 待做 {len(pending)}")
        done_week, planned_week = self.service.schedule_week_done()
        self.week_label.configure(text=f"本周 {done_week}/{planned_week}")

        if not items:
            tk.Label(
                self.list_inner,
                text="今天还没有安排。\n\n在上面选一条收藏视频、填个时间,到点主界面会提示你。",
                bg=BG,
                fg=FG_DIM,
                justify="left",
                anchor="w",
                font=font(10),
            ).pack(anchor="w", pady=px(16))
        for item in items:
            self._build_row(item, day)

        self._refresh_week()
        self._resize()
        if self._on_change is not None:
            self._on_change()

    def _build_row(self, item: schedule_mod.ScheduleItem, day: str) -> None:
        done = schedule_mod.is_done(item, day)
        row = tk.Frame(
            self.list_inner, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER
        )
        row.pack(fill="x", pady=px(3))

        check = tk.Label(
            row,
            text="✓" if done else "○",
            bg=CARD_BG,
            fg=ACCENT if done else FG_DIM,
            font=font(11),
            width=3,
            cursor="hand2",
        )
        check.pack(side="left", padx=(px(4), 0), pady=px(6))
        check.bind("<Button-1>", lambda _event, item_id=item.id: self._toggle(item_id))

        time_label = tk.Label(
            row, text=item.time, bg=CARD_BG, fg=ACCENT, font=font(10, "bold")
        )
        time_label.pack(side="left", padx=(px(2), px(8)))
        time_label.bind("<Button-1>", lambda _event, item_id=item.id: self._toggle(item_id))

        tk.Label(
            row,
            text=schedule_mod.repeat_text(item),
            bg=BG_ALT,
            fg=FG_DIM,
            font=font(8),
            padx=px(6),
            pady=px(2),
        ).pack(side="left", padx=(0, px(8)))

        title = tk.Label(
            row,
            text=_short(item.title, 28),
            bg=CARD_BG,
            fg=WATCHED_FG if done else FG,
            font=font(10),
            anchor="w",
            cursor="hand2" if item.bvid and self._on_play else "arrow",
        )
        title.pack(side="left", fill="x", expand=True)
        title.bind("<Double-Button-1>", lambda _event, item=item: self._start_edit(item))
        if item.bvid and self._on_play:
            title.bind("<Button-1>", lambda _event, item=item: self._play(item))

        if item.bvid and self._on_play:
            play = tk.Label(
                row,
                text="▶ 开始看",
                bg=CARD_BG,
                fg=ACCENT,
                font=font(8),
                padx=px(6),
                cursor="hand2",
            )
            play.pack(side="right", padx=(0, px(2)))
            play.bind("<Button-1>", lambda _event, item=item: self._play(item))

        delete = tk.Label(
            row, text="×", bg=CARD_BG, fg=FG_DIM, font=font(11), padx=px(8), cursor="hand2"
        )
        delete.pack(side="right")
        delete.bind("<Button-1>", lambda _event, item_id=item.id: self._remove(item_id))

        edit = tk.Label(
            row, text="改", bg=CARD_BG, fg=FG_DIM, font=font(8), padx=px(4), cursor="hand2"
        )
        edit.pack(side="right")
        edit.bind("<Button-1>", lambda _event, item=item: self._start_edit(item))

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
            bar = tk.Canvas(
                cell, width=px(28), height=px(4), bg=BORDER, highlightthickness=0
            )
            bar.pack(pady=(px(2), px(3)))
            if entry["planned"]:
                width = max(1, int(px(28) * entry["done"] / entry["planned"]))
                bar.create_rectangle(0, 0, width, px(4), fill=ACCENT, outline="")

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
        if self._on_change is not None:
            self._on_change()
        self.destroy()
