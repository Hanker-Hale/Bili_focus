"""日程测试:重复规则、按日完成、到点提醒、本周记录、绑定视频。"""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from bilifocus import schedule as sc
from bilifocus.models import Video
from bilifocus.service import FavoritesService
from bilifocus.store import StateStore


def at(hour: int, minute: int, *, days_ago: int = 0) -> float:
    """构造一个"某天某时刻"的时间戳,给 now 参数注入用。"""
    now = datetime.now() + timedelta(days=-days_ago)
    return datetime(now.year, now.month, now.day, hour, minute).timestamp()


def demo_video(bvid: str = "BVdemo1", title: str = "示例视频:Python 编程入门") -> Video:
    return Video(
        aid=1,
        bvid=bvid,
        title=title,
        upper="示例UP",
        duration=600,
        fav_time=0,
        intro="",
        attr=0,
        pic="",
    )


class TimeParseTests(unittest.TestCase):
    def test_common_formats(self):
        self.assertEqual(sc.normalize_time("20:00"), "20:00")
        self.assertEqual(sc.normalize_time("8:5"), "08:05")
        self.assertEqual(sc.normalize_time("2000"), "20:00")
        self.assertEqual(sc.normalize_time("2030"), "20:30")
        self.assertEqual(sc.normalize_time("8点30"), "08:30")

    def test_invalid(self):
        self.assertIsNone(sc.normalize_time(""))
        self.assertIsNone(sc.normalize_time("abc"))
        self.assertIsNone(sc.normalize_time("24:00"))
        self.assertIsNone(sc.normalize_time("12:60"))


class ItemTests(unittest.TestCase):
    def test_round_trip_keeps_repeat_rule(self):
        item = sc.ScheduleItem(
            id="s1",
            time="20:00",
            title="看数学",
            bvid="BV1",
            repeat=sc.REPEAT_WEEKLY,
            weekdays=(0, 2, 4),
            done_dates=("2026-10-18",),
        )
        restored = sc.ScheduleItem.from_dict(item.to_dict())
        self.assertEqual(restored.bvid, "BV1")
        self.assertEqual(restored.repeat, sc.REPEAT_WEEKLY)
        self.assertEqual(restored.weekdays, (0, 2, 4))
        self.assertEqual(restored.done_dates, ("2026-10-18",))

    def test_legacy_done_day_becomes_done_dates(self):
        restored = sc.ScheduleItem.from_dict(
            {"id": "s2", "time": "20:00", "title": "旧日程", "done_day": "2026-10-17"}
        )
        self.assertEqual(restored.done_dates, ("2026-10-17",))
        self.assertEqual(restored.repeat, sc.REPEAT_DAILY)

    def test_weekly_without_days_falls_back_to_daily(self):
        restored = sc.ScheduleItem.from_dict(
            {"time": "20:00", "title": "x", "repeat": "weekly", "weekdays": []}
        )
        self.assertEqual(restored.repeat, sc.REPEAT_DAILY)

    def test_bad_item_is_dropped(self):
        self.assertIsNone(sc.ScheduleItem.from_dict({"time": "oops", "title": "x"}))
        self.assertIsNone(sc.ScheduleItem.from_dict({"time": "20:00", "title": ""}))
        self.assertIsNone(sc.ScheduleItem.from_dict("not a dict"))


class RepeatTests(unittest.TestCase):
    def test_daily_applies_every_day(self):
        item = sc.ScheduleItem("a", "20:00", "每天", repeat=sc.REPEAT_DAILY)
        self.assertTrue(sc.applies_on(item, "2026-10-19"))
        self.assertTrue(sc.applies_on(item, "2026-10-20"))

    def test_once_only_on_its_date(self):
        item = sc.ScheduleItem("a", "20:00", "一次性", repeat=sc.REPEAT_ONCE, date="2026-10-19")
        self.assertTrue(sc.applies_on(item, "2026-10-19"))
        self.assertFalse(sc.applies_on(item, "2026-10-20"))

    def test_weekly_matches_chosen_weekdays(self):
        # 2026-10-19 是周一,2026-10-21 是周三
        item = sc.ScheduleItem("a", "20:00", "每周一三", repeat=sc.REPEAT_WEEKLY, weekdays=(0, 2))
        self.assertTrue(sc.applies_on(item, "2026-10-19"))
        self.assertTrue(sc.applies_on(item, "2026-10-21"))
        self.assertFalse(sc.applies_on(item, "2026-10-20"))

    def test_repeat_text(self):
        self.assertEqual(sc.repeat_text(sc.ScheduleItem("a", "20:00", "x")), "每天")
        weekly = sc.ScheduleItem("b", "20:00", "x", repeat=sc.REPEAT_WEEKLY, weekdays=(0, 4))
        self.assertEqual(sc.repeat_text(weekly), "每周一、五")
        once = sc.ScheduleItem("c", "20:00", "x", repeat=sc.REPEAT_ONCE, date=sc.today())
        self.assertEqual(sc.repeat_text(once), "仅今天")


