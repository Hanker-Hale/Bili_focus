"""日程:什么时候看哪条视频。

一条日程由三部分组成:

- **时间**:当天几点(如 20:00)
- **看什么**:绑定收藏夹里的一条视频(也允许只写文字计划)
- **重复**:仅今天 / 每天 / 每周几

完成状态按**日期**记录(`done_dates`),所以"每天"的日程今天勾了,
明天会自己回到未完成;"仅今天"的日程过期后自动归档,不再出现在列表里。

这里只放纯逻辑(不含界面、不碰存储),方便单独测。
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MAX_TITLE = 80
MAX_DONE_DATES = 120  # 每条日程最多记这么久的历史,避免无限增长

REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"
REPEAT_CHOICES = (REPEAT_DAILY, REPEAT_ONCE, REPEAT_WEEKLY)
REPEAT_LABELS = {REPEAT_DAILY: "每天", REPEAT_ONCE: "仅今天", REPEAT_WEEKLY: "每周几"}
WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass
class ScheduleItem:
    id: str
    time: str                       # "HH:MM"
    title: str                      # 视频标题快照或文字计划
    bvid: str = ""                  # 绑定的收藏视频(空 = 纯文字计划)
    repeat: str = REPEAT_DAILY      # once / daily / weekly
    date: str = ""                  # once:目标日期 "YYYY-MM-DD"
    weekdays: tuple[int, ...] = ()  # weekly:0=周一 … 6=周日
    done_dates: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "time": self.time,
            "title": self.title,
            "bvid": self.bvid,
            "repeat": self.repeat,
            "date": self.date,
            "weekdays": list(self.weekdays),
            "done_dates": list(self.done_dates),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ScheduleItem | None":
        if not isinstance(raw, dict):
            return None
        time_text = normalize_time(str(raw.get("time") or ""))
        title = str(raw.get("title") or "").strip()
        if not time_text or not title:
            return None

        repeat = str(raw.get("repeat") or REPEAT_DAILY)
        if repeat not in REPEAT_CHOICES:
            repeat = REPEAT_DAILY

        weekdays: list[int] = []
        for value in raw.get("weekdays") or ():
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index <= 6 and index not in weekdays:
                weekdays.append(index)
        weekdays.sort()
        if repeat == REPEAT_WEEKLY and not weekdays:
            repeat = REPEAT_DAILY

        done: list[str] = []
        for value in raw.get("done_dates") or ():
            text = str(value)
            if len(text) == 10 and text[4] == "-" and text not in done:
                done.append(text)
        # 兼容旧格式:只有 done_day(单日)
        legacy = str(raw.get("done_day") or "")
        if legacy and legacy not in done:
            done.append(legacy)

        return cls(
            id=str(raw.get("id") or new_id()),
            time=time_text,
            title=normalize_title(title),
            bvid=str(raw.get("bvid") or ""),
            repeat=repeat,
            date=str(raw.get("date") or ""),
            weekdays=tuple(weekdays),
            done_dates=tuple(done[-MAX_DONE_DATES:]),
        )


_counter = 0


def new_id() -> str:
    global _counter
    _counter += 1
    return f"s{int(_time.time() * 1000)}{_counter}"


def normalize_time(text: str) -> str | None:
    """把用户输入收敛成 HH:MM。接受 20:00 / 8:5 / 2000 / 8点30 这类写法。"""
    raw = (text or "").strip()
    if not raw:
        return None
    raw = raw.replace("：", ":").replace(".", ":").replace("点", ":").replace("分", "")
    raw = raw.replace("时", ":")
    if ":" in raw:
        left, _, right = raw.partition(":")
        left = left.strip()
        right = right.strip() or "0"
        if not left.isdigit() or not right.isdigit():
            return None
        hour, minute = int(left), int(right)
    else:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            return None
        if len(digits) <= 2:
            hour, minute = int(digits), 0
        elif len(digits) == 3:
            hour, minute = int(digits[0]), int(digits[1:])
        else:
            hour, minute = int(digits[:-2]), int(digits[-2:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def normalize_title(text: str) -> str:
    title = " ".join((text or "").split())
    return title[:MAX_TITLE]


def normalize_weekdays(values) -> tuple[int, ...]:
    days = sorted({int(value) for value in values if 0 <= int(value) <= 6})
    return tuple(days)


def today(now: float | None = None) -> str:
    moment = _time.time() if now is None else now
    return date.fromtimestamp(moment).isoformat()


def weekday_of(day: str) -> int:
    """0=周一 … 6=周日。"""
    return datetime.strptime(day, "%Y-%m-%d").weekday()


def day_label(day: str) -> str:
    return WEEKDAY_LABELS[weekday_of(day)]


def shift_day(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).date().isoformat()


def repeat_text(item: ScheduleItem) -> str:
    if item.repeat == REPEAT_ONCE:
        return "仅今天" if item.date == today() else f"仅 {item.date[5:]}"
    if item.repeat == REPEAT_WEEKLY:
        if not item.weekdays:
            return "每周"
        return "每周" + "、".join(WEEKDAY_LABELS[index][1:] for index in item.weekdays)
    return "每天"


def applies_on(item: ScheduleItem, day: str | None = None) -> bool:
    target = day or today()
    if item.repeat == REPEAT_ONCE:
        return item.date == target
    if item.repeat == REPEAT_WEEKLY:
        return weekday_of(target) in item.weekdays
    return True


def is_done(item: ScheduleItem, day: str | None = None) -> bool:
    return (day or today()) in item.done_dates


def mark_done(item: ScheduleItem, day: str | None = None, done: bool = True) -> None:
    target = day or today()
    dates = [value for value in item.done_dates if value != target]
    if done:
        dates.append(target)
        dates = dates[-MAX_DONE_DATES:]
    item.done_dates = tuple(dates)


def sort_items(items: list[ScheduleItem]) -> list[ScheduleItem]:
    return sorted(items, key=lambda item: item.time)


def items_on(items: list[ScheduleItem], day: str | None = None) -> list[ScheduleItem]:
    """当天真正"应该出现"的日程(按时间排序)。"""
    target = day or today()
    return sort_items([item for item in items if applies_on(item, target)])


def pending_on(items: list[ScheduleItem], day: str | None = None) -> list[ScheduleItem]:
    target = day or today()
    return [item for item in items_on(items, target) if not is_done(item, target)]


def now_text(now: float | None = None) -> str:
    return _time.strftime("%H:%M", _time.localtime(now))


def next_pending(items: list[ScheduleItem], now: float | None = None) -> ScheduleItem | None:
    """今天还没做、时间最接近现在的那一项(优先还没到点的)。"""
    moment = _time.time() if now is None else now
    day = today(moment)
    current = now_text(moment)
    pending = pending_on(items, day)
    if not pending:
        return None
    later = [item for item in pending if item.time >= current]
    return later[0] if later else pending[0]


def due_now(
    items: list[ScheduleItem],
    now: float | None = None,
    *,
    window_minutes: int = 120,
    dismissed: set[str] | None = None,
) -> ScheduleItem | None:
    """到点提醒:今天这个时间点已经过了、但还没勾掉的最近一项。

    `dismissed` 里放 `id@日期`,用户点过"稍后"的就不再提醒。
    """
    moment = _time.time() if now is None else now
    day = today(moment)
    current = now_text(moment)
    skip = dismissed or set()
    candidates = [
        item
        for item in pending_on(items, day)
        if item.time <= current and f"{item.id}@{day}" not in skip
    ]
    if not candidates:
        return None
    item = candidates[-1]  # 最接近现在的那一项
    hours, minutes = (int(part) for part in item.time.split(":"))
    planned = datetime.fromtimestamp(moment).replace(
        hour=hours, minute=minutes, second=0, microsecond=0
    )
    if (datetime.fromtimestamp(moment) - planned) > timedelta(minutes=window_minutes):
        return None
    return item


def week_view(items: list[ScheduleItem], day: str | None = None) -> list[dict]:
    """最近 7 天(含今天)的计划/完成数,用于"本周记录"。"""
    target = day or today()
    days = [shift_day(target, offset) for offset in range(-6, 1)]
    view: list[dict] = []
    for current in days:
        planned = items_on(items, current)
        done = [item for item in planned if is_done(item, current)]
        view.append(
            {
                "date": current,
                "label": day_label(current),
                "planned": len(planned),
                "done": len(done),
                "is_today": current == target,
            }
        )
    return view


def week_done(items: list[ScheduleItem], day: str | None = None) -> tuple[int, int]:
    view = week_view(items, day)
    return sum(entry["done"] for entry in view), sum(entry["planned"] for entry in view)


def describe(item: ScheduleItem) -> str:
    return f"{item.time} · {repeat_text(item)} · {item.title}"


def prune(items: list[ScheduleItem], now: float | None = None) -> list[ScheduleItem]:
    """丢掉"仅今天"且已经过期很久的一次性日程(保留最近 7 天,便于回看)。"""
    moment = _time.time() if now is None else now
    cutoff = shift_day(today(moment), -7)
    kept: list[ScheduleItem] = []
    for item in items:
        if item.repeat == REPEAT_ONCE and item.date and item.date < cutoff:
            continue
        kept.append(item)
    return kept
