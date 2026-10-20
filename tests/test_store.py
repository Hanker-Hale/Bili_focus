"""持久化测试:原子写、损坏恢复、v1 迁移、缓存 TTL。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from favfocus.models import Video
from favfocus.store import (
    SCHEMA_VERSION,
    CacheStore,
    CachedFavorites,
    StateStore,
    default_home,
)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class LegacyHomeMigrationTests(unittest.TestCase):
    """改名迁移:旧 bili-focus 只搬状态,不搬缓存。"""

    def test_migrates_state_but_skips_regenerable_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "bili-focus"
            (legacy / "cache").mkdir(parents=True)
            (legacy / "player-profile").mkdir()
            (legacy / "state.json").write_text('{"schema": 4}', encoding="utf-8")
            (legacy / "cache" / "fav.json").write_text("{}", encoding="utf-8")

            env = {
                key: value
                for key, value in os.environ.items()
                if key not in ("FAVFOCUS_HOME", "BILI_FOCUS_HOME")
            }
            env["APPDATA"] = tmp
            with mock.patch.dict(os.environ, env, clear=True):
                home = default_home()

            self.assertEqual(home, root / "favfocus")
            self.assertTrue((home / "state.json").is_file())
            self.assertFalse((home / "cache").exists())
            self.assertFalse((home / "player-profile").exists())


class StateStoreTests(StoreTestCase):
    def test_fresh_store_defaults(self):
        store = StateStore(home=self.home)
        self.assertIsNone(store.sessdata)
        self.assertEqual(store.watched, {})
        self.assertEqual(store.prefs, {})
        self.assertFalse(store.path.exists())

    def test_sessdata_round_trip(self):
        store = StateStore(home=self.home)
        store.set_sessdata("abc%2C123")
        self.assertEqual(store.sessdata, "abc%2C123")
        # 新实例重新读盘
        self.assertEqual(StateStore(home=self.home).sessdata, "abc%2C123")

    def test_clear_sessdata(self):
        store = StateStore(home=self.home)
        store.set_sessdata("x")
        store.set_sessdata(None)
        self.assertIsNone(store.sessdata)

    def test_watched_lifecycle(self):
        store = StateStore(home=self.home)
        self.assertTrue(store.mark_watched("BV1", when=100))
        self.assertFalse(store.mark_watched("BV1"), "重复标记不该改写时间戳")
        self.assertEqual(store.watched["BV1"], 100)
        self.assertTrue(store.is_watched("BV1"))
        self.assertTrue(store.unmark_watched("BV1"))
        self.assertFalse(store.unmark_watched("BV1"))
        self.assertFalse(store.is_watched("BV1"))

    def test_watched_persists_across_instances(self):
        StateStore(home=self.home).mark_watched("BV9", when=5)
        self.assertEqual(StateStore(home=self.home).watched, {"BV9": 5})

    def test_prune_watched(self):
        store = StateStore(home=self.home)
        store.mark_watched("keep", when=1)
        store.mark_watched("drop", when=2)
        removed = store.prune_watched(["keep"])
        self.assertEqual(removed, 1)
        self.assertEqual(store.watched, {"keep": 1})

    def test_prefs(self):
        store = StateStore(home=self.home)
        store.set_pref("fid", 12345678)
        store.set_pref("hide_watched", True)
        reloaded = StateStore(home=self.home)
        self.assertEqual(reloaded.get_pref("fid"), 12345678)
        self.assertTrue(reloaded.get_pref("hide_watched"))
        self.assertEqual(reloaded.get_pref("missing", "fallback"), "fallback")

    def test_corrupt_state_is_quarantined(self):
        path = self.home / "state.json"
        path.write_text("{not json", encoding="utf-8")
        store = StateStore(home=self.home)
        self.assertIsNone(store.sessdata)
        backups = list(self.home.glob("state.corrupt-*.json"))
        self.assertEqual(len(backups), 1, "损坏文件应留备份而不是静默丢失")
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{not json")
        self.assertFalse(path.exists(), "损坏的原文件应被移走")

    def test_v1_migration_keeps_data(self):
        # v1 没有 schema 字段,结构是 {cookies, watched, ui}
        path = self.home / "state.json"
        path.write_text(
            json.dumps(
                {"cookies": {"SESSDATA": "old"}, "watched": {"BV1": 1}, "ui": {"fid": 5}}
            ),
            encoding="utf-8",
        )
        store = StateStore(home=self.home)
        self.assertEqual(store.sessdata, "old")
        self.assertEqual(store.watched, {"BV1": 1})
        self.assertEqual(store.get_pref("fid"), 5)
        self.assertEqual(store._data["schema"], SCHEMA_VERSION)

    def test_unknown_fields_are_preserved(self):
        path = self.home / "state.json"
        path.write_text(json.dumps({"schema": 2, "future": {"keep": 1}}), encoding="utf-8")
        store = StateStore(home=self.home)
        self.assertEqual(store._data["future"], {"keep": 1})

    def test_describe_hides_secrets(self):
        store = StateStore(home=self.home)
        store.set_sessdata("super-secret")
        described = store.describe()
        self.assertTrue(described["has_sessdata"])
        self.assertNotIn("super-secret", json.dumps(described))

    def test_write_is_atomic_no_tmp_left(self):
        store = StateStore(home=self.home)
        store.set_sessdata("x")
        leftovers = list(self.home.glob("*.tmp"))
        self.assertEqual(leftovers, [])


class CacheStoreTests(StoreTestCase):
    def _videos(self):
        return [
            Video(aid=1, bvid="BV1", title="a", upper="u", duration=60, fav_time=1),
            Video(aid=2, bvid="BV2", title="b", upper="u", duration=120, fav_time=2),
        ]

    def test_put_then_get(self):
        cache = CacheStore(home=self.home)
        cache.put(9, self._videos(), total=2, now=1000.0)
        cached = cache.get(9)
        assert isinstance(cached, CachedFavorites)
        self.assertEqual(len(cached.videos), 2)
        self.assertEqual(cached.total, 2)
        self.assertEqual(cached.fetched_at, 1000.0)

    def test_ttl_expiry(self):
        cache = CacheStore(home=self.home)
        cache.put(9, self._videos(), total=2, now=1000.0)
        self.assertIsNotNone(cache.get(9, max_age=10, now=1005.0))
        self.assertIsNone(cache.get(9, max_age=10, now=1011.0), "超过 TTL 应视为没有缓存")

    def test_missing_returns_none(self):
        self.assertIsNone(CacheStore(home=self.home).get(404))

    def test_corrupt_cache_is_removed(self):
        cache = CacheStore(home=self.home)
        cache.put(9, self._videos(), total=2, now=1000.0)
        cache.path_for(9).write_text("{broken", encoding="utf-8")
        self.assertIsNone(cache.get(9))
        self.assertFalse(cache.path_for(9).exists())

    def test_clear_one_and_all(self):
        cache = CacheStore(home=self.home)
        cache.put(1, self._videos(), total=2)
        cache.put(2, self._videos(), total=2)
        self.assertEqual(cache.clear(1), 1)
        self.assertIsNone(cache.get(1))
        self.assertIsNotNone(cache.get(2))
        self.assertEqual(cache.clear(), 1)

    def test_does_not_leak_between_folders(self):
        cache = CacheStore(home=self.home)
        cache.put(1, [self._videos()[0]], total=1, now=1000.0)
        cache.put(2, self._videos(), total=2, now=1000.0)
        self.assertEqual(StateStore(home=self.home).watched, {})  # 缓存不碰已看

    def test_age(self):
        cached = CachedFavorites(videos=(), total=0, fetched_at=100.0)
        self.assertEqual(cached.age_seconds(now=130.0), 30.0)


class ConcurrencyTests(StoreTestCase):
    """界面会在后台线程取数据、主线程标记已看,两者都会落盘。"""

    def test_concurrent_marks_do_not_lose_updates(self):
        import threading

        store = StateStore(home=self.home)

        def worker(prefix: str) -> None:
            for index in range(25):
                store.mark_watched(f"{prefix}{index}", when=1)

        threads = [threading.Thread(target=worker, args=(f"t{n}-",)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(store.watched), 100, "并发标记不能互相覆盖")
        self.assertEqual(len(StateStore(home=self.home).watched), 100)

    def test_concurrent_writes_leave_valid_json_and_no_tmp(self):
        import json
        import threading

        store = StateStore(home=self.home)

        def worker(number: int) -> None:
            for index in range(20):
                store.set_pref(f"k{number}", index)
                store.mark_watched(f"{number}-{index}", when=index)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        payload = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], SCHEMA_VERSION)
        self.assertEqual(len(payload["watched"]), 60)
        self.assertEqual(list(self.home.glob("*.tmp")), [], "不该留下临时文件")


class ProgressTests(StoreTestCase):
    """观看进度:记录/读取/清除/清理,以及 v2 → v3 迁移。"""

    def test_set_and_get_progress(self):
        store = StateStore(home=self.home)
        record = store.set_progress("BV1", position=120.5, duration=600, watched_seconds=118, when=1000)
        self.assertEqual(record["position"], 120.5)
        self.assertEqual(record["updated_at"], 1000)
        self.assertEqual(store.get_progress("BV1")["duration"], 600)
        self.assertEqual(store.get_progress("BV1")["watched"], 118)

    def test_finished_flag_persists(self):
        store = StateStore(home=self.home)
        store.set_progress("BV1", position=100, duration=100, finished=True)
        again = StateStore(home=self.home)
        self.assertTrue(again.get_progress("BV1")["finished"])
        self.assertFalse(StoreFinishDefault := StateStore(home=self.home).get_progress("BV1").get("finished") is None)

    def test_progress_survives_reload(self):
        store = StateStore(home=self.home)
        store.set_progress("BV1", position=30, duration=100, when=1)
        again = StateStore(home=self.home)
        self.assertEqual(again.get_progress("BV1")["position"], 30)

    def test_missing_progress_returns_none(self):
        store = StateStore(home=self.home)
        self.assertIsNone(store.get_progress("nope"))

    def test_clear_progress(self):
        store = StateStore(home=self.home)
        store.set_progress("BV1", position=10, duration=100)
        self.assertTrue(store.clear_progress("BV1"))
        self.assertFalse(store.clear_progress("BV1"))
        self.assertIsNone(store.get_progress("BV1"))

    def test_prune_progress(self):
        store = StateStore(home=self.home)
        store.set_progress("keep", position=1, duration=10)
        store.set_progress("drop", position=2, duration=10)
        self.assertEqual(store.prune_progress(["keep"]), 1)
        self.assertIsNone(store.get_progress("drop"))

    def test_v3_schedule_migrates_to_repeat_rules(self):
        """v3 的日程是"文字 + 单日完成",v4 要变成"按日期记录完成"。"""
        payload = {
            "schema": 3,
            "schedule": [
                {"id": "s1", "time": "20:00", "title": "旧日程", "done_day": "2026-10-17"},
                {"id": "s2", "time": "21:00", "title": "没完成的"},
                {"time": "bad", "title": "坏条目"},
            ],
        }
        (self.home / "state.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        store = StateStore(home=self.home)
        migrated = store.schedule
        self.assertEqual(len(migrated), 3)
        self.assertEqual(migrated[0]["done_dates"], ["2026-10-17"])
        self.assertNotIn("done_day", migrated[0])
        self.assertEqual(migrated[0]["repeat"], "daily")
        self.assertEqual(migrated[1]["done_dates"], [])
        self.assertEqual(store._data["schema"], SCHEMA_VERSION)

    def test_v2_state_migrates_with_empty_progress(self):
        import json

        legacy = {
            "schema": 2,
            "cookies": {"SESSDATA": "x"},
            "watched": {"BV1": 1},
            "ui": {"fid": 1},
        }
        (self.home / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
        store = StateStore(home=self.home)
        self.assertEqual(store.progress, {})
        self.assertEqual(store.stats, {})
        self.assertEqual(store.watched, {"BV1": 1})
        self.assertEqual(store.describe()["schema"], SCHEMA_VERSION)
        self.assertTrue(store.is_watched("BV1"), "旧版已看标记必须保留")


class WatchStatsTests(StoreTestCase):
    """累计观看时长与播放次数。"""

    def test_accumulates_and_persists(self):
        store = StateStore(home=self.home)
        store.add_watch_seconds(120)
        store.add_watch_seconds(30.5)
        self.assertAlmostEqual(store.total_watch_seconds, 150.5, places=1)
        self.assertAlmostEqual(StateStore(home=self.home).total_watch_seconds, 150.5, places=1)

    def test_ignores_non_positive(self):
        store = StateStore(home=self.home)
        store.add_watch_seconds(0)
        store.add_watch_seconds(-5)
        self.assertEqual(store.total_watch_seconds, 0.0)

    def test_session_counters(self):
        store = StateStore(home=self.home)
        store.add_session(watched_seconds=10, plays=1, finished=0)
        store.add_session(watched_seconds=20, plays=1, finished=1)
        self.assertEqual(store.stats.get("plays"), 2)
        self.assertEqual(store.stats.get("finished"), 1)

    def test_describe_exposes_watch_seconds(self):
        store = StateStore(home=self.home)
        store.add_watch_seconds(77)
        info = store.describe()
        self.assertEqual(info["watch_seconds"], 77)
        self.assertEqual(info["plays"], 0)


class HomeTests(unittest.TestCase):
    def test_env_override(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("FAVFOCUS_HOME")
            os.environ["FAVFOCUS_HOME"] = tmp
            try:
                self.assertEqual(str(default_home()), tmp)
            finally:
                if old is None:
                    os.environ.pop("FAVFOCUS_HOME", None)
                else:
                    os.environ["FAVFOCUS_HOME"] = old


if __name__ == "__main__":
    unittest.main()