class DoneTests(unittest.TestCase):
    def test_mark_done_is_per_day(self):
        item = sc.ScheduleItem("a", "20:00", "每天")
        sc.mark_done(item, "2026-10-19")
        self.assertTrue(sc.is_done(item, "2026-10-19"))
        self.assertFalse(sc.is_done(item, "2026-10-20"))
        sc.mark_done(item, "2026-10-19", done=False)
        self.assertFalse(sc.is_done(item, "2026-10-19"))

    def test_done_dates_are_capped(self):
        item = sc.ScheduleItem("a", "20:00", "每天")
        for offset in range(sc.MAX_DONE_DATES + 20):
            sc.mark_done(item, f"2026-01-{offset % 28 + 1:02d}")
        self.assertLessEqual(len(item.done_dates), sc.MAX_DONE_DATES)


class ListTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            sc.ScheduleItem("a", "21:00", "晚", repeat=sc.REPEAT_DAILY),
            sc.ScheduleItem("b", "09:00", "早", repeat=sc.REPEAT_DAILY),
            sc.ScheduleItem("c", "10:00", "一次性", repeat=sc.REPEAT_ONCE, date="2026-10-19"),
        ]

    def test_items_on_sorted_and_filtered(self):
        shown = sc.items_on(self.items, "2026-10-19")
        self.assertEqual([item.id for item in shown], ["b", "c", "a"])
        only_daily = sc.items_on(self.items, "2026-10-20")
        self.assertEqual([item.id for item in only_daily], ["b", "a"])

    def test_pending_excludes_done(self):
        sc.mark_done(self.items[1], "2026-10-19")
        pending = sc.pending_on(self.items, "2026-10-19")
        self.assertNotIn("b", [item.id for item in pending])

    def test_next_pending_prefers_later_item(self):
        nxt = sc.next_pending(self.items, now=at(12, 0))
        self.assertEqual(nxt.id, "a")  # 12:00 之后最近的是 21:00

    def test_next_pending_falls_back_to_earliest(self):
        nxt = sc.next_pending(self.items, now=at(23, 30))
        self.assertEqual(nxt.id, "b")  # 都过点了 → 回到最早没做的

    def test_next_pending_none_when_all_done(self):
        day = sc.today()
        sc.mark_done(self.items[1], day)
        sc.mark_done(self.items[0], day)
        self.assertIsNone(sc.next_pending([self.items[0], self.items[1]], now=at(8, 0)))


class DueTests(unittest.TestCase):
    def test_due_within_window(self):
        items = [sc.ScheduleItem("a", "20:00", "看数学")]
        self.assertEqual(sc.due_now(items, now=at(20, 30)).id, "a")

    def test_not_due_before_time(self):
        items = [sc.ScheduleItem("a", "20:00", "看数学")]
        self.assertIsNone(sc.due_now(items, now=at(19, 30)))

    def test_not_due_after_window(self):
        items = [sc.ScheduleItem("a", "20:00", "看数学")]
        self.assertIsNone(sc.due_now(items, now=at(23, 0), window_minutes=120))

    def test_dismissed_is_skipped(self):
        items = [sc.ScheduleItem("a", "20:00", "看数学")]
        day = sc.today()
        self.assertIsNone(sc.due_now(items, now=at(20, 10), dismissed={f"a@{day}"}))

    def test_done_item_is_not_due(self):
        item = sc.ScheduleItem("a", "20:00", "看数学")
        sc.mark_done(item, sc.today())
        self.assertIsNone(sc.due_now([item], now=at(20, 10)))


class WeekTests(unittest.TestCase):
    def test_week_view_counts(self):
        items = [
            sc.ScheduleItem("a", "20:00", "每天"),
            sc.ScheduleItem("b", "21:00", "每天"),
        ]
        sc.mark_done(items[0], sc.today())
        view = sc.week_view(items, "2026-10-19")
        self.assertEqual(len(view), 7)
        today_entry = view[-1]
        self.assertTrue(today_entry["is_today"])
        self.assertEqual(today_entry["planned"], 2)
        self.assertEqual(today_entry["done"], 1)
        self.assertEqual(sc.week_done(items, "2026-10-19"), (1, 14))

    def test_prune_drops_old_once_items(self):
        items = [
            sc.ScheduleItem("old", "20:00", "老的", repeat=sc.REPEAT_ONCE, date="2020-01-01"),
            sc.ScheduleItem("new", "20:00", "每天"),
        ]
        kept = sc.prune(items, now=at(12, 0))
        self.assertEqual([item.id for item in kept], ["new"])


class ServiceScheduleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.service = FavoritesService(store=StateStore(home=self.home))

    def test_add_with_video_binding(self):
        video = demo_video()
        item = self.service.add_schedule("20:00", video=video)
        self.assertEqual(item.bvid, "BVdemo1")
        self.assertEqual(item.title, video.title)
        self.assertEqual(item.repeat, sc.REPEAT_DAILY)

    def test_add_weekly_requires_days(self):
        with self.assertRaises(ValueError):
            self.service.add_schedule("20:00", "看数学", repeat=sc.REPEAT_WEEKLY)

        item = self.service.add_schedule(
            "20:00", "看数学", repeat=sc.REPEAT_WEEKLY, weekdays=[2, 0]
        )
        self.assertEqual(item.weekdays, (0, 2))

    def test_add_once_uses_today(self):
        item = self.service.add_schedule("20:00", "一次性", repeat=sc.REPEAT_ONCE)
        self.assertEqual(item.date, sc.today())

    def test_add_requires_title_or_video(self):
        with self.assertRaises(ValueError):
            self.service.add_schedule("20:00", "   ")

    def test_update_time_and_repeat(self):
        item = self.service.add_schedule("20:00", "看数学")
        updated = self.service.update_schedule(
            item.id, time_text="21:30", repeat=sc.REPEAT_WEEKLY, weekdays=[1]
        )
        self.assertEqual(updated.time, "21:30")
        self.assertEqual(updated.repeat, sc.REPEAT_WEEKLY)
        self.assertEqual(updated.weekdays, (1,))

    def test_update_keeps_done_history(self):
        item = self.service.add_schedule("20:00", "看数学")
        self.service.toggle_schedule_done(item.id)
        updated = self.service.update_schedule(item.id, time_text="19:00")
        self.assertTrue(sc.is_done(updated))

    def test_toggle_done_today_only(self):
        item = self.service.add_schedule("20:00", "看数学")
        self.assertTrue(self.service.toggle_schedule_done(item.id))
        self.assertTrue(sc.is_done(self.service.schedule_items()[0]))
        self.assertFalse(self.service.toggle_schedule_done(item.id))

    def test_finish_video_ticks_bound_items(self):
        video = demo_video()
        self.service.add_schedule("20:00", video=video)
        self.assertEqual(self.service.finish_schedule_for_video(video), 1)
        self.assertTrue(sc.is_done(self.service.schedule_items()[0]))
        self.assertEqual(self.service.finish_schedule_for_video(video), 0)

    def test_finish_video_ignores_other_videos(self):
        self.service.add_schedule("20:00", video=demo_video("BVother", "别的视频"))
        self.assertEqual(self.service.finish_schedule_for_video(demo_video()), 0)

    def test_due_and_dismiss(self):
        # 用"几分钟前"的时间,避免测试依赖运行时刻(比如半夜跑)
        recent = time.strftime("%H:%M", time.localtime(time.time() - 300))
        item = self.service.add_schedule(recent, "看数学")
        due = self.service.schedule_due()
        self.assertIsNotNone(due)
        self.assertEqual(due.id, item.id)
        self.service.dismiss_schedule_reminder(item)
        self.assertIsNone(self.service.schedule_due())

    def test_week_done(self):
        video = demo_video()
        item = self.service.add_schedule("20:00", video=video)
        self.service.toggle_schedule_done(item.id)
        done, planned = self.service.schedule_week_done()
        self.assertGreaterEqual(done, 1)
        self.assertGreaterEqual(planned, done)

    def test_remove(self):
        item = self.service.add_schedule("20:00", "看数学")
        self.assertTrue(self.service.remove_schedule(item.id))
        self.assertFalse(self.service.remove_schedule(item.id))
        self.assertEqual(self.service.schedule_items(), [])

    def test_persists_across_instances(self):
        self.service.add_schedule("20:00", "看数学", repeat=sc.REPEAT_WEEKLY, weekdays=[0])
        again = FavoritesService(store=StateStore(home=self.home))
        self.assertEqual(again.schedule_items()[0].weekdays, (0,))

    def test_corrupt_entries_are_dropped(self):
        self.service.store.set_schedule(
            [{"time": "bad", "title": "x"}, {"time": "20:00", "title": "ok"}]
        )
        self.assertEqual([item.title for item in self.service.schedule_items()], ["ok"])


if __name__ == "__main__":
    unittest.main()
