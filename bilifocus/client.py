"""B 站 API 客户端:一个账号视角下的只读操作。

只做三件事:验证登录、列收藏夹、翻页取收藏内容。**不做缓存、不做已看标记**
——那是 `service` 的职责。这样替换存储或加缓存时不用碰 API 代码。

WBI 签名失败会**自动刷新密钥重试一次**:B 站的 `img_key`/`sub_key` 每日轮换,
长跑进程必然遇到密钥过期,重试比让用户看到错误要好。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping

from .errors import (
    AuthRequiredError,
    InvalidResponseError,
    NetworkError,
    PermissionDeniedError,
    RiskControlError,
    SignatureError,
    VoucherRequiredError,
    error_for_code,
    is_voucher_payload,
)
from .http import DEFAULT_REFERER, UrllibTransport
from .models import Folder, LoginState, Page, Video
from .wbi import parse_wbi_keys, sign

API_BASE = "https://api.bilibili.com"

# B 站对每条返回条数有硬上限:ps 超过 20 会被截断。
FAV_PAGE_SIZE = 20
# 翻页保护,避免超大收藏夹把流程卡死。
DEFAULT_MAX_PAGES = 100
# WBI 密钥缓存时长。官方文档说密钥每日轮换,1 小时足够保守。
WBI_KEY_TTL = 3600.0

ProgressCallback = Callable[[int, int, int], None]


class BiliClient:
    """只读的 B 站 Web API 客户端。"""

    def __init__(
        self,
        *,
        transport=None,
        cookies: Mapping[str, str] | None = None,
        timeout: float = 15.0,
        page_size: int = FAV_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        clock: Callable[[], float] = time.time,
    ):
        self._transport = transport or UrllibTransport(timeout=timeout)
        self._cookies: dict[str, str] = dict(cookies or {})
        self.timeout = timeout
        self.page_size = max(1, min(int(page_size), FAV_PAGE_SIZE))
        self.max_pages = max(1, int(max_pages))
        self._clock = clock
        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_fetched_at = 0.0

    # -- 凭证 -------------------------------------------------------------
    @property
    def cookies(self) -> dict[str, str]:
        return dict(self._cookies)

    def set_cookies(self, cookies: Mapping[str, str] | None) -> None:
        self._cookies = {str(key): str(value) for key, value in (cookies or {}).items() if value}
        # 登录态变了,密钥不必换(全站统一),但清掉更安全。
        self._wbi_keys = None

    def has_sessdata(self) -> bool:
        return bool(self._cookies.get("SESSDATA"))

    def _headers(self) -> dict[str, str]:
        headers = {"Referer": DEFAULT_REFERER, "Accept": "application/json, text/plain, */*"}
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self._cookies.items())
        return headers

    # -- 传输 -------------------------------------------------------------
    def _get_json(
        self, path: str, params: Mapping[str, Any] | None = None, *, strict: bool = True
    ) -> dict[str, Any]:
        from urllib.parse import urlencode

        url = f"{API_BASE}{path}"
        if params:
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
        response = self._transport.get(url, headers=self._headers(), timeout=self.timeout)
        if response.status >= 500:
            raise NetworkError(f"B 站服务端错误(HTTP {response.status})", url=url)
        if response.status == 401:
            raise AuthRequiredError("登录态被拒绝(HTTP 401),请重新登录", code=401, url=url)
        if response.status == 403:
            raise PermissionDeniedError("请求被拒绝(HTTP 403)", code=403, url=url)
        if response.status in (412, 429):
            raise RiskControlError(
                f"请求被风控拦截(HTTP {response.status}),请降低频率后重试",
                code=response.status,
                url=url,
            )
        try:
            payload = json.loads(response.body)
        except ValueError as error:
            raise InvalidResponseError(
                f"响应不是 JSON(可能被风控页拦截,HTTP {response.status}):"
                f"{response.body[:120]!r}"
            ) from error
        if not isinstance(payload, dict):
            raise InvalidResponseError("响应不是 JSON 对象")

        # 伪成功:code=0 但 data 里塞 v_voucher,表示签名/风控没通过。
        if is_voucher_payload(payload):
            raise VoucherRequiredError(
                "B 站没有接受这次请求的签名(v_voucher),可能是密钥过期或触发风控",
                code=payload.get("code"),
                url=url,
                payload=payload,
            )

        code = payload.get("code")
        if code not in (0, None) and strict:
            raise error_for_code(code, payload.get("message") or "", url=url, payload=payload)
        return payload

    # -- 登录 -------------------------------------------------------------
    def nav_payload(self) -> dict[str, Any]:
        """nav 返回 WBI 密钥;匿名时 code=-101 但 data.wbi_img 仍然有效。"""
        return self._get_json("/x/web-interface/nav", strict=False)

    def check_login(self) -> LoginState:
        payload = self.nav_payload()
        data = payload.get("data")
        return LoginState.from_nav_data(data if isinstance(data, Mapping) else None)

    # -- WBI --------------------------------------------------------------
    def wbi_keys(self, *, force: bool = False) -> tuple[str, str]:
        now = self._clock()
        if self._wbi_keys and not force and now - self._wbi_fetched_at < WBI_KEY_TTL:
            return self._wbi_keys
        payload = self.nav_payload()
        try:
            self._wbi_keys = parse_wbi_keys(payload)
        except ValueError as error:
            raise InvalidResponseError(f"拿不到 WBI 密钥:{error}") from error
        self._wbi_fetched_at = now
        return self._wbi_keys

    def _signed_params(self, params: Mapping[str, Any]) -> dict[str, str]:
        img_key, sub_key = self.wbi_keys()
        return sign(params, img_key, sub_key, timestamp=self._clock())

    def signed_get(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """带 WBI 签名的 GET。签名被拒时刷新密钥并重试一次。"""
        try:
            return self._get_json(path, self._signed_params(params))
        except (SignatureError, VoucherRequiredError) as first_error:
            try:
                self.wbi_keys(force=True)
                return self._get_json(path, self._signed_params(params))
            except VoucherRequiredError:
                raise RiskControlError(
                    "刷新签名密钥后 B 站仍然拒绝(B 站可能判定为风控),请稍后重试",
                    code=first_error.code,
                    payload=getattr(first_error, "payload", None),
                ) from first_error
            except SignatureError:
                raise SignatureError(
                    "WBI 签名在刷新密钥后仍未被接受,通常是 B 站改了算法或账号被限流",
                    code=first_error.code,
                    payload=getattr(first_error, "payload", None),
                ) from first_error

    # -- 收藏夹 -----------------------------------------------------------
    def folders(self, mid: int) -> tuple[Folder, ...]:
        """列出某账号的收藏夹。私密收藏夹只在登录态属于该账号时才可见。"""
        collected: list[Folder] = []
        seen: set[int] = set()
        for page_number in range(1, self.max_pages + 1):
            payload = self.signed_get(
                "/x/v3/fav/folder/created/list",
                {"up_mid": int(mid), "pn": page_number, "ps": FAV_PAGE_SIZE},
            )
            data = payload.get("data") or {}
            items = data.get("list") or []
            if not isinstance(items, list) or not items:
                break
            for raw in items:
                folder = Folder.from_api(raw)
                if folder is not None and folder.fid not in seen:
                    seen.add(folder.fid)
                    collected.append(folder)
            if not data.get("has_more"):
                break
        return tuple(collected)

    def fav_page(self, fid: int, page: int = 1, *, order: str = "mtime") -> Page:
        payload = self.signed_get(
            "/x/v3/fav/resource/list",
            {
                "media_id": int(fid),
                "pn": int(page),
                "ps": self.page_size,
                "keyword": "",
                "order": order,
                "type": 0,
                "tid": 0,
                "platform": "web",
            },
        )
        data = payload.get("data") or {}
        return Page.from_api(data, page)

    def all_favorites(
        self, fid: int, *, order: str = "mtime", progress: ProgressCallback | None = None
    ) -> tuple[list[Video], int]:
        """翻完整个收藏夹。返回 (videos, 服务端报告的总数)。"""
        videos: list[Video] = []
        total = 0
        for page_number in range(1, self.max_pages + 1):
            page = self.fav_page(fid, page_number, order=order)
            if page_number == 1:
                total = page.total
            if not page.videos:
                break
            videos.extend(page.videos)
            if progress is not None:
                progress(page_number, len(videos), total)
            if not page.has_more:
                break
        return videos, total
