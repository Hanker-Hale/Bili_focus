"""封面图:下载 → 校验是 PNG → 落到本地缓存。

两个关键决定:

1. **只要 PNG**。Tk 原生只解 PNG/GIF,解不了 JPEG;而 B 站图床支持用 URL 后缀
   在线转码成 PNG(`@320w_180h_1c.png`),所以不需要 Pillow。
2. **必须缓存到磁盘**。55 张封面每次重开都重新下载是浪费,而且列表滚动会卡。
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_REFERER = "https://www.bilibili.com/"


class CoverFetcher:
    """按 URL 取封面 PNG 字节,带磁盘缓存和内存缓存。"""

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        timeout: float = 10.0,
        max_age_seconds: float = 30 * 24 * 3600,
        download=None,
        clock=time.time,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_age_seconds = max_age_seconds
        self._download = download or self._http_get
        self._clock = clock
        self._memory: dict[str, bytes] = {}
        self.failures = 0

    # -- 网络 -------------------------------------------------------------
    def _http_get(self, url: str) -> bytes | None:
        request = urllib.request.Request(url)
        request.add_header("User-Agent", DEFAULT_USER_AGENT)
        request.add_header("Referer", DEFAULT_REFERER)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except (urllib.error.URLError, OSError, TimeoutError):
            return None

    # -- 缓存路径 ---------------------------------------------------------
    def path_for(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.png"

    def _fresh(self, path: Path) -> bool:
        try:
            age = self._clock() - path.stat().st_mtime
        except OSError:
            return False
        return age < self.max_age_seconds

    # -- 对外接口 ---------------------------------------------------------
    def fetch(self, url: str) -> bytes | None:
        """返回 PNG 字节;失败返回 None(界面该画占位图)。"""
        if not url:
            return None
        if url in self._memory:
            return self._memory[url]

        path = self.path_for(url)
        if path.exists() and self._fresh(path):
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if data.startswith(PNG_MAGIC):
                self._memory[url] = data
                return data
            # 文件坏了就删掉重下
            try:
                path.unlink()
            except OSError:
                pass

        data = self._download(url)
        if not data or not data.startswith(PNG_MAGIC):
            self.failures += 1
            return None

        self._memory[url] = data
        self._write_atomic(path, data)
        return data

    def _write_atomic(self, path: Path, data: bytes) -> None:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def get_cached(self, url: str) -> bytes | None:
        """只查缓存,绝不联网。界面先用它把已有封面立刻画出来。"""
        if not url:
            return None
        if url in self._memory:
            return self._memory[url]
        path = self.path_for(url)
        if not path.exists():
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if not data.startswith(PNG_MAGIC):
            return None
        self._memory[url] = data
        return data

    def prefetch(self, urls) -> int:
        """按顺序预热(给后台线程用,不接触界面)。返回成功张数。"""
        ok = 0
        for url in urls:
            if not url:
                continue
            if self.fetch(url):
                ok += 1
        return ok

    def clear(self) -> int:
        self._memory.clear()
        removed = 0
        for path in self.cache_dir.glob("*.png"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
