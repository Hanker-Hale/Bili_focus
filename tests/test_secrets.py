"""凭证加密测试:DPAPI/退化 base64、旧明文迁移、坏数据不崩。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from favfocus import secrets
from favfocus.store import StateStore

SAMPLE = "abc%2C123%2Csuper-secret"


class ProtectTests(unittest.TestCase):
    def test_round_trip(self):
        cipher = secrets.protect(SAMPLE)
        self.assertIsNotNone(cipher)
        self.assertNotEqual(cipher, SAMPLE)
        self.assertEqual(secrets.unprotect(cipher), SAMPLE)

    def test_plaintext_not_visible(self):
        cipher = secrets.protect(SAMPLE)
        self.assertNotIn(SAMPLE, cipher)
        self.assertNotIn("super-secret", cipher)

    def test_prefix_and_flag(self):
        cipher = secrets.protect(SAMPLE)
        self.assertTrue(secrets.is_encrypted(cipher))
        self.assertTrue(
            cipher.startswith(secrets.PREFIX_DPAPI) or cipher.startswith(secrets.PREFIX_PLAIN)
        )
        self.assertFalse(secrets.is_encrypted(SAMPLE))
        self.assertFalse(secrets.is_encrypted(None))

    def test_empty_values(self):
        self.assertIsNone(secrets.protect(None))
        self.assertEqual(secrets.protect(""), "")
        self.assertIsNone(secrets.unprotect(None))
        self.assertEqual(secrets.unprotect(""), "")

    def test_legacy_plaintext_still_readable(self):
        self.assertEqual(secrets.unprotect(SAMPLE), SAMPLE)

    def test_corrupt_or_foreign_cipher_returns_none(self):
        self.assertIsNone(secrets.unprotect("dpapi:not-base64!"))
        self.assertIsNone(secrets.unprotect("dpapi:QUJD"))  # 合法 base64,但不是我们的密文

    def test_backend_reported(self):
        self.assertIn("DPAPI", secrets.backend() if sys.platform == "win32" else "base64")

    @unittest.skipUnless(sys.platform == "win32", "DPAPI 只在 Windows 上有")
    def test_windows_uses_dpapi(self):
        self.assertTrue(secrets.protect(SAMPLE).startswith(secrets.PREFIX_DPAPI))


class StoreCredentialTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _raw_state(self) -> str:
        return (self.home / "state.json").read_text(encoding="utf-8")

    def test_sessdata_is_encrypted_on_disk(self):
        store = StateStore(home=self.home)
        store.set_sessdata(SAMPLE)
        self.assertEqual(store.sessdata, SAMPLE)
        raw = self._raw_state()
        self.assertNotIn(SAMPLE, raw)
        self.assertNotIn("super-secret", raw)
        stored = json.loads(raw)["cookies"]["SESSDATA"]
        self.assertTrue(secrets.is_encrypted(stored))

    def test_cookies_property_returns_plaintext(self):
        """HTTP 请求用的 cookie 必须是明文,否则会被 B 站判成未登录。"""
        store = StateStore(home=self.home)
        store.set_sessdata(SAMPLE)
        self.assertEqual(store.cookies.get("SESSDATA"), SAMPLE)
        again = StateStore(home=self.home)
        self.assertEqual(again.cookies.get("SESSDATA"), SAMPLE)

    def test_cookies_property_after_legacy_migration(self):
        (self.home / "state.json").write_text(
            json.dumps({"schema": 3, "cookies": {"SESSDATA": SAMPLE}}), encoding="utf-8"
        )
        store = StateStore(home=self.home)
        self.assertEqual(store.cookies.get("SESSDATA"), SAMPLE)

    def test_undecryptable_cookie_is_dropped(self):
        (self.home / "state.json").write_text(
            json.dumps({"schema": 4, "cookies": {"SESSDATA": "dpapi:QUJD", "bili_jct": "x"}}),
            encoding="utf-8",
        )
        cookies = StateStore(home=self.home).cookies
        self.assertNotIn("SESSDATA", cookies)
        self.assertEqual(cookies.get("bili_jct"), "x")

    def test_legacy_plaintext_is_upgraded_on_load(self):
        (self.home / "state.json").write_text(
            json.dumps({"schema": 3, "cookies": {"SESSDATA": SAMPLE}}), encoding="utf-8"
        )
        store = StateStore(home=self.home)
        self.assertEqual(store.sessdata, SAMPLE)
        raw = self._raw_state()
        self.assertNotIn(SAMPLE, raw)
        self.assertTrue(secrets.is_encrypted(json.loads(raw)["cookies"]["SESSDATA"]))

    def test_unreadable_cipher_is_treated_as_logged_out(self):
        (self.home / "state.json").write_text(
            json.dumps({"schema": 4, "cookies": {"SESSDATA": "dpapi:QUJD"}}), encoding="utf-8"
        )
        store = StateStore(home=self.home)
        self.assertIsNone(store.sessdata)
        self.assertFalse(store.describe()["has_sessdata"])

    def test_clearing_sessdata(self):
        store = StateStore(home=self.home)
        store.set_sessdata(SAMPLE)
        store.set_sessdata(None)
        self.assertIsNone(store.sessdata)
        self.assertNotIn("SESSDATA", json.loads(self._raw_state())["cookies"])


if __name__ == "__main__":
    unittest.main()
