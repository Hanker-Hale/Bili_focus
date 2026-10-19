"""用独立浏览器配置登录 B 站,并从**本软件自己的**会话里取 SESSDATA。

为什么不直接读你日常浏览器里的 Cookie:

- Chrome/Edge 从较新版本起对 Cookie 启用 App-Bound 加密,解密需要冒充浏览器
  进程,做法脆弱且属于逆向对抗;
- 更重要的是,读取别人的 Cookie 库在安全审计里和窃取凭据无法区分。

所以这里换一条干净的路:本软件启动一个带**独立 `user-data-dir`** 的浏览器窗口,
你在里面正常登录 B 站;登录态只属于这个软件,和你的日常浏览器互不影响。登录完成
后,软件通过 CDP(DevTools 协议)从这个会话里读 SESSDATA,然后关掉浏览器。

这套机制不依赖第三方库:WebSocket 客户端和 CDP 调用都在本文件内用标准库实现。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path

from .errors import BiliFocusError

LOGIN_URL = "https://www.bilibili.com/"
WEB_SOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class BrowserLoginError(BiliFocusError):
    """取登录态失败,消息面向用户。"""


# --------------------------------------------------------------------------
# 找浏览器
# --------------------------------------------------------------------------
_BROWSER_CANDIDATES = (
    ("Edge", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ("Edge", r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ("Chrome", r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ("Chrome", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    ("Chrome", r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
)


def find_browser() -> tuple[str, str]:
    """返回 (名称, 可执行文件路径)。"""
    for name, template in _BROWSER_CANDIDATES:
        path = Path(os.path.expandvars(template))
        if path.is_file():
            return name, str(path)
    raise BrowserLoginError("没找到 Edge 或 Chrome,无法用浏览器登录。请改用粘贴 SESSDATA。")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------
# 最小 WebSocket 客户端(RFC 6455,只做 CDP 需要的那部分)
# --------------------------------------------------------------------------
class _MiniWebSocket:
    def __init__(self, url: str, timeout: float = 15.0):
        if not url.startswith("ws://"):
            raise BrowserLoginError(f"不支持的 CDP 地址:{url}")
        rest = url[len("ws://"):]
        host_port, _, path = rest.partition("/")
        host, _, port_text = host_port.partition(":")
        self.host = host or "127.0.0.1"
        self.port = int(port_text or 80)
        self.path = "/" + path
        self._sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buffer = b""
        self._handshake()

    # -- 握手 -------------------------------------------------------------
    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode())
        head = self._read_until(b"\r\n\r\n", limit=16384)
        first_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in first_line.split(" ")[1:2]:
            raise BrowserLoginError(f"CDP 握手被拒绝:{first_line}")

    def _read_until(self, marker: bytes, *, limit: int) -> bytes:
        data = self._buffer
        while marker not in data:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise BrowserLoginError("CDP 连接在握手阶段被关闭")
            data += chunk
            if len(data) > limit:
                raise BrowserLoginError("CDP 握手响应异常(过长)")
        head, _, tail = data.partition(marker)
        self._buffer = tail
        return head + marker

    # -- 帧 ---------------------------------------------------------------
    @staticmethod
    def build_frame(payload: bytes, opcode: int = 0x1) -> bytes:
        """构造一个客户端帧(FIN=1 + 掩码,符合 RFC 6455 对客户端的要求)。"""
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return bytes(header) + masked

    def send_text(self, text: str) -> None:
        self._sock.sendall(self.build_frame(text.encode("utf-8"), opcode=0x1))

    def _read_frame(self) -> tuple[bool, int, bytes]:
        head = self._read_exact(2)
        fin = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise BrowserLoginError("CDP 连接已关闭")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def recv_text(self) -> str:
        """收一条完整的文本消息;自动回 ping、处理分片。"""
        parts: list[bytes] = []
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == 0x9:  # ping
                self._send_pong(payload)
                continue
            if opcode == 0x8:  # close
                raise BrowserLoginError("CDP 连接被浏览器关闭")
            parts.append(payload)
            if fin:
                break
        return b"".join(parts).decode("utf-8", "replace")

    def _send_pong(self, payload: bytes) -> None:
        header = bytearray([0x8A])
        header.append(0x80 | len(payload))
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def close(self) -> None:
        try:
            self._sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# 极简 CDP 客户端
# --------------------------------------------------------------------------
class _Cdp:
    def __init__(self, ws_url: str, timeout: float = 15.0):
        self._ws = _MiniWebSocket(ws_url, timeout=timeout)
        self._next_id = 0
        self._timeout = timeout

    def call(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._ws.send_text(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + self._timeout
        while True:
            if time.monotonic() > deadline:
                raise BrowserLoginError(f"CDP 调用超时:{method}")
            message = json.loads(self._ws.recv_text())
            if message.get("id") != request_id:
                continue  # 事件或别的响应,忽略
            if "error" in message:
                raise BrowserLoginError(f"CDP 返回错误({method}):{message['error'].get('message')}")
            return message.get("result") or {}

    def close(self) -> None:
        self._ws.close()


# --------------------------------------------------------------------------
# 登录流程
# --------------------------------------------------------------------------
def _http_json(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _stale_instances(profile_dir: Path) -> list[int]:
    """找出正在用我们这个 profile 的浏览器进程(只可能是本软件自己开的)。

    `wmic` 在新版 Windows 里已被移除,所以优先用 PowerShell 的 CIM 查询,
    失败再退回 wmic。只匹配命令行里带我们 profile 路径的进程,不会误杀
    用户自己日常用的浏览器。
    """
    needle = str(profile_dir).lower().replace("/", "\\")

    def matches(command_line: str) -> bool:
        return bool(command_line) and needle in command_line.lower().replace("/", "\\")

    # 1) PowerShell CIM(现代系统)
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" "
        "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=25,
        )
        payload = json.loads((completed.stdout or "").strip() or "[]")
        items = payload if isinstance(payload, list) else [payload]
        found = [
            int(item["ProcessId"])
            for item in items
            if isinstance(item, dict) and matches(item.get("CommandLine") or "")
        ]
        if found:
            return found
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass

    # 2) 退回 wmic(老系统)
    try:
        output = subprocess.run(
            ["wmic", "process", "where", "name='msedge.exe' or name='chrome.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    results: list[int] = []
    for line in output.splitlines():
        if not matches(line):
            continue
        for field in reversed(line.split(",")):
            field = field.strip()
            if field.isdigit():
                results.append(int(field))
                break
    return results


class BrowserLogin:
    """管理"开浏览器 → 等你登录 → 取 SESSDATA → 关浏览器"这个过程。"""

    def __init__(
        self,
        profile_dir: Path | str,
        *,
        browser_path: str | None = None,
        login_url: str = LOGIN_URL,
        timeout: float = 240.0,
        poll_interval: float = 1.2,
    ):
        self.profile_dir = Path(profile_dir)
        self.browser_path = browser_path
        self.login_url = login_url
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._process: subprocess.Popen | None = None
        self._cdp: _Cdp | None = None
        self.port = 0

    # -- 生命周期 ---------------------------------------------------------
    def _ensure_browser(self) -> str:
        if self.browser_path:
            return self.browser_path
        _name, path = find_browser()
        self.browser_path = path
        return path

    def start(self) -> None:
        path = self._ensure_browser()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # 上一次没收干净的实例会占着 profile,导致新实例不开调试端口
        for pid in _stale_instances(self.profile_dir):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)

        self.port = find_free_port()
        args = [
            path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--remote-allow-origins=*",
            self.login_url,
        ]
        self._process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        deadline = time.monotonic() + 30
        version: dict | None = None
        while time.monotonic() < deadline:
            try:
                version = _http_json(f"http://127.0.0.1:{self.port}/json/version")
                break
            except (OSError, ValueError):
                if self._process.poll() is not None:
                    raise BrowserLoginError("浏览器启动后立刻退出了,请手动粘贴 SESSDATA")
                time.sleep(0.4)
        if not version:
            raise BrowserLoginError("浏览器调试端口没有起来(可能被安全策略限制)")
        self._ws_url = version["webSocketDebuggerUrl"]

    def _connect(self) -> _Cdp:
        if self._cdp is None:
            self._cdp = _Cdp(getattr(self, "_ws_url"), timeout=20.0)
        return self._cdp

    def close(self) -> None:
        if self._cdp is not None:
            try:
                self._cdp.call("Browser.close")
            except BrowserLoginError:
                pass
            self._cdp.close()
            self._cdp = None
        if self._process is not None:
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            self._process = None

    def forget(self) -> None:
        """删掉这个 profile,等于退出登录并清掉本地会话。"""
        shutil.rmtree(self.profile_dir, ignore_errors=True)

    # -- 取 Cookie --------------------------------------------------------
    def _cookies(self) -> list[dict]:
        cdp = self._connect()
        try:
            return list(cdp.call("Storage.getCookies").get("cookies") or [])
        except BrowserLoginError:
            # 老版本 CDP 没有 Storage 域时的退路
            return list(
                cdp.call("Network.getCookies", {"urls": [self.login_url]}).get("cookies") or []
            )

    @staticmethod
    def extract_sessdata(cookies: list[dict]) -> str | None:
        for cookie in cookies:
            if cookie.get("name") != "SESSDATA":
                continue
            domain = str(cookie.get("domain") or "").lower()
            if domain in ("bilibili.com", ".bilibili.com") or domain.endswith(".bilibili.com"):
                value = cookie.get("value")
                if value:
                    return str(value)
        return None

    def capture(self, progress=None) -> str:
        """打开浏览器等你登录,拿到 SESSDATA 就返回并关闭浏览器。"""
        self.start()
        try:
            deadline = time.monotonic() + self.timeout
            last_note = ""
            while time.monotonic() < deadline:
                if self._process is not None and self._process.poll() is not None:
                    raise BrowserLoginError("浏览器窗口被关闭了,没有拿到登录态")
                try:
                    value = self.extract_sessdata(self._cookies())
                except BrowserLoginError as error:
                    # CDP 偶发断连不代表失败,继续等
                    last_note = str(error)
                    value = None
                if value:
                    return value
                if progress is not None:
                    progress("已打开浏览器,请在其中登录 B 站…")
                time.sleep(self.poll_interval)
            detail = f"(最后一次错误:{last_note})" if last_note else ""
            raise BrowserLoginError(f"等了 {int(self.timeout)} 秒仍未检测到登录{detail}")
        finally:
            self.close()
