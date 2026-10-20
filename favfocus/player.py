"""播放器:用独立浏览器 + CDP 接管播放。

为什么必须自己接管浏览器,而不是 `webbrowser.open`:

1. **自动全屏** —— 浏览器不允许普通链接让页面全屏(需要"用户手势"),
   而通过 CDP 派发的鼠标事件是受信任的,可以点播放器的全屏按钮。
2. **记录进度** —— 只有能读到 `<video>.currentTime` / `duration`,
   才知道你看到了哪儿、看了多久。
3. **续播** —— 下次打开时用 `?t=秒` 从上次的位置开始。

实现上复用登录模块里已经写好的极简 CDP 客户端(浏览器调试协议),
不引入 selenium/playwright 这类重依赖。

浏览器用**独立 profile**(`player-profile`),不碰你日常浏览器的数据;
启动时把已保存的 SESSDATA 注进去,所以打开就是登录态。
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .browser_login import (
    BrowserLoginError,
    _Cdp,
    _http_json,
    _stale_instances,
    find_browser,
    find_free_port,
)
from .errors import FavFocusError
from .winproc import taskkill, wait_pid_gone

# 采样间隔:2 秒足够算准观看时长,又不会把 CDP 打满
POLL_INTERVAL = 2.0
# 最长允许播放多久(防止用户忘了关,记录一直挂在那儿)
PLAYER_TIMEOUT = 6 * 3600.0
# 打开页面后等播放器出现的上限
VIDEO_WAIT_SECONDS = 90.0

# 取播放器状态。选择器覆盖新版 bpx 播放器和旧版播放器。
SAMPLE_JS = r"""
(function () {
  var v = document.querySelector('video');
  if (!v) return null;
  var btn = document.querySelector('.bpx-player-ctrl-full')
         || document.querySelector('.bilibili-player-video-btn-fullscreen')
         || document.querySelector('[class*="ctrl-full"]');
  var rect = btn ? btn.getBoundingClientRect() : null;
  return {
    t: v.currentTime || 0,
    d: v.duration || 0,
    paused: !!v.paused,
    fs: !!document.fullscreenElement,
    bx: rect ? rect.left + rect.width / 2 : -1,
    by: rect ? rect.top + rect.height / 2 : -1
  };
})()
"""


class PlaybackError(FavFocusError):
    """播放过程中的可预期失败。"""


class _ConnectionLost(Exception):
    """CDP 连接断了 —— 多数是用户关掉了窗口,属于正常结束而不是错误。"""


@dataclass(frozen=True)
class PlaybackResult:
    position: float
    duration: float
    watched_seconds: float
    samples: int = 0

    @property
    def ratio(self) -> float:
        return min(1.0, self.position / self.duration) if self.duration > 0 else 0.0


class PlaybackTracker:
    """把播放器的采样变成"看到哪儿、看了多久"。

    单独拎出来是因为这部分是纯逻辑:不碰浏览器、不碰网络,可以直接测。
    """

    def __init__(self, *, jump_tolerance: float = 15.0):
        self.position = 0.0
        self.duration = 0.0
        self.watched = 0.0
        self.samples = 0
        self.jump_tolerance = jump_tolerance
        self._last_time: float | None = None
        self._last_wall: float | None = None

    def sample(
        self,
        current_time: float,
        duration: float,
        paused: bool,
        *,
        wall: float | None = None,
    ) -> None:
        now = time.monotonic() if wall is None else wall
        self.samples += 1
        if duration and duration > 0:
            self.duration = float(duration)
        if paused:
            # 暂停期间不算观看时长,而且要断开累计,避免暂停很久后被算进去
            self._last_time = None
            self._last_wall = None
            return
        current = max(0.0, float(current_time or 0))
        if self._last_time is not None and self._last_wall is not None:
            elapsed = now - self._last_wall
            advanced = current - self._last_time
            # 正常播放时时间轴和真实时间同步前进(允许倍速和一点抖动)。
            # 拖动进度条会造成大幅跳变,那部分不该算进观看时长。
            if 0 <= elapsed <= 30 and 0 < advanced <= max(1.0, elapsed * 3 + 2):
                self.watched += min(elapsed, advanced + 1.0)
        self._last_time = current
        self._last_wall = now
        self.position = current

    def result(self) -> PlaybackResult:
        return PlaybackResult(
            position=round(self.position, 2),
            duration=round(self.duration, 2),
            watched_seconds=round(self.watched, 2),
            samples=self.samples,
        )


class BrowserPlayer:
    """开一个全屏播放器窗口,盯着播放进度,关掉后返回结果。"""

    def __init__(
        self,
        profile_dir: Path | str,
        *,
        browser_path: str | None = None,
        poll_interval: float = POLL_INTERVAL,
        timeout: float = PLAYER_TIMEOUT,
        video_wait: float = VIDEO_WAIT_SECONDS,
        start_fullscreen: bool = True,
    ):
        self.profile_dir = Path(profile_dir)
        self.browser_path = browser_path
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.video_wait = video_wait
        self.start_fullscreen = start_fullscreen
        self.port = 0
        self._process: subprocess.Popen | None = None
        self._browser_pids: list[int] = []

    # -- 启动参数(可测)--------------------------------------------------
    def _ensure_browser(self) -> str:
        if self.browser_path:
            return self.browser_path
        _name, path = find_browser()
        self.browser_path = path
        return path

    def build_args(self, port: int) -> list[str]:
        path = self._ensure_browser()
        args = [
            path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--remote-allow-origins=*",
            # 允许有声自动播放,否则打开后还要手动点一下
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=Translate,MediaRouter",
            "about:blank",
        ]
        if self.start_fullscreen:
            args.insert(-1, "--start-fullscreen")
        return args

    # -- 主流程 -----------------------------------------------------------
    def _log(self, message: str) -> None:
        """播放器自己的日志:出问题时有据可查(不写敏感信息)。"""
        try:
            path = self.profile_dir.parent / "player.log"
            if path.exists() and path.stat().st_size > 200_000:
                path.write_text("", encoding="utf-8")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {message}\n")
        except OSError:
            pass

    def _cleanup_stale(self, *, wait: bool = False) -> list[int]:
        """清理上次残留的播放器浏览器;wait=True 时还会等它们真的退出。"""
        try:
            pids = _stale_instances(self.profile_dir)
        except Exception as error:  # noqa: BLE001 - 清理失败不该阻断播放
            self._log(f"查残留进程失败:{error}")
            return []
        if not pids:
            return []
        self._log(f"清理残留浏览器:{pids}")
        for pid in pids:
            taskkill(pid)
        if wait:
            for pid in pids:
                wait_pid_gone(pid, timeout=3.0)
            # 再确认一遍,避免"刚杀完又冒出来一个新进程"
            time.sleep(0.2)
        return pids

    def play(
        self,
        url: str,
        *,
        sessdata: str | None = None,
        on_sample: Callable[[PlaybackTracker], None] | None = None,
    ) -> PlaybackResult:
        if not url:
            raise PlaybackError("这条视频没有可打开的地址")
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for attempt in (1, 2):
            cdp: _Cdp | None = None
            started = time.monotonic()
            try:
                self._cleanup_stale(wait=True)
                self.port = find_free_port()
                self._log(f"第 {attempt} 次启动浏览器,调试端口 {self.port}")
                self._process = subprocess.Popen(
                    self.build_args(self.port),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                target_ws = self._wait_target()
                cdp = _Cdp(target_ws, timeout=25.0)
                self._prepare(cdp, sessdata)
                cdp.call("Page.navigate", {"url": url})
                result = self._track(cdp, on_sample)
                # 浏览器一启动就退出(常见于旧实例占着 profile)会出现 0 采样,
                # 这种情况自动重来一次,而不是让用户看到"视频刚开就没了"。
                if result.samples == 0 and time.monotonic() - started < 8:
                    raise PlaybackError("播放器打开后立刻退出了")
                self._log(f"第 {attempt} 次播放结束:采样 {result.samples}")
                return result
            except _ConnectionLost as error:
                last_error = error
                self._log(f"第 {attempt} 次连接断开:{error}")
            except PlaybackError as error:
                last_error = error
                self._log(f"第 {attempt} 次启动失败:{error}")
            finally:
                if cdp is not None:
                    try:
                        cdp.close()
                    except Exception:  # noqa: BLE001 - 关连接失败不影响结果
                        pass
                self._shutdown()
            if attempt == 2:
                break
            self._log("准备重试一次…")
            time.sleep(0.4)

        raise PlaybackError(f"播放器反复启动失败:{last_error}")

    # -- CDP 细节 ---------------------------------------------------------
    def _wait_target(self) -> str:
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise PlaybackError("播放器窗口打开后立刻退出了")
            try:
                payload = _http_json(f"http://127.0.0.1:{self.port}/json/list")
            except (OSError, ValueError) as error:
                last_error = error
                time.sleep(0.4)
                continue
            for target in payload if isinstance(payload, list) else []:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return str(target["webSocketDebuggerUrl"])
            time.sleep(0.4)
        raise PlaybackError(f"没连上播放器调试端口:{last_error}")

    def _prepare(self, cdp: _Cdp, sessdata: str | None) -> None:
        """把登录态注入播放器 profile,避免打开是未登录状态。"""
        try:
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
        except BrowserLoginError:
            pass
        if not sessdata:
            return
        try:
            cdp.call("Network.enable")
            cdp.call(
                "Network.setCookie",
                {
                    "name": "SESSDATA",
                    "value": sessdata,
                    "domain": ".bilibili.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                },
            )
        except BrowserLoginError:
            pass  # 注入失败就匿名播放,不阻断流程

    def _evaluate(self, cdp: _Cdp, expression: str):
        """执行 JS。连接断了会抛 _ConnectionLost,由调用方决定怎么收场。"""
        try:
            result = cdp.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": False},
            )
        except (BrowserLoginError, OSError, ValueError) as error:
            raise _ConnectionLost(str(error)) from error
        return (result.get("result") or {}).get("value")

    def _click(self, cdp: _Cdp, x: float, y: float) -> None:
        """派发一次受信任的鼠标点击(只有它能让浏览器进全屏)。"""
        for event_type in ("mousePressed", "mouseReleased"):
            try:
                cdp.call(
                    "Input.dispatchMouseEvent",
                    {
                        "type": event_type,
                        "x": float(x),
                        "y": float(y),
                        "button": "left",
                        "clickCount": 1,
                    },
                )
            except (BrowserLoginError, OSError, ValueError):
                return

    def _reconnect(self, cdp: _Cdp) -> _Cdp:
        """CDP 断了之后重连到当前的页面目标。"""
        try:
            cdp.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            ws_url = self._wait_target()
        except PlaybackError as error:
            raise _ConnectionLost(str(error)) from error
        return _Cdp(ws_url, timeout=25.0)

    def _track(
        self,
        cdp: _Cdp,
        on_sample: Callable[[PlaybackTracker], None] | None = None,
    ) -> PlaybackResult:
        tracker = PlaybackTracker()
        started = time.monotonic()
        deadline = started + self.timeout
        fullscreen_clicked = False

        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                break  # 用户关掉了窗口
            try:
                state = self._evaluate(cdp, SAMPLE_JS)
            except _ConnectionLost:
                # 关窗、崩溃、或页面换了目标都会走到这里,先试着重连一次
                try:
                    cdp = self._reconnect(cdp)
                except _ConnectionLost:
                    break  # 连不回来 = 窗口真的没了,带着已有进度收场
                continue
            if not isinstance(state, dict):
                if tracker.samples == 0 and time.monotonic() - started > self.video_wait:
                    raise PlaybackError("等不到播放器加载(页面可能没打开)")
                time.sleep(self.poll_interval)
                continue
            tracker.sample(state.get("t") or 0, state.get("d") or 0, bool(state.get("paused")))
            if on_sample is not None:
                try:
                    on_sample(tracker)
                except Exception:  # noqa: BLE001 - 回调出错不该中断播放
                    pass
            if self.start_fullscreen and not fullscreen_clicked and not state.get("fs"):
                x, y = state.get("bx", -1), state.get("by", -1)
                if x and y and x > 0 and y > 0:
                    self._click(cdp, x, y)
                    fullscreen_clicked = True
            time.sleep(self.poll_interval)

        return tracker.result()

    # -- 收尾 -------------------------------------------------------------
    def _shutdown(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is None:
            taskkill(process.pid)
        # 启动器退出后浏览器可能还活着(多进程架构),按 profile 兜底清一遍
        pids = list(self._browser_pids or [])
        if not pids:
            pids = _stale_instances(self.profile_dir)
        for pid in pids:
            taskkill(pid)
        self._browser_pids = []
