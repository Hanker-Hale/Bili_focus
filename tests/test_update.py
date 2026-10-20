"""检查更新/下载安装包/一键更新的逻辑测试(不联网)。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from favfocus import update


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(update.parse_version("v2.2.0"), (2, 2, 0))
        self.assertEqual(update.parse_version("2.10"), (2, 10, 0))
        self.assertEqual(update.parse_version("release 2.1.3 版"), (2, 1, 3))
        self.assertIsNone(update.parse_version("最新版本"))
        self.assertIsNone(update.parse_version(None))

    def test_is_newer(self):
        self.assertTrue(update.is_newer("2.2.0", "2.1.0"))
        self.assertTrue(update.is_newer("2.1.10", "2.1.9"))
        self.assertFalse(update.is_newer("2.1.0", "2.1.0"))
        self.assertFalse(update.is_newer("2.0.9", "2.1.0"))
        self.assertFalse(update.is_newer(None, "2.1.0"))

    def test_urls(self):
        self.assertIn("/releases/download/v2.2.0/", update.installer_url_for("2.2.0"))
        self.assertTrue(update.installer_url_for("2.2.0").endswith("FavFocus-Setup-2.2.0.exe"))
        self.assertTrue(update.portable_url_for("2.2.0").endswith("FavFocus.exe"))


class ReleaseParseTests(unittest.TestCase):
    def test_from_api_json(self):
        payload = json.dumps(
            {
                "tag_name": "v2.2.0",
                "html_url": "https://github.com/x/y/releases/tag/v2.2.0",
                "body": "本次更新:日程封面",
                "assets": [
                    {
                        "name": "FavFocus-Setup-2.2.0.exe",
                        "browser_download_url": "https://example.com/setup.exe",
                    },
                    {
                        "name": "FavFocus.exe",
                        "browser_download_url": "https://example.com/app.exe",
                    },
                ],
            }
        ).encode("utf-8")
        info = update._from_api(payload)
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "2.2.0")
        self.assertEqual(info.tag, "v2.2.0")
        self.assertEqual(info.installer_url, "https://example.com/setup.exe")
        self.assertEqual(info.portable_url, "https://example.com/app.exe")
        self.assertIn("日程封面", info.notes)

    def test_from_api_without_assets_falls_back_to_naming_rule(self):
        payload = json.dumps({"tag_name": "v2.2.0"}).encode("utf-8")
        info = update._from_api(payload)
        self.assertEqual(info.installer_url, update.installer_url_for("2.2.0"))

    def test_from_api_garbage(self):
        self.assertIsNone(update._from_api(b"not json"))
        self.assertIsNone(update._from_api(b"[]"))

    def test_from_atom(self):
        atom = b"""<?xml version="1.0"?><feed>
        <entry><id>tag:github.com,2008:Repository/1/v2.2.0</id>
        <link rel="alternate" href="https://github.com/x/y/releases/tag/v2.2.0"/>
        <title>v2.2.0</title></entry>
        <entry><id>tag:github.com,2008:Repository/1/v2.1.0</id></entry>
        </feed>"""
        info = update._from_atom(atom)
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "2.2.0")
        self.assertEqual(info.tag, "v2.2.0")

    def test_latest_release_falls_back_to_atom(self):
        atom = b"<feed><entry><title>v2.3.0</title><link href=\"/releases/tag/v2.3.0\"/></entry></feed>"

        def fake_get(url, timeout=8.0):
            return None if "api.github.com" in url else atom

        with mock.patch.object(update, "_http_get", side_effect=fake_get):
            info = update.latest_release()
        self.assertEqual(info.version, "2.3.0")

    def test_check_for_update(self):
        fake = update.ReleaseInfo("2.2.0", "v2.2.0", "i", "p", "page")
        with mock.patch.object(update, "latest_release", return_value=fake):
            info, newer = update.check_for_update("2.1.0")
        self.assertTrue(newer)
        self.assertEqual(info.version, "2.2.0")

        with mock.patch.object(update, "latest_release", return_value=None):
            info, newer = update.check_for_update("2.1.0")
        self.assertFalse(newer)

    def test_describe_info(self):
        self.assertIn("查不到", update.describe_info(None, "2.1.0"))
        self.assertIn("已是最新", update.describe_info(update.ReleaseInfo("2.1.0", "v2.1.0", "", "", ""), "2.1.0"))
        self.assertIn("2.2.0", update.describe_info(update.ReleaseInfo("2.2.0", "v2.2.0", "", "", "p"), "2.1.0"))


class DownloadTests(unittest.TestCase):
    def test_verify_installer(self):
        with mock.patch.object(update, "MIN_INSTALLER_BYTES", 4):
            path = Path(self._tmp_file(b"MZxx"))
            self.assertTrue(update.verify_installer(path))
            small = Path(self._tmp_file(b"M"))
            self.assertFalse(update.verify_installer(small))
            wrong = Path(self._tmp_file(b"PKxx"))
            self.assertFalse(update.verify_installer(wrong))

    def _tmp_file(self, data: bytes) -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".exe")
        handle.write(data)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_download_writes_file_and_reports_progress(self):
        class FakeResponse:
            headers = {"Content-Length": "6"}

            def __init__(self):
                self.chunks = [b"MZab", b"cd"]

            def read(self, size=0):
                return self.chunks.pop(0) if self.chunks else b""

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        percents = []
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            path = update.download(
                "https://example.com/x.exe", filename="setup.exe", progress=percents.append
            )
        self.assertEqual(path.read_bytes(), b"MZabcd")
        self.assertEqual(percents[-1], 100)


class OneClickTests(unittest.TestCase):
    def test_can_one_click_follows_install_state(self):
        with mock.patch.object(update.install, "is_installed", return_value=True):
            self.assertTrue(update.can_one_click())
        with mock.patch.object(update.install, "is_installed", return_value=False):
            self.assertFalse(update.can_one_click())

    def test_launch_installer_writes_script(self):
        with mock.patch.object(update.winproc, "run_detached") as detached:
            ok = update.launch_installer(Path(r"C:\Temp\FavFocus-Setup-2.2.0.exe"))
        self.assertTrue(ok)
        self.assertTrue(detached.called)
        script = Path(detached.call_args[0][0][-1])
        self.assertTrue(script.name.startswith("favfocus-update-"))
        script.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
