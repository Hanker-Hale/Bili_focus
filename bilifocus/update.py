"""检查更新 / 下载安装包 / 一键更新。

数据来源只有 GitHub 的公开 Release 信息,**不发送任何用户数据**:

1. 先问 GitHub API:`/repos/<repo>/releases/latest`;
2. API 不通(限流/被墙)就退到 Release 的 Atom 订阅,从标题/链接里取版本号;
3. 拿到版本号后,按固定命名规则拼出安装包下载地址
   (`releases/download/v<版本>/BiliFocus-Setup-<版本>.exe`)。

只做"发现自己有新版本 → 引导下载",不做后台静默替换程序。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import install, winproc

REPO = os.environ.get("BILI_FOCUS_REPO", "Hanker-Hale/Bili_focus")
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ATOM_FEED = f"https://github.com/{REPO}/releases.atom"
PAGE_LATEST = f"https://github.com/{REPO}/releases/latest"
USER_AGENT = "BiliFocus-update-check"
INSTALLER_PREFIX = "BiliFocus-Setup-"
MIN_INSTALLER_BYTES = 5 * 1024 * 1024  # 安装包 20MB+,小于这个肯定不对


@dataclass
class ReleaseInfo:
    version: str          # "2.2.0"
    tag: str              # "v2.2.0"
    installer_url: str    # 安装包地址(可能为空)
    portable_url: str     # 绿色版地址(可能为空)
    page_url: str         # Release 页面
    notes: str = ""

    def summary(self) -> str:
        return f"{self.version} · {self.page_url}"


# -- 版本比较 --------------------------------------------------------------
def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """把 "v2.2.0" / "2.2" 解析成 (2,2,0);解析不了返回 None。"""
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text))
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def is_newer(latest: str | None, current: str | None) -> bool:
    new = parse_version(latest)
    old = parse_version(current)
    if new is None or old is None:
        return False
    return new > old


# -- 地址拼接 --------------------------------------------------------------
def installer_url_for(version: str) -> str:
    return f"https://github.com/{REPO}/releases/download/v{version}/{INSTALLER_PREFIX}{version}.exe"


def portable_url_for(version: str) -> str:
    return f"https://github.com/{REPO}/releases/download/v{version}/BiliFocus.exe"


# -- 取最新版本 ------------------------------------------------------------
def _http_get(url: str, timeout: float = 8.0) -> bytes | None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json, application/atom+xml;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _from_api(payload: bytes | None) -> ReleaseInfo | None:
    if not payload:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tag = str(data.get("tag_name") or "")
    version = ".".join(str(part) for part in (parse_version(tag) or ()))
    if not version:
        return None
    installer_url = ""
    portable_url = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not url:
            continue
        if name.startswith(INSTALLER_PREFIX) and name.lower().endswith(".exe"):
            installer_url = installer_url or url
        elif name.lower() == "bilifocus.exe":
            portable_url = portable_url or url
    return ReleaseInfo(
        version=version,
        tag=tag or f"v{version}",
        installer_url=installer_url or installer_url_for(version),
        portable_url=portable_url or portable_url_for(version),
        page_url=str(data.get("html_url") or PAGE_LATEST),
        notes=str(data.get("body") or "")[:400],
    )


def _from_atom(payload: bytes | None) -> ReleaseInfo | None:
    if not payload:
        return None
    try:
        text = payload.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return None
    entry = text.split("<entry", 1)
    if len(entry) < 2:
        return None
    first = entry[1]
    tags = re.findall(r"/releases/tag/([^\"<>\s]+)", first)
    version_source = next((tag for tag in tags if parse_version(tag)), None)
    if not version_source:
        # 有些 feed 的标题里才带版本号
        title = re.search(r"<title>([^<]+)</title>", first)
        version_source = title.group(1) if title else None
    version_tuple = parse_version(version_source)
    if not version_tuple:
        return None
    version = ".".join(str(part) for part in version_tuple)
    tag = version_source if str(version_source).startswith("v") else f"v{version}"
    return ReleaseInfo(
        version=version,
        tag=tag,
        installer_url=installer_url_for(version),
        portable_url=portable_url_for(version),
        page_url=f"https://github.com/{REPO}/releases/tag/{tag}",
    )


def latest_release(timeout: float = 8.0) -> ReleaseInfo | None:
    """最新 Release(没有 Release 或网络不通返回 None)。"""
    info = _from_api(_http_get(API_LATEST, timeout=timeout))
    if info is not None:
        return info
    return _from_atom(_http_get(ATOM_FEED, timeout=timeout))


def check_for_update(current: str, timeout: float = 8.0) -> tuple[ReleaseInfo | None, bool]:
    """返回 (最新版本信息, 是否比当前新)。"""
    info = latest_release(timeout=timeout)
    if info is None:
        return None, False
    return info, is_newer(info.version, current)


# -- 下载安装包 ------------------------------------------------------------
def download(
    url: str,
    *,
    dest_dir: Path | str | None = None,
    filename: str | None = None,
    progress=None,
    timeout: float = 120.0,
) -> Path:
    """下载到临时目录;progress(0~100) 会定期回调。"""
    folder = Path(dest_dir) if dest_dir else Path(tempfile.mkdtemp(prefix="bilifocus-update-"))
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (filename or "BiliFocus-Setup.exe")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        with target.open("wb") as handle:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                received += len(chunk)
                if progress and total:
                    progress(min(100, int(received * 100 / total)))
    if progress:
        progress(100)
    return target


def verify_installer(path: Path) -> bool:
    """粗校验:够大 + 是 exe(PE 头 MZ)。"""
    try:
        if path.stat().st_size < MIN_INSTALLER_BYTES:
            return False
        with path.open("rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False


def download_installer(info: ReleaseInfo, *, progress=None, dest_dir=None) -> Path:
    filename = f"{INSTALLER_PREFIX}{info.version}.exe"
    path = download(info.installer_url, dest_dir=dest_dir, filename=filename, progress=progress)
    if not verify_installer(path):
        raise OSError("下载到的安装包不完整,请稍后重试")
    return path


# -- 一键更新 --------------------------------------------------------------
def can_one_click() -> bool:
    """只有"已安装版"才适合一键更新;绿色版直接给下载页。"""
    return install.is_installed()


def launch_installer(installer: Path, *, relaunch: bool = True) -> bool:
    """启动安装包(等应用退出后安装),装完自动重新打开程序。"""
    script = Path(tempfile.gettempdir()) / f"bilifocus-update-{os.getpid()}.cmd"
    target = install.installed_exe()
    lines = [
        "@echo off",
        "ping -n 3 127.0.0.1 >nul",
        f'"{installer}" --silent',
        f'start "" "{target}"' if relaunch else "rem 不自动打开",
        'del "%~f0" >nul 2>nul',
    ]
    script.write_text("\r\n".join(lines) + "\r\n", encoding="gbk", errors="replace")
    try:
        winproc.run_detached(["cmd", "/c", str(script)])
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def describe_info(info: ReleaseInfo | None, current: str) -> str:
    """给 CLI/日志用的一行说明。"""
    if info is None:
        return "查不到最新版本(网络或仓库没有 Release)"
    if is_newer(info.version, current):
        return f"有新版本:{current} → {info.version}  {info.page_url}"
    return f"已是最新版本({current})"


__all__ = [
    "REPO",
    "ReleaseInfo",
    "parse_version",
    "is_newer",
    "latest_release",
    "check_for_update",
    "download",
    "download_installer",
    "verify_installer",
    "can_one_click",
    "launch_installer",
    "describe_info",
    "installer_url_for",
    "portable_url_for",
]
