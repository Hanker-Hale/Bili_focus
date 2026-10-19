"""数据模型测试:脏数据、空壳条目、格式化的边界情况。"""

from __future__ import annotations

import unittest

from bilifocus.models import Folder, LoginState, Page, Video


class VideoTests(unittest.TestCase):
    def test_parses_normal_entry(self):
        video = Video.from_api(
            {
                "id": 123,
                "bvid": "BV1xx411c7mD",
                "title": "  标题带空格  ",
                "duration": 125,
                "fav_time": 1700000000,
                "upper": {"name": "某UP"},
            }
        )
        assert video is not None
        self.assertEqual(video.bvid, "BV1xx411c7mD")
        self.assertEqual(video.title, "标题带空格")
        self.assertEqual(video.upper, "某UP")
        self.assertTrue(video.is_valid)
        self.assertTrue(video.is_openable)
        self.assertEqual(video.url, "https://www.bilibili.com/video/BV1xx411c7mD")

    def test_empty_husk_is_dropped(self):
        # 收藏夹里被删除的视频会以没有 id/bvid 的空壳出现
        self.assertIsNone(Video.from_api({"title": "已失效视频", "id": 0, "bvid": ""}))
        self.assertIsNone(Video.from_api({}))

    def test_missing_bvid_keeps_entry_but_not_openable(self):
        video = Video.from_api({"id": 42, "bvid": "", "title": "已失效视频", "attr": 9})
        assert video is not None
        self.assertEqual(video.key, "av42")
        self.assertFalse(video.is_openable)
        self.assertEqual(video.url, "")

    def test_attr_bit0_marks_invalid(self):
        valid = Video.from_api({"id": 1, "bvid": "BV1", "title": "x", "attr": 0})
        invalid = Video.from_api({"id": 2, "bvid": "BV2", "title": "y", "attr": 1})
        self.assertTrue(valid.is_valid)
        self.assertFalse(invalid.is_valid)

    def test_duration_formats(self):
        cases = {0: "--:--", 5: "0:05", 65: "1:05", 125: "2:05", 3600: "1:00:00", 3725: "1:02:05"}
        for seconds, expected in cases.items():
            video = Video(aid=1, bvid="BV1", title="t", upper="u", duration=seconds, fav_time=0)
            self.assertEqual(video.duration_text, expected, f"{seconds}s")

    def test_missing_upper_has_fallback(self):
        video = Video.from_api({"id": 1, "bvid": "BV1", "title": "t"})
        self.assertEqual(video.upper, "未知UP")

    def test_dict_round_trip(self):
        original = Video(aid=7, bvid="BV7", title="t", upper="u", duration=65, fav_time=1700000000)
        restored = Video.from_dict(original.to_dict())
        self.assertEqual(original, restored)

    def test_reads_cover_field_from_favorites_api(self):
        """收藏夹接口的字段名是 cover;视频详情接口才叫 pic —— 两个都要认。"""
        video = Video.from_api(
            {"id": 1, "bvid": "BV1", "title": "t", "cover": "http://i0.hdslb.com/a.jpg"}
        )
        self.assertEqual(video.pic, "http://i0.hdslb.com/a.jpg")

    def test_prefers_pic_when_both_present(self):
        video = Video.from_api(
            {
                "id": 1,
                "bvid": "BV1",
                "title": "t",
                "pic": "http://i0.hdslb.com/pic.jpg",
                "cover": "http://i0.hdslb.com/cover.jpg",
            }
        )
        self.assertEqual(video.pic, "http://i0.hdslb.com/pic.jpg")

    def test_pic_survives_dict_round_trip(self):
        original = Video(
            aid=1,
            bvid="BV1",
            title="t",
            upper="u",
            duration=1,
            fav_time=0,
            pic="http://i0.hdslb.com/x.jpg",
        )
        self.assertEqual(original, Video.from_dict(original.to_dict()))


class CoverUrlTests(unittest.TestCase):
    def _video(self, pic: str) -> Video:
        return Video(aid=1, bvid="BV1", title="t", upper="u", duration=0, fav_time=0, pic=pic)

    def test_builds_png_suffix_url(self):
        video = self._video("http://i0.hdslb.com/bfs/archive/abc.jpg")
        self.assertEqual(
            video.cover_url(260, 146),
            "https://i0.hdslb.com/bfs/archive/abc.jpg@260w_146h_1c.png",
        )

    def test_upgrades_http_to_https(self):
        self.assertTrue(self._video("http://i0.hdslb.com/a.jpg").cover_url().startswith("https://"))

    def test_empty_pic_gives_empty_url(self):
        self.assertEqual(self._video("").cover_url(), "")

    def test_rejects_non_https_source(self):
        # 防止把 file:// 之类的东西拼进 URL
        self.assertEqual(self._video("file:///etc/passwd").cover_url(), "")

    def test_rounds_dimensions_to_int(self):
        url = self._video("https://i0.hdslb.com/a.jpg").cover_url(260.7, 146.2)
        self.assertIn("@260w_146h_1c.png", url)


class FolderTests(unittest.TestCase):
    def test_parses(self):
        folder = Folder.from_api({"id": 12345678, "title": "默认收藏夹", "media_count": 23, "attr": 0})
        assert folder is not None
        self.assertEqual(folder.fid, 12345678)
        self.assertTrue(folder.is_public)

    def test_private_flag(self):
        folder = Folder.from_api({"id": 1, "title": "私密", "media_count": 2, "attr": 1})
        self.assertFalse(folder.is_public)

    def test_accepts_media_id_key(self):
        folder = Folder.from_api({"media_id": 5, "title": "t", "media_count": 0})
        self.assertEqual(folder.fid, 5)

    def test_rejects_missing_id(self):
        self.assertIsNone(Folder.from_api({"title": "t"}))

    def test_dict_round_trip(self):
        original = Folder(fid=1, title="t", media_count=3, attr=0)
        self.assertEqual(original, Folder.from_dict(original.to_dict()))


class PageTests(unittest.TestCase):
    def test_drops_husks_and_reads_total(self):
        page = Page.from_api(
            {
                "info": {"media_count": 99},
                "has_more": True,
                "medias": [
                    {"id": 1, "bvid": "BV1", "title": "a"},
                    {"id": 0, "bvid": "", "title": "空壳"},
                    {"id": 2, "bvid": "BV2", "title": "b"},
                ],
            },
            page=2,
        )
        self.assertEqual(len(page.videos), 2)
        self.assertEqual(page.page, 2)
        self.assertTrue(page.has_more)
        self.assertEqual(page.total, 99)

    def test_empty_payload(self):
        page = Page.from_api({}, page=1)
        self.assertEqual(page.videos, ())
        self.assertFalse(page.has_more)
        self.assertEqual(page.total, 0)


class LoginStateTests(unittest.TestCase):
    def test_logged_in(self):
        state = LoginState.from_nav_data({"isLogin": True, "uname": "名字", "mid": 42})
        self.assertTrue(state.is_login)
        self.assertEqual(state.uname, "名字")
        self.assertEqual(state.mid, 42)

    def test_anonymous(self):
        state = LoginState.from_nav_data({"isLogin": False})
        self.assertFalse(state.is_login)
        self.assertIsNone(state.uname)

    def test_none(self):
        self.assertFalse(LoginState.from_nav_data(None).is_login)


if __name__ == "__main__":
    unittest.main()
