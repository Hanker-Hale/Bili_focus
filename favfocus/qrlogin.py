"""B 站扫码登录:生成二维码 → 手机扫码 → 轮询状态 → 拿 SESSDATA。

B 站手机客户端扫码登录用的是网页版通行证的两步接口:

1. `GET /x/passport-login/web/qrcode/generate`
   → `{url, qrcode_key}`,`url` 就是要编成二维码给手机扫的内容。
2. `GET /x/passport-login/web/qrcode/poll?qrcode_key=...`
   → `data.code` 表示状态:
      `86101` 未扫码 / `86090` 已扫码待确认 / `86038` 已失效 / `0` 成功
   成功时 `data.url` 里带着 Cookie 参数(`SESSDATA`、`bili_jct`、`DedeUserID`),
   从里面取出 SESSDATA 就完成了登录。

这两个接口都是匿名可访问的,不需要 WBI 签名,也不携带任何已有凭证。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .errors import FavFocusError, NetworkError, InvalidResponseError
from .vendor import qrcodegen

GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_REFERER = "https://passport.bilibili.com/"

# 轮询接口的 data.code
STATUS_WAITING = "waiting"    # 86101 还没扫
STATUS_SCANNED = "scanned"    # 86090 扫了但没点确认
STATUS_EXPIRED = "expired"    # 86038 二维码过期
STATUS_SUCCESS = "success"    # 0    登录成功

_CODE_TO_STATUS = {
    86101: STATUS_WAITING,
    86090: STATUS_SCANNED,
    86038: STATUS_EXPIRED,
    0: STATUS_SUCCESS,
}

_STATUS_TEXT = {
    STATUS_WAITING: "请用 B 站手机客户端扫码",
    STATUS_SCANNED: "已扫码,请在手机上点「确认登录」",
    STATUS_EXPIRED: "二维码已失效,正在刷新…",
    STATUS_SUCCESS: "登录成功",
}


@dataclass(frozen=True)
class QrTicket:
    """一次二维码会话。"""

    url: str
    key: str


@dataclass(frozen=True)
class QrPollResult:
    status: str
    message: str
    sessdata: str | None = None

    @property
    def done(self) -> bool:
        return self.status == STATUS_SUCCESS

    @property
    def expired(self) -> bool:
        return self.status == STATUS_EXPIRED


class QrLoginError(FavFocusError):
    """扫码登录过程中的可预期失败。"""


class QrLoginSession:
    """两接口的薄封装,不碰界面、不碰持久化。

    关于 SESSDATA 从哪来:web 端扫码成功时,B 站同时做两件事——
    在 JSON 的 `data.url` 里带上 Cookie 参数,并通过 `Set-Cookie` 响应头下发。
    不同账号/版本下这两者不一定都在,所以三个来源都要试:
    `data.cookie_info.cookies` → `data.url` 查询参数 → `Set-Cookie` 头。
    """

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        get_json: Callable[[str], dict] | None = None,
        http_get: Callable[[str], tuple[dict, dict[str, list[str]]]] | None = None,
    ):
        self.timeout = timeout
        self._get_json = get_json          # 测试注入:只返回 JSON
        self._http_get = http_get or self._http_get_with_headers

    # -- 网络 -------------------------------------------------------------
    def _http_get_with_headers(self, url: str) -> tuple[dict, dict[str, list[str]]]:
        request = urllib.request.Request(url)
        request.add_header("User-Agent", DEFAULT_USER_AGENT)
        request.add_header("Referer", DEFAULT_REFERER)
        request.add_header("Accept", "application/json, text/plain, */*")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                set_cookies: list[str] = []
                try:
                    set_cookies = list(response.headers.get_all("Set-Cookie") or [])
                except Exception:  # noqa: BLE001 - 拿不到头不影响主流程
                    set_cookies = []
        except urllib.error.HTTPError as error:
            raise NetworkError(f"HTTP {error.code}:{url}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NetworkError(f"网络失败:{error}", url=url) from error
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise InvalidResponseError(f"响应不是 JSON:{raw[:120]!r}") from error
        if not isinstance(payload, dict):
            raise InvalidResponseError("响应格式异常")
        return payload, {"set-cookie": set_cookies}

    def _fetch(self, url: str) -> tuple[dict, dict[str, list[str]]]:
        if self._get_json is not None:
            return self._get_json(url), {}
        return self._http_get(url)

    # -- 接口 -------------------------------------------------------------
    def start(self) -> QrTicket:
        payload, _headers = self._fetch(GENERATE_URL)
        if payload.get("code") not in (0, None):
            raise QrLoginError(payload.get("message") or "生成二维码失败")
        data = payload.get("data") or {}
        url = data.get("url") or ""
        key = data.get("qrcode_key") or ""
        if not url or not key:
            raise InvalidResponseError("生成二维码的响应缺少 url 或 qrcode_key")
        return QrTicket(url=url, key=key)

    def poll(self, ticket: QrTicket) -> QrPollResult:
        query = urllib.parse.urlencode({"qrcode_key": ticket.key})
        payload, headers = self._fetch(f"{POLL_URL}?{query}")
        if _debug_enabled():
            _debug_dump(payload, headers)
        data = payload.get("data") or {}
        code = data.get("code")
        status = _CODE_TO_STATUS.get(code, "error")
        if status == "error":
            return QrPollResult(status=status, message=data.get("message") or f"未知状态 code={code}")
        if status == STATUS_SUCCESS:
            sessdata = extract_sessdata_from_data(data)
            if not sessdata:
                sessdata = extract_sessdata_from_headers(headers)
            if not sessdata and data.get("url"):
                # 最后一道兜底:有些流程把 Cookie 放在跨域跳转那一步的响应头里,
                # 跟着这个地址再请求一次,只读它的 Set-Cookie。
                try:
                    _payload, redirect_headers = self._fetch(str(data["url"]))
                    sessdata = extract_sessdata_from_headers(redirect_headers)
                except FavFocusError:
                    sessdata = None
            if not sessdata:
                raise QrLoginError(_missing_sessdata_message(data, headers))
            return QrPollResult(status=status, message=_STATUS_TEXT[status], sessdata=sessdata)
        return QrPollResult(status=status, message=_STATUS_TEXT[status])

    # -- 二维码渲染 -------------------------------------------------------
    @staticmethod
    def make_qr(text: str, *, border: int = 2) -> list[list[bool]]:
        """把文本编成二维码矩阵(含静默区)。True = 黑块。

        纠错级别用 MEDIUM:二维码会显示在屏幕上,不容易被遮挡,
        但手机拍摄角度/反光仍可能干扰,中等级别是最稳的平衡点。
        """
        code = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
        size = code.get_size()
        matrix: list[list[bool]] = []
        for _ in range(border):
            matrix.append([False] * (size + border * 2))
        for y in range(size):
            row = [False] * border
            row.extend(code.get_module(x, y) for x in range(size))
            row.extend([False] * border)
            matrix.append(row)
        for _ in range(border):
            matrix.append([False] * (size + border * 2))
        return matrix


def _pairs(text: str) -> list[tuple[str, str]]:
    """把 `a=1&b=2` 或 `a=1;b=2` 拆成键值对,不做 URL 解码。"""
    items: list[tuple[str, str]] = []
    text = text.replace("&amp;", "&")   # 少数响应会把 & 转义成 &amp;
    for chunk in text.replace(";", "&").split("&"):
        name, _, value = chunk.partition("=")
        name = name.strip()
        if name:
            items.append((name, value.strip()))
    return items


def extract_sessdata(url: str) -> str | None:
    """从登录成功返回的跳转地址里取出 SESSDATA。

    形如:`https://passport.biligame.com/crossDomain?DedeUserID=***&SESSDATA=***&bili_jct=***`

    注意**不能做 URL 解码**:SESSDATA 值本身就带 `%2C`(逗号的百分号编码),
    这个百分号是 Cookie 值的一部分,解码后拿去当 Cookie 会被 B 站拒绝。
    """
    if not url:
        return None
    text = urllib.parse.urlsplit(url).query or url
    for name, value in _pairs(text):
        if name.upper() == "SESSDATA" and value:
            return value
    # 兜底:整个地址被当成一个参数编码过(少见),解一层再试
    decoded = urllib.parse.unquote(url)
    if decoded != url:
        query = urllib.parse.urlsplit(decoded).query or decoded
        for name, value in _pairs(query):
            if name.upper() == "SESSDATA" and value:
                return value
    return None


def extract_sessdata_from_data(data: dict) -> str | None:
    """从轮询 JSON 的 `data` 里找 SESSDATA。

    优先级:结构化字段 → 跨域跳转地址。结构化字段最可靠,因为不用解析字符串。
    """
    # 1) 部分登录流程(TV/APP)会在 cookie_info 里直接给出 Cookie 列表
    info = data.get("cookie_info") or {}
    if isinstance(info, dict):
        for cookie in info.get("cookies") or []:
            if not isinstance(cookie, dict):
                continue
            if str(cookie.get("name", "")).upper() == "SESSDATA" and cookie.get("value"):
                return str(cookie["value"])
    # 2) 顶层直接给字段(历史版本出现过)
    for key in ("sessdata", "SESSDATA"):
        if data.get(key):
            return str(data[key])
    # 3) web 端的跨域跳转地址
    return extract_sessdata(str(data.get("url") or ""))


def extract_sessdata_from_headers(headers: dict[str, list[str]] | None) -> str | None:
    """从 `Set-Cookie` 响应头里找 SESSDATA(web 端真正下发 Cookie 的地方)。"""
    if not headers:
        return None
    values: list[str] = []
    for key, items in headers.items():
        if key.lower().replace("_", "-") == "set-cookie":
            values.extend(items if isinstance(items, (list, tuple)) else [str(items)])
    for header in values:
        first = header.split(";", 1)[0]
        name, _, value = first.partition("=")
        if name.strip().upper() == "SESSDATA" and value.strip():
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            return value
    return None


def _missing_sessdata_message(data: dict, headers: dict[str, list[str]] | None) -> str:
    """拿不到 SESSDATA 时,给一句能用于排查的话(只暴露字段名,不暴露值)。"""
    data_keys = ",".join(sorted(data.keys())) or "(空)"
    set_cookie_names: list[str] = []
    if headers:
        for key, items in headers.items():
            if key.lower().replace("_", "-") == "set-cookie":
                for header in items:
                    name = header.split(";", 1)[0].partition("=")[0].strip()
                    if name:
                        set_cookie_names.append(name)
    cookies = ",".join(set_cookie_names) or "(无)"
    return (
        "扫码成功但没拿到 SESSDATA。"
        f"响应字段:{data_keys};Set-Cookie:{cookies}。"
        "这通常说明 B 站改了登录返回格式,请把这个信息反馈一下。"
    )


# ── 排查开关 ──────────────────────────────────────────────────────────
# 设环境变量 FAVFOCUS_QR_DEBUG=1 后,每次轮询会把响应结构打到 stderr
# (只打字段名和值的长度,不打印任何凭证内容),方便定位 B 站格式变化。
_SENSITIVE = ("sessdata", "token", "cookie", "jct", "dedeuserid", "url", "sign")


def _debug_enabled() -> bool:
    import os

    return os.environ.get("FAVFOCUS_QR_DEBUG", "").strip() in ("1", "true", "yes")


def _redact(key: str, value) -> str:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE) or isinstance(value, str) and len(str(value)) > 24:
        text = str(value)
        return f"<{len(text)} 字符>"
    return repr(value)


def _describe(node, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(node, dict):
        if not node:
            return "{}"
        lines = ["{"]
        for key, value in node.items():
            rendered = _describe(value, depth + 1) if isinstance(value, (dict, list)) else _redact(key, value)
            lines.append(f"{pad}  {key}: {rendered}")
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(node, list):
        if not node:
            return "[]"
        items = []
        for index, value in enumerate(node[:5]):
            if isinstance(value, dict):
                names = value.get("name") if isinstance(value.get("name"), str) else None
                label = f" name={names}" if names else ""
                items.append(f"{pad}  [{index}]{label} {_describe(value, depth + 1) if not names else ''}")
            else:
                items.append(f"{pad}  [{index}] {_redact('', value)}")
        return "\n".join(items)
    return _redact("", node)


def _debug_dump(payload: dict, headers: dict[str, list[str]] | None) -> None:
    """把一次轮询响应的结构打到 stderr(默认关闭)。"""
    import sys

    try:
        print("[qr-debug] 响应结构:", file=sys.stderr)
        print(_describe(payload), file=sys.stderr)
        names = []
        for key, items in (headers or {}).items():
            if key.lower().replace("_", "-") == "set-cookie":
                for header in items:
                    names.append(header.split(";", 1)[0].partition("=")[0].strip())
        print(f"[qr-debug] Set-Cookie 名称: {names or '(无)'}", file=sys.stderr)
    except Exception:  # noqa: BLE001 - 排查输出不该影响主流程
        pass


def render_terminal(matrix: list[list[bool]]) -> str:
    """把矩阵渲成终端里能看的方块图(每模块两个字符,近似正方形)。"""
    return "\n".join(
        "".join("██" if cell else "  " for cell in row) for row in matrix
    )
