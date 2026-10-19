"""安装/卸载辅助逻辑测试(不碰真实注册表和快捷方式)。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bilifocus import install


class PathTests(unittest.TestCase):
    def test_install_dir_uses_localappdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Temp\Local"}):
            self.assertEqual(
                install.install_dir(), Path(r"C:\Temp\Local") / "Programs" / "BiliFocus"
            )

    def test_data_dir_uses_appdata(self):
        with mock.patch.dict(os.environ, {"APPDATA": r"C:\Temp\Roaming"}):
            self.assertEqual(install.data_dir(), Path(r"C:\Temp\Roaming") / "bili-focus")

    def test_shortcut_locations(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"USERPROFILE": home, "APPDATA": home}):
                self.assertEqual(
                    install.desktop_shortcut(), Path(home) / "Desktop" / "BiliFocus.lnk"
                )
                self.assertIn(
                    "Start Menu",
                    str(install.start_menu_shortcut()),
                )

    def test_version_matches_package(self):
        from bilifocus import __version__

        self.assertEqual(install.version(), __version__)

    def test_installed_detection(self):
        with tempfile.TemporaryDirectory() as local:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": local}):
                self.assertFalse(install.is_installed())
                target = install.install_dir()
                target.mkdir(parents=True)
                (target / install.EXE_NAME).write_bytes(b"fake")
                (target / "VERSION.txt").write_text("9.9.9", encoding="utf-8")
                self.assertTrue(install.is_installed())
                self.assertEqual(install.installed_version(), "9.9.9")

    def test_installed_version_missing(self):
        with tempfile.TemporaryDirectory() as local:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": local}):
                self.assertIsNone(install.installed_version())


class PayloadTests(unittest.TestCase):
    def test_installer_payload_resolution(self):
        """安装器在开发态应指向 dist/BiliFocus.exe。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "installer_app", Path(__file__).resolve().parent.parent / "dev" / "installer_app.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        payload = module.payload_path()
        self.assertEqual(payload.name, install.EXE_NAME)
        self.assertIn("dist", str(payload).lower())


class ShortcutScriptTests(unittest.TestCase):
    def test_powershell_quote_escapes_single_quotes(self):
        self.assertEqual(install._ps_quote("a'b"), "'a''b'")

    def test_uninstall_key_is_under_current_user(self):
        self.assertTrue(install.UNINSTALL_KEY.startswith("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"))


if __name__ == "__main__":
    unittest.main()
