"""传输层:一个最小的 GET 抽象,便于注入假实现做测试。

为什么不让 client 直接用 urllib:底层要能被单测覆盖,而单元测试不该打真实
网络。`UrllibTransport` 是唯一的联网实现,`favfocus.client` 只依赖
`Transport` 协议。
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from .errors import NetworkError

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_REFERER = "https://www.bilibili.com/"

# 两次请求之间的最小间隔。B 站对密集请求敏感,0.34s 大约每秒 3 次。
DEFAULT_MIN_INTERVAL = 0.34
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 0.6


@dataclass(frozen=True)
class HttpResponse:
    """传输层只回状态码和正文,不做 JSON 解析。"""

    status: int
    body: str
    url: str = ""


@runtime_checkable
class Transport(Protocol):
    """一次 HTTP GET。实现方负责 UA/Referer 之外的传输细节。"""

    def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float | None = None
    ) -> HttpResponse: ...


class UrllibTransport:
    """标准库实现:限速 + 对网络错误和 5xx 做指数退避重试。"""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        referer: str = DEFAULT_REFERER,
        timeout: float = 15.0,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL,
        sleep=None,
    ):
        self.user_agent = user_agent
        self.referer = referer
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._sleep = sleep or _default_sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    # -- 限速 -------------------------------------------------------------
    def _wait_turn(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = _monotonic()
            wait = self._next_allowed - now
            self._next_allowed = max(now, self._next_allowed) + self.min_interval_seconds
        if wait > 0:
            self._sleep(wait)

    # -- 请求 -------------------------------------------------------------
    def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float | None = None
    ) -> HttpResponse:
        merged = {"User-Agent": self.user_agent, "Referer": self.referer}
        if headers:
            merged.update({key: value for key, value in headers.items() if value})
        attempt = 0
        while True:
            self._wait_turn()
            request = urllib.request.Request(url)
            for key, value in merged.items():
                request.add_header(key, value)
            try:
                with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                    body = response.read().decode("utf-8", "replace")
                    return HttpResponse(status=int(getattr(response, "status", 200)), body=body, url=url)
            except urllib.error.HTTPError as error:
                try:
                    body = error.read().decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 - 读取错误正文本身失败不该掩盖状态码
                    body = ""
                # 5xx 才重试;4xx 是调用方的问题,重试没有意义。
                if error.code >= 500 and attempt < self.retries:
                    attempt += 1
                    self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))
                    continue
                return HttpResponse(status=int(error.code), body=body, url=url)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                reason = getattr(error, "reason", error)
                if attempt < self.retries:
                    attempt += 1
                    self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))
                    continue
                raise NetworkError(f"网络请求失败:{reason}", url=url) from error


def _monotonic() -> float:
    return time.monotonic()


def _default_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
