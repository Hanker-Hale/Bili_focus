"""播放器测试:进度采样逻辑、启动参数、跟踪循环。

不启动真实浏览器:CDP 和进程都用替身。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from bilifocus.player import (
    SAMPLE_JS,
    BrowserPlayer,
    PlaybackError,
    PlaybackTracker,
)


class TrackerTests(unittest.TestCase):
    def test_normal_playback_accumulates_watch_time(self):
        tracker = PlaybackTracker()
        # 每 2 秒采一次,时间轴同步前进
        tracker.sample(0, 600, False, wall=100.0)
        tracker.sample(2, 600, False, wall=102.0)
        tracker.sample(4, 600, False, wall=104.0)
        result = tracker.result()
        self.assertEqual(result.position, 4)
        self.assertEqual(result.duration, 600)
        self.assertAlmostEqual(result.watched_seconds, 4.0, places=1)
        self.assertEqual(result.samples, 3)

    def test_paused_does_not_add_watch_time(self):
        tracker = PlaybackTracker()
        tracker.sample(10, 600, False, wall=100.0)
        tracker.sample(12, 600, False, wall=102.0)
        tracker.sample(12, 600, True, wall=150.0)      # 暂停很久
        tracker.sample(12, 600, True, wall=400.0)
        self.assertAlmostEqual(tracker.result().watched_seconds, 2.0, places=1)

    def test_seek_jump_is_not_counted_as_watching(self):
        tracker = PlaybackTracker()
        tracker.sample(10, 600, False, wall=100.0)
        tracker.sample(11, 600, False, wall=102.0)
        tracker.sample(300, 600, False, wall=104.0)    # 拖动进度条到 5 分钟
        # 只累计了前面正常播放的 2 秒;跳到 300 秒那段不算观看时长
        self.assertAlmostEqual(tracker.result().watched_seconds, 2.0, places=1)
        self.assertEqual(tracker.result().position, 300)

    def test_duration_zero_is_tolerated(self):
        tracker = PlaybackTracker()
        tracker.sample(0, 0, False, wall=100.0)
        result = tracker.result()
        self.assertEqual(result.duration, 0)
        self.assertEqual(result.ratio, 0.0)

    def test_ratio(self):
        tracker = PlaybackTracker()
        tracker.sample(150, 600, False, wall=100.0)
        self.assertAlmostEqual(tracker.result().ratio, 0.25)


class ArgsTests(unittest.TestCase):
    def player(self, **kwargs) -> BrowserPlayer:
        return BrowserPlayer("C:/tmp/player-profile", browser_path="C:/fake/msedge.exe", **kwargs)

    def test_args_include_debug_port_and_profile(self):
        args = self.player().build_args(9333)
        joined = " ".join(args)
        self.assertIn("--remote-debugging-port=9333", joined)
        self.assertIn(f"--user-data-dir={Path('C:/tmp/player-profile')}", joined)
        self.assertEqual(args[-1], "about:blank")

    def test_args_allow_autoplay_and_fullscreen(self):
        joined = " ".join(self.player().build_args(9333))
        self.assertIn("--autoplay-policy=no-user-gesture-required", joined)
        self.assertIn("--start-fullscreen", joined)

    def test_fullscreen_can_be_disabled(self):
        joined = " ".join(self.player(start_fullscreen=False).build_args(9333))
        self.assertNotIn("--start-fullscreen", joined)

    def test_sample_script_looks_at_video_element(self):
        self.assertIn("video", SAMPLE_JS)
        self.assertIn("currentTime", SAMPLE_JS)
        self.assertIn("fullscreenElement", SAMPLE_JS)


class FakeProcess:
    pid = 4321

    def __init__(self, alive_polls: int):
        self.remaining = alive_polls

    def poll(self):
        if self.remaining <= 0:
            return 0
        self.remaining -= 1
        return None

    def wait(self, timeout=None):
        return 0


class TrackLoopTests(unittest.TestCase):
    def player(self) -> BrowserPlayer:
        return BrowserPlayer(
            "C:/tmp/player-profile",
            browser_path="C:/fake/msedge.exe",
            poll_interval=0.0,
            timeout=5.0,
            video_wait=1.0,
        )

    def test_track_samples_until_window_closes(self):
        player = self.player()
        states = [
            {"t": 0, "d": 600, "paused": False, "fs": True},
            {"t": 2, "d": 600, "paused": False, "fs": True},
            {"t": 4, "d": 600, "paused": False, "fs": True},
        ]
        player._process = FakeProcess(alive_polls=len(states) + 1)
        player._evaluate = lambda _cdp, _js: states.pop(0) if states else None
        result = player._track(object())
        self.assertEqual(result.position, 4)
        self.assertEqual(result.duration, 600)
        self.assertGreaterEqual(result.samples, 3)

    def test_track_clicks_fullscreen_once(self):
        player = self.player()
        clicks = []
        player._click = lambda _cdp, x, y: clicks.append((x, y))
        states = [
            {"t": 0, "d": 600, "paused": False, "fs": False, "bx": 100, "by": 200},
            {"t": 2, "d": 600, "paused": False, "fs": True, "bx": 100, "by": 200},
            {"t": 4, "d": 600, "paused": False, "fs": True, "bx": 100, "by": 200},
        ]
        player._process = FakeProcess(alive_polls=len(states) + 1)
        player._evaluate = lambda _cdp, _js: states.pop(0) if states else None
        player._track(object())
        self.assertEqual(clicks, [(100, 200)])

    def test_track_raises_when_player_never_loads(self):
        player = self.player()
        player._process = FakeProcess(alive_polls=1000)
        player._evaluate = lambda _cdp, _js: None
        player.video_wait = -1  # 立刻判定超时
        with self.assertRaises(PlaybackError):
            player._track(object())

    def test_empty_url_raises(self):
        with self.assertRaises(PlaybackError):
            self.player().play("")


if __name__ == "__main__":
    unittest.main()
