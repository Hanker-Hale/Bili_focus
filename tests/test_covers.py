"""封面下载与缓存测试:不联网,用注入的 download 函数。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bilifocus.covers import PNG_MAGIC, CoverFetcher

FAKE_PNG = PNG_MAGIC + b"fake-image-body"


class FetchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.calls: list[str] = []

    def fetcher(self, bodies: dict[str, bytes | None], **kwargs):
        def download(url: str) -> bytes | None:
            self.calls.append(url)
            return bodies.get(url)

        return CoverFetcher(self.home, download=download, **kwargs)

    def test_downloads_and_caches(self):
        url = "https://i0.hdslb.com/a.jpg@260w_146h_1c.png"
        fetcher = self.fetcher({url: FAKE_PNG})
        first = fetcher.fetch(url)
        second = fetcher.fetch(url)
        self.assertEqual(first, FAKE_PNG)
        self.assertEqual(second, FAKE_PNG)
        self.assertEqual(len(self.calls), 1, "第二次应该命中内存缓存")

    def test_disk_cache_survives_new_instance(self):
        url = "https://i0.hdslb.com/a.png"
        self.fetcher({url: FAKE_PNG}).fetch(url)
        self.calls.clear()
        second = CoverFetcher(self.home, download=lambda u: self.calls.append(u))
        self.assertEqual(second.fetch(url), FAKE_PNG)
        self.assertEqual(self.calls, [], "新实例应该直接读磁盘缓存")

    def test_cache_expires(self):
        import os

        url = "https://i0.hdslb.com/a.png"
        now = [1_700_000_000.0]
        fetcher = CoverFetcher(
            self.home,
            download=lambda u: (self.calls.append(u), FAKE_PNG)[1],
            max_age_seconds=10,
            clock=lambda: now[0],
        )
        fetcher.fetch(url)
        # 文件的 mtime 是真实时间,这里对齐到假时钟,否则"年龄"算出来是负数
        os.utime(fetcher.path_for(url), (now[0], now[0]))
        now[0] += 11
        fetcher._memory.clear()
        fetcher.fetch(url)
        self.assertEqual(len(self.calls), 2, "超过有效期要重新下载")

    def test_rejects_non_png(self):
        url = "https://i0.hdslb.com/a.png"
        fetcher = self.fetcher({url: b"\xff\xd8JPEG-NOT-ALLOWED"})
        self.assertIsNone(fetcher.fetch(url))
        self.assertEqual(fetcher.failures, 1)

    def test_download_failure_returns_none(self):
        fetcher = self.fetcher({})
        self.assertIsNone(fetcher.fetch("https://i0.hdslb.com/missing.png"))

    def test_empty_url_short_circuits(self):
        fetcher = self.fetcher({})
        self.assertIsNone(fetcher.fetch(""))
        self.assertEqual(self.calls, [])

    def test_corrupt_cache_is_refetched(self):
        url = "https://i0.hdslb.com/a.png"
        fetcher = self.fetcher({url: FAKE_PNG})
        path = fetcher.path_for(url)
        path.write_bytes(b"garbage")
        self.assertEqual(fetcher.fetch(url), FAKE_PNG)
        self.assertTrue(path.read_bytes().startswith(PNG_MAGIC))

    def test_prefetch_counts_successes(self):
        good = "https://i0.hdslb.com/1.png"
        bad = "https://i0.hdslb.com/2.png"
        fetcher = self.fetcher({good: FAKE_PNG})
        self.assertEqual(fetcher.prefetch([good, bad, ""]), 1)

    def test_clear_removes_files(self):
        url = "https://i0.hdslb.com/a.png"
        fetcher = self.fetcher({url: FAKE_PNG})
        fetcher.fetch(url)
        self.assertEqual(fetcher.clear(), 1)
        self.assertEqual(fetcher.fetch(url), FAKE_PNG, "清掉后会重新下载")

    def test_cache_key_is_url_specific(self):
        a = "https://i0.hdslb.com/a.png@260w_146h_1c.png"
        b = "https://i0.hdslb.com/a.png@320w_180h_1c.png"
        fetcher = self.fetcher({a: FAKE_PNG, b: PNG_MAGIC + b"other"})
        self.assertNotEqual(fetcher.path_for(a), fetcher.path_for(b))
        self.assertNotEqual(fetcher.fetch(a), fetcher.fetch(b))


if __name__ == "__main__":
    unittest.main()
