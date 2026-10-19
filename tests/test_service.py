"""服务层测试:缓存语义、登录回滚、已看标记、统计、导出。"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from bilifocus.errors import AuthRequiredError
from bilifocus.models import Video
from bilifocus.service import FavoritesService, normalize_sessdata, search
from bilifocus.store import StateStore

from tests.fakes import (
    FAV,
    NAV,
    Clock,
    api_error,
    fav_payload,
    json_response,
    make_service,
    media_item,
    nav_payload,
)


def videos_payload(*items, total=None, has_more=False):
    return json_response(fav_payload(list(items), total=total, has_more=has_more))


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def harness(self, routes, **kwargs):
        return make_service(routes, self.home, **kwargs)


class LoginTests(ServiceTestCase):
    def test_normalize_strips_prefix_and_quotes(self):
        self.assertEqual(normalize_sessdata("SESSDATA=abc"), "abc")
        self.assertEqual(normalize_sessdata('sessdata="abc"'), "abc")
        self.assertEqual(normalize_sessdata("  abc  "), "abc")

    def test_normalize_accepts_whole_row_copy(self):
        """从 DevTools 表格里连名字一起复制时也要能用(名字后面常是 Tab)。"""
        self.assertEqual(normalize_sessdata("SESSDATA\tabc%2C123"), "abc%2C123")
        self.assertEqual(normalize_sessdata("SESSDATA: abc%2C123"), "abc%2C123")
        self.assertEqual(normalize_sessdata("SESSDATA：abc%2C123"), "abc%2C123")
        self.assertEqual(normalize_sessdata("  SESSDATA = abc%2C123  "), "abc%2C123")
        self.assertEqual(normalize_sessdata("sessdata\tabc\n"), "abc")

    def test_normalize_keeps_value_that_only_looks_similar(self):
        # 正常值不会以 "SESSDATA" 开头,但真出现也不能被切坏
        self.assertEqual(normalize_sessdata("SESSDATAx1"), "SESSDATAx1")

    def test_normalize_empty(self):
        self.assertEqual(normalize_sessdata(""), "")
        self.assertEqual(normalize_sessdata("   "), "")
        self.assertEqual(normalize_sessdata('""'), "")

    def test_login_saves_and_verifies(self):
        harness = self.harness({NAV: json_response(nav_payload(is_login=True, uname="乙"))})
        state = harness.service.login("SESSDATA=abc%2C123")
        self.assertTrue(state.is_login)
        self.assertEqual(state.uname, "乙")
        self.assertEqual(harness.service.store.sessdata, "abc%2C123")
        self.assertTrue(harness.service.is_logged_in)

    def test_login_with_invalid_sessdata_rolls_back(self):
        """验证失败不能把无效凭证留在磁盘上,否则界面会显示"已登录"却取不到数据。"""
        harness = self.harness({NAV: json_response(nav_payload(is_login=False))})
        with self.assertRaises(AuthRequiredError):
            harness.service.login("bad")
        self.assertIsNone(harness.service.store.sessdata)

    def test_login_via_browser_uses_captured_value(self):
        """浏览器登录只是"取值的来源"不同,之后的验证与保存必须走同一条路。"""
        import bilifocus.browser_login as browser_login

        harness = self.harness({NAV: json_response(nav_payload(is_login=True, uname="丙"))})
        original = browser_login.BrowserLogin.capture

        def fake_capture(self, progress=None):
            if progress:
                progress("fake")
            return "captured%2Cvalue"

        browser_login.BrowserLogin.capture = fake_capture
        try:
            state = harness.service.login_via_browser()
        finally:
            browser_login.BrowserLogin.capture = original

        self.assertTrue(state.is_login)
        self.assertEqual(state.uname, "丙")
        self.assertEqual(harness.service.store.sessdata, "captured%2Cvalue")

    def test_login_via_browser_rolls_back_on_invalid_capture(self):
        import bilifocus.browser_login as browser_login

        harness = self.harness({NAV: json_response(nav_payload(is_login=False))})
        original = browser_login.BrowserLogin.capture
        browser_login.BrowserLogin.capture = lambda self, progress=None: "bad-value"
        try:
            with self.assertRaises(AuthRequiredError):
                harness.service.login_via_browser()
        finally:
            browser_login.BrowserLogin.capture = original
        self.assertIsNone(harness.service.store.sessdata)

    def test_forget_browser_login(self):
        harness = self.harness({NAV: json_response(nav_payload(is_login=True))})
        profile = harness.service.browser_profile_dir
        self.assertFalse(harness.service.forget_browser_login(), "不存在时返回 False")
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "marker").write_text("x", encoding="utf-8")
        self.assertTrue(harness.service.forget_browser_login())
        self.assertFalse(profile.exists())

    def test_login_rejects_empty(self):
        harness = self.harness({NAV: json_response(nav_payload(is_login=True))})
        with self.assertRaises(ValueError):
            harness.service.login("   ")

    def test_logout_clears(self):
        harness = self.harness({NAV: json_response(nav_payload(is_login=True))})
        harness.service.login("abc")
        harness.service.logout()
        self.assertIsNone(harness.service.store.sessdata)


class CacheTests(ServiceTestCase):
    def test_first_call_hits_network_second_uses_cache(self):
        harness = self.harness(
            {NAV: json_response(nav_payload()), FAV: videos_payload(media_item(1, "BV1", "一"))}
        )
        first = harness.service.videos(12345678)
        self.assertFalse(first.from_cache)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 1)

        second = harness.service.videos(12345678)
        self.assertTrue(second.from_cache)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 1, "第二次不该联网")
        self.assertEqual(first.videos, second.videos)

    def test_force_bypasses_cache(self):
        harness = self.harness(
            {
                NAV: json_response(nav_payload()),
                FAV: [videos_payload(media_item(1, "BV1", "一")), videos_payload(media_item(2, "BV2", "二"))],
            }
        )
        harness.service.videos(1)
        refreshed = harness.service.videos(1, force=True)
        self.assertFalse(refreshed.from_cache)
        self.assertEqual([video.bvid for video in refreshed.videos], ["BV2"])
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 2)

    def test_checks_are_throttled_by_sync_interval(self):
        """间隔内不联网;过了间隔才做一次增量核对(只拉第 1 页)。"""
        harness = self.harness(
            {
                NAV: json_response(nav_payload()),
                FAV: [
                    videos_payload(media_item(1, "BV1", "一")),
                    videos_payload(media_item(1, "BV1", "一")),
                ],
            },
            cache_ttl=600.0,
        )
        harness.service.videos(1)
        harness.clock.advance(599)
        self.assertTrue(harness.service.videos(1).from_cache)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 1, "间隔内不该联网")

        harness.clock.advance(2)
        view = harness.service.videos(1)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 2, "过间隔后核对一次")
        self.assertTrue(view.from_cache, "没有新内容时仍标记为缓存")
        self.assertEqual(view.new_count, 0)
        self.assertEqual(view.checked_at, harness.clock.now)

    def test_cached_view_reports_total_and_timestamp(self):
        harness = self.harness(
            {NAV: json_response(nav_payload()), FAV: videos_payload(media_item(1, "BV1", "一"), total=99)}
        )
        view = harness.service.videos(1)
        self.assertEqual(view.total, 99)
        self.assertEqual(view.fetched_at, harness.clock.now)
        self.assertEqual(view.fid, 1)

    def test_page_is_never_cached(self):
        harness = self.harness(
            {NAV: json_response(nav_payload()), FAV: videos_payload(media_item(1, "BV1", "一"))}
        )
        harness.service.page(1, 1)
        harness.service.page(1, 1)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 2)

    def test_auth_error_surfaces_from_videos(self):
        harness = self.harness(
            {NAV: json_response(nav_payload()), FAV: json_response(api_error(-101, "账号未登录"))}
        )
        with self.assertRaises(AuthRequiredError):
            harness.service.videos(1)
        self.assertIsNone(harness.service.cache.get(1), "失败不该写缓存")


class IncrementalTests(ServiceTestCase):
    """自动增量:只拉第 1 页,靠"遇到已认识的条目"判断边界。"""

    def harness_with(self, responses, *, interval=180.0):
        return self.harness(
            {NAV: json_response(nav_payload()), FAV: responses},
            cache_ttl=interval,
        )

    def test_new_item_is_prepended_without_full_refetch(self):
        harness = self.harness_with(
            [
                videos_payload(media_item(1, "BV1", "旧一"), media_item(2, "BV2", "旧二"), total=2),
                # 过了间隔后:第一页多了一条新的,总数 3
                videos_payload(
                    media_item(9, "BV9", "新的"), media_item(1, "BV1", "旧一"), total=3
                ),
            ]
        )
        harness.service.videos(1)
        harness.clock.advance(181)

        view = harness.service.videos(1)
        self.assertEqual(view.new_count, 1)
        self.assertFalse(view.from_cache)
        self.assertEqual([video.bvid for video in view.videos][:2], ["BV9", "BV1"])
        self.assertEqual(len(view.videos), 3)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 2, "增量只该多请求 1 页")

    def test_no_change_keeps_cache_and_does_not_rewrite(self):
        harness = self.harness_with(
            [
                videos_payload(media_item(1, "BV1", "一"), total=1),
                videos_payload(media_item(1, "BV1", "一"), total=1),
            ]
        )
        harness.service.videos(1)
        before = harness.service.cache.path_for(1).stat().st_mtime_ns
        harness.clock.advance(181)

        view = harness.service.videos(1)
        self.assertEqual(view.new_count, 0)
        self.assertTrue(view.from_cache)
        self.assertEqual(
            harness.service.cache.path_for(1).stat().st_mtime_ns, before, "没变化不该重写缓存"
        )

    def test_removal_triggers_full_refetch(self):
        harness = self.harness_with(
            [
                videos_payload(
                    media_item(1, "BV1", "一"), media_item(2, "BV2", "二"), total=2
                ),
                # 第二页起被删了一条:总数变 1 → 走全量
                videos_payload(media_item(1, "BV1", "一"), total=1),
            ]
        )
        harness.service.videos(1)
        harness.clock.advance(181)

        view = harness.service.videos(1)
        self.assertEqual(len(view.videos), 1)
        self.assertFalse(view.from_cache)
        self.assertEqual(view.new_count, 0, "全量同步时没有旧缓存可比,不算新增")

    def test_many_new_items_are_collected_across_pages(self):
        """一次新增超过一页(20 条)时,要顺着往前翻到已知区。"""
        old = [media_item(index, f"BV{index}", f"旧{index}") for index in range(1, 3)]
        fresh = [media_item(100 + index, f"BV{100 + index}", f"新{index}") for index in range(25)]
        page1 = fresh[:20]
        page2 = fresh[20:] + old
        harness = self.harness_with(
            [
                videos_payload(*old, total=2),
                videos_payload(*page1, total=27, has_more=True),
                videos_payload(*page2, total=27, has_more=False),
            ]
        )
        harness.service.videos(1)
        harness.clock.advance(181)

        view = harness.service.videos(1)
        self.assertEqual(view.new_count, 25)
        self.assertEqual(len(view.videos), 27)
        self.assertEqual(view.videos[0].bvid, "BV100")
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 3)

    def test_too_many_new_pages_falls_back_to_full(self):
        """新增页数超过上限时,干脆全量重拉,避免无限翻页。"""
        old = [media_item(1, "BV1", "旧")]
        responses = [videos_payload(*old, total=1)]
        # 连续 5 页全是新内容(正好到 INCREMENTAL_MAX_PAGES),再往前就该放弃
        for page in range(5):
            responses.append(
                videos_payload(
                    media_item(200 + page, f"BV{200 + page}", f"新{page}"),
                    total=100,
                    has_more=True,
                )
            )
        responses.append(videos_payload(*old, total=1))
        harness = self.harness_with(responses)
        harness.service.videos(1)
        harness.clock.advance(181)

        view = harness.service.videos(1)
        self.assertEqual(len(view.videos), 1, "超过页数上限后走全量,结果来自最后一次响应")
        self.assertFalse(view.from_cache)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 7, "1 次初始 + 5 次增量 + 1 次全量")

    def test_recent_check_costs_no_request(self):
        harness = self.harness_with([videos_payload(media_item(1, "BV1", "一"), total=1)])
        harness.service.videos(1)
        for _ in range(5):
            harness.clock.advance(10)
            view = harness.service.videos(1)
            self.assertTrue(view.from_cache)
        self.assertEqual(len(harness.transport.calls_matching(FAV)), 1)


class WatchedTests(ServiceTestCase):
    def test_mark_toggle_and_count(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        service = harness.service
        video = Video(aid=1, bvid="BV1", title="一", upper="u", duration=60, fav_time=0)

        self.assertFalse(service.is_watched(video))
        self.assertTrue(service.mark_watched(video))
        self.assertTrue(service.is_watched("BV1"))
        self.assertFalse(service.mark_watched(video), "重复标记返回 False")

        self.assertFalse(service.toggle_watched(video))
        self.assertFalse(service.is_watched(video))
        self.assertTrue(service.toggle_watched(video))

    def test_watched_count_and_stats(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        service = harness.service
        videos = [
            Video(aid=1, bvid="BV1", title="a", upper="甲", duration=60, fav_time=0),
            Video(aid=2, bvid="BV2", title="b", upper="甲", duration=120, fav_time=0),
            Video(aid=3, bvid="BV3", title="c", upper="乙", duration=180, fav_time=0),
        ]
        service.mark_watched("BV1")
        self.assertEqual(service.watched_count(videos), 1)
        stats = service.stats(videos)
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["watched"], 1)
        self.assertEqual(stats["unwatched"], 2)
        self.assertEqual(stats["total_duration_seconds"], 360)
        self.assertEqual(stats["total_duration_text"], "6 分钟")
        self.assertEqual(stats["top_uploaders"][0], {"upper": "甲", "count": 2})

    def test_stats_flags_invalid(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        videos = [
            Video(aid=1, bvid="BV1", title="a", upper="u", duration=0, fav_time=0),
            Video(aid=2, bvid="", title="失效", upper="u", duration=0, fav_time=0, attr=1),
        ]
        stats = harness.service.stats(videos)
        self.assertEqual(stats["invalid"], 1)

    def test_random_prefers_unwatched(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        service = harness.service
        videos = [
            Video(aid=1, bvid="BV1", title="a", upper="u", duration=60, fav_time=0),
            Video(aid=2, bvid="BV2", title="b", upper="u", duration=60, fav_time=0),
        ]
        service.mark_watched("BV1")
        chosen = service.random_unwatched(videos, rng=lambda pool: pool[0])
        assert chosen is not None
        self.assertEqual(chosen.bvid, "BV2")

    def test_random_falls_back_when_all_watched(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        service = harness.service
        videos = [Video(aid=1, bvid="BV1", title="a", upper="u", duration=60, fav_time=0)]
        service.mark_watched("BV1")
        self.assertEqual(service.random_unwatched(videos, rng=lambda pool: pool[0]), videos[0])

    def test_random_skips_unopenable(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        service = harness.service
        videos = [
            Video(aid=1, bvid="", title="失效", upper="u", duration=0, fav_time=0, attr=1),
            Video(aid=2, bvid="BV2", title="b", upper="u", duration=60, fav_time=0),
        ]
        chosen = service.random_unwatched(videos, rng=lambda pool: pool[0])
        assert chosen is not None
        self.assertEqual(chosen.bvid, "BV2")

    def test_random_on_empty_returns_none(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        self.assertIsNone(harness.service.random_unwatched([]))


class DefaultFidTests(ServiceTestCase):
    def test_videos_records_default_fid(self):
        harness = self.harness(
            {NAV: json_response(nav_payload()), FAV: videos_payload(media_item(1, "BV1", "一"))}
        )
        harness.service.videos(12345678)
        self.assertEqual(harness.service.default_fid, 12345678)
        # 换一个实例也要记得
        fresh = FavoritesService(store=harness.service.store)
        self.assertEqual(fresh.default_fid, 12345678)

    def test_set_default_fid_with_none(self):
        harness = self.harness({NAV: json_response(nav_payload()), FAV: videos_payload()})
        harness.service.set_default_fid(5)
        harness.service.set_default_fid(None)
        self.assertIsNone(harness.service.default_fid)


class ExportTests(ServiceTestCase):
    def _view(self):
        harness = self.harness(
            {
                NAV: json_response(nav_payload()),
                FAV: videos_payload(
                    media_item(1, "BV1", "标题一", duration=65, upper="甲", fav_time=1700000000),
                    media_item(2, "BV2", "标题二", duration=3725, upper="乙", fav_time=1700000100),
                ),
            }
        )
        harness.service.mark_watched("BV1")
        return harness, harness.service.videos(1)

    def test_export_csv(self):
        harness, view = self._view()
        target = harness.service.export_csv(view, self.home / "out" / "fav.csv")
        self.assertTrue(target.exists())
        with target.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0][:3], ["bvid", "aid", "标题"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][0], "BV1")
        self.assertEqual(rows[1][2], "标题一")
        self.assertEqual(rows[1][5], "1:05")
        self.assertEqual(rows[1][7], "是")
        self.assertEqual(rows[2][7], "")

    def test_export_json(self):
        harness, view = self._view()
        target = harness.service.export_json(view, self.home / "out" / "fav.json")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["fid"], 1)
        self.assertEqual(payload["count"], 2)
        self.assertTrue(payload["videos"][0]["watched"])
        self.assertFalse(payload["videos"][1]["watched"])
        self.assertEqual(payload["videos"][0]["url"], "https://www.bilibili.com/video/BV1")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.videos = [
            Video(aid=1, bvid="BV1", title="Serenity 环境搭建", upper="甲", duration=0, fav_time=0),
            Video(aid=2, bvid="BV2", title="另一个视频", upper="Serenity", duration=0, fav_time=0),
            Video(aid=3, bvid="BV3", title="无关", upper="乙", duration=0, fav_time=0),
        ]

    def test_matches_title_case_insensitively(self):
        self.assertEqual([video.bvid for video in search(self.videos, "serenity")], ["BV1", "BV2"])

    def test_matches_upper(self):
        self.assertEqual([video.bvid for video in search(self.videos, "乙")], ["BV3"])

    def test_empty_keyword_returns_all(self):
        self.assertEqual(len(search(self.videos, "")), 3)
        self.assertEqual(len(search(self.videos, "   ")), 3)

    def test_no_match(self):
        self.assertEqual(search(self.videos, "不存在"), [])


class ProgressTests(ServiceTestCase):
    """观看进度:续播位置、看完判定、进度视图。"""

    def setUp(self):
        super().setUp()
        self.service = self.harness({}).service
        self.video = Video(
            aid=1, bvid="BV1progress", title="长视频", upper="甲", duration=600, fav_time=0
        )

    def test_no_progress_resumes_from_zero(self):
        self.assertEqual(self.service.resume_position(self.video), 0)

    def test_partial_progress_resumes(self):
        self.service.record_progress(
            self.video, position=120, duration=600, watched_seconds=118
        )
        self.assertEqual(self.service.resume_position(self.video), 120)

    def test_finished_progress_marks_watched_and_keeps_record(self):
        record = self.service.record_progress(
            self.video, position=580, duration=600, watched_seconds=450
        )
        self.assertTrue(record["finished"])
        self.assertTrue(self.service.is_watched(self.video), "看完应标为已观看")
        kept = self.service.store.get_progress(self.video.key)
        self.assertIsNotNone(kept, "看完也要保留进度记录,退出小结靠它统计")
        self.assertTrue(kept["finished"])
        self.assertEqual(self.service.resume_position(self.video), 0, "看完后从头看")

    def test_ratio_threshold_is_90_percent(self):
        # 90% 正好算看完
        at_90 = self.service.record_progress(
            self.video, position=540, duration=600, watched_seconds=530
        )
        self.assertTrue(at_90["finished"], "90% 应算看完")
        # 89.9% 不算
        below = self.service.record_progress(
            self.video, position=539, duration=600, watched_seconds=520
        )
        self.assertFalse(below["finished"], "不到 90% 不该算看完")

    def test_short_video_also_needs_90_percent(self):
        # 旧版对短视频有"离结尾 90 秒就算看完"的宽松规则,现已去掉:
        # 120 秒的视频看到 40 秒(33%)不算看完
        short = Video(aid=2, bvid="BVshort", title="短视频", upper="乙", duration=120, fav_time=0)
        record = self.service.record_progress(
            short, position=40, duration=120, watched_seconds=38
        )
        self.assertFalse(record["finished"])
        # 108 秒(90%)才算
        done = self.service.record_progress(
            short, position=108, duration=120, watched_seconds=100
        )
        self.assertTrue(done["finished"])

    def test_just_started_is_not_finished(self):
        record = self.service.record_progress(
            self.video, position=10, duration=600, watched_seconds=8
        )
        self.assertFalse(record["finished"])
        self.assertAlmostEqual(record["ratio"], 10 / 600, places=3)

    def test_progress_view(self):
        self.service.record_progress(self.video, position=150, duration=600)
        view = self.service.progress_view([self.video])
        self.assertIn(self.video.key, view)
        self.assertAlmostEqual(view[self.video.key]["ratio"], 0.25, places=3)
        self.assertEqual(view[self.video.key]["position"], 150)

    def test_recording_progress_keeps_watched_flag_untouched(self):
        self.service.record_progress(self.video, position=30, duration=600)
        self.assertFalse(self.service.is_watched(self.video))


class WatchStatsTests(ServiceTestCase):
    """累计观看时长与消化率。"""

    def test_total_watch_seconds_counts_finished_too(self):
        service = self.harness({}).service
        long_video = Video(aid=1, bvid="BV1", title="长", upper="u", duration=600, fav_time=0)
        short = Video(aid=2, bvid="BV2", title="短", upper="u", duration=120, fav_time=0)
        service.record_progress(long_video, position=100, duration=600, watched_seconds=90)
        service.record_progress(short, position=115, duration=120, watched_seconds=80)  # 看完
        self.assertAlmostEqual(service.total_watch_seconds(), 170.0, places=1)
        self.assertEqual(service.store.stats.get("plays"), 2)
        self.assertEqual(service.store.stats.get("finished"), 1)

    def test_digest_counts_finished_by_progress(self):
        service = self.harness({}).service
        videos = [
            Video(aid=i, bvid=f"BV{i}", title="t", upper="u", duration=600, fav_time=0)
            for i in range(1, 5)
        ]
        # 95% 看完;60 秒(10%)看了一半;手动标记不算数
        service.record_progress(videos[0], position=570, duration=600, watched_seconds=560)
        service.record_progress(videos[1], position=60, duration=600, watched_seconds=58)
        service.mark_watched(videos[2])
        digest = service.digest(videos)
        self.assertEqual(digest["total"], 4)
        self.assertEqual(digest["finished"], 1, "只有进度达标的算看完,手动标记不算")
        self.assertEqual(digest["in_progress"], 1)
        self.assertEqual(digest["remaining"], 3)
        self.assertAlmostEqual(digest["rate"], 0.25, places=3)
        self.assertEqual(digest["rate_percent"], 25.0)

    def test_digest_handles_empty(self):
        service = self.harness({}).service
        digest = service.digest([])
        self.assertEqual(digest["total"], 0)
        self.assertEqual(digest["rate"], 0.0)
        self.assertEqual(digest["rate_percent"], 0.0)


class ClockTests(unittest.TestCase):
    def test_clock_advances(self):
        clock = Clock(100.0)
        clock.advance(5)
        self.assertEqual(clock(), 105.0)


class TopicOverrideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.service = FavoritesService(store=StateStore(home=self.home))
        self.video = Video(
            aid=1, bvid="BV1", title="随手拍的一段", upper="u", duration=60, fav_time=0
        )

    def test_set_and_read_override(self):
        self.assertEqual(self.service.topic_overrides(), {})
        self.service.set_topic_override(self.video, "graphics")
        self.assertEqual(self.service.topic_overrides(), {"BV1": "graphics"})
        self.assertEqual(self.service.topic_name(self.video), "图形学 / 渲染")

    def test_clear_override(self):
        self.service.set_topic_override(self.video, "graphics")
        self.service.set_topic_override(self.video, None)
        self.assertEqual(self.service.topic_overrides(), {})
        self.assertEqual(self.service.topic_name(self.video), "其他")

    def test_invalid_topic_raises(self):
        with self.assertRaises(ValueError):
            self.service.set_topic_override(self.video, "nope")

    def test_persists_and_ignores_bad_data(self):
        self.service.set_topic_override(self.video, "math")
        again = FavoritesService(store=StateStore(home=self.home))
        self.assertEqual(again.topic_overrides(), {"BV1": "math"})
        again.store.set_pref("topic_overrides", {"BV1": "bad", "BV2": 3})
        self.assertEqual(again.topic_overrides(), {})

    def test_caps_growth(self):
        for index in range(FavoritesService.MAX_TOPIC_OVERRIDES + 20):
            video = Video(aid=index, bvid=f"BV{index}", title="x", upper="u", duration=1, fav_time=0)
            self.service.set_topic_override(video, "code")
        self.assertLessEqual(
            len(self.service.topic_overrides()), FavoritesService.MAX_TOPIC_OVERRIDES
        )


if __name__ == "__main__":
    unittest.main()
