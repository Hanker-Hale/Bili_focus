"""播放器启动失败时的重试与清理测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bilifocus import player as player_mod
from bilifocus import winproc
from bilifocus.player import BrowserPlayer, PlaybackError, PlaybackResult


class FakeProcess:
    pid = 4321

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


class FakeCdp:
    def __init__(self, ws_url, timeout=25.0):
        self.ws_url = ws_url
        self.calls = []

    def call(self, method, params=None):
        self.calls.append(method)
        return {}

    def close(self):
        pass


class RetryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.profile = Path(self._tmp.name) / "player-profile"
        self.player = BrowserPlayer(
            self.profile, browser_path="fake.exe", start_fullscreen=False, poll_interval=0.01
        )

    def _patch(self, track):
        self.player._cleanup_stale = mock.Mock(return_value=[])
        self.player._wait_target = mock.Mock(return_value="ws://fake")
        self.player._prepare = mock.Mock()
        self.player._track = mock.Mock(side_effect=track)
        patches = [
            mock.patch.object(player_mod, "_Cdp", FakeCdp),
            mock.patch.object(player_mod.subprocess, "Popen", return_value=FakeProcess()),
            mock.patch.object(player_mod, "_stale_instances", return_value=[]),
            mock.patch.object(player_mod, "taskkill"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_retries_once_when_first_launch_exits(self):
        results = [
            PlaybackResult(position=0, duration=0, watched_seconds=0, samples=0),
            PlaybackResult(position=120, duration=600, watched_seconds=118, samples=12),
        ]
        self._patch(lambda cdp, on_sample=None: results.pop(0))

        result = self.player.play("https://www.bilibili.com/video/BV1xx")

        self.assertEqual(result.samples, 12)
        self.assertEqual(self.player._track.call_count, 2)
        self.assertEqual(self.player._cleanup_stale.call_count, 2)

    def test_raises_after_two_failures(self):
        self._patch(
            lambda cdp, on_sample=None: PlaybackResult(
                position=0, duration=0, watched_seconds=0, samples=0
            )
        )
        with self.assertRaises(PlaybackError):
            self.player.play("https://www.bilibili.com/video/BV1xx")
        self.assertEqual(self.player._track.call_count, 2)

    def test_empty_url_still_raises(self):
        with self.assertRaises(PlaybackError):
            self.player.play("")

    def test_cleanup_waits_for_processes_to_die(self):
        waited = []
        with mock.patch.object(player_mod, "_stale_instances", return_value=[111, 222]):
            with mock.patch.object(player_mod, "taskkill") as kill:
                with mock.patch.object(
                    player_mod, "wait_pid_gone", side_effect=lambda pid, timeout=3.0: waited.append(pid)
                ):
                    pids = self.player._cleanup_stale(wait=True)
        self.assertEqual(pids, [111, 222])
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(waited, [111, 222])

    def test_player_writes_log(self):
        self._patch(
            lambda cdp, on_sample=None: PlaybackResult(
                position=10, duration=100, watched_seconds=10, samples=3
            )
        )
        self.player.play("https://www.bilibili.com/video/BV1xx")
        log = Path(self._tmp.name) / "player.log"
        self.assertTrue(log.is_file())
        self.assertIn("启动浏览器", log.read_text(encoding="utf-8"))


class WaitPidTests(unittest.TestCase):
    def test_missing_pid_is_gone(self):
        self.assertTrue(winproc.wait_pid_gone(999999, timeout=0.2))

    def test_current_process_is_alive(self):
        import os

        self.assertFalse(winproc.wait_pid_gone(os.getpid(), timeout=0.2))


if __name__ == "__main__":
    unittest.main()
