"""外部命令必须"静默执行",不能弹控制台窗口。"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from bilifocus import winproc


class RunHiddenTests(unittest.TestCase):
    def test_runs_command_and_returns_output(self):
        result = winproc.run_hidden(["cmd", "/c", "echo keep-focus"], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "keep-focus")

    def test_sets_no_window_flag(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch("bilifocus.winproc.subprocess.run", side_effect=fake_run):
            winproc.run_hidden(["cmd", "/c", "echo hi"])

        self.assertTrue(captured.get("creationflags", 0) & winproc.CREATE_NO_WINDOW)
        if winproc.sys.platform == "win32":
            self.assertIsNotNone(captured.get("startupinfo"))

    def test_taskkill_on_missing_pid_does_not_raise(self):
        winproc.taskkill(999999, timeout=5)  # 不存在的 pid:静默忽略

    def test_run_detached_sets_flags(self):
        captured = {}

        def fake_popen(command, **kwargs):
            captured.update(kwargs)
            return mock.Mock()

        with mock.patch("bilifocus.winproc.subprocess.Popen", side_effect=fake_popen):
            winproc.run_detached(["cmd", "/c", "exit"])

        flags = captured.get("creationflags", 0)
        self.assertTrue(flags & winproc.DETACHED_PROCESS)
        self.assertTrue(flags & winproc.CREATE_NO_WINDOW)


class NoVisibleWindowTests(unittest.TestCase):
    def test_player_uses_hidden_helpers(self):
        """播放器/登录模块里不该再直接调用 subprocess.run(会弹窗)。"""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "bilifocus"
        for name in ("player.py", "browser_login.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("subprocess.run(", text, f"{name} 里还有没隐藏的 subprocess.run")

    def test_stale_instances_goes_through_run_hidden(self):
        from bilifocus import browser_login

        calls = []

        def fake_run_hidden(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "[]", "")

        with mock.patch.object(browser_login, "run_hidden", side_effect=fake_run_hidden):
            result = browser_login._stale_instances(object())

        self.assertEqual(result, [])
        self.assertTrue(calls)
        self.assertIn("powershell", calls[0][0][0])


if __name__ == "__main__":
    unittest.main()
