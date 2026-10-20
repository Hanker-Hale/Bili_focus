"""异常层:把 B 站的返回码翻译成调用方能判断的类型。

调用方(包括将来的前端)不应该去记 -101 是什么意思,只需要 `except
AuthRequiredError` 然后提示用户重新登录。每条消息都写清"下一步做什么"。
"""

from __future__ import annotations

from typing import Any


class FavFocusError(Exception):
    """本库所有异常的基类。捕获它就捕获了全部可预期失败。"""


class NetworkError(FavFocusError):
    """连不上、超时、TLS 失败等传输层问题。通常值得重试。"""

    def __init__(self, message: str, *, url: str | None = None):
        super().__init__(message)
        self.url = url


class InvalidResponseError(FavFocusError):
    """响应不是预期的 JSON(常见于被风控页拦截返回 HTML)。"""


class ApiError(FavFocusError):
    """B 站返回了 code != 0。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        url: str | None = None,
        payload: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.url = url
        self.payload = payload or {}


class AuthRequiredError(ApiError):
    """登录态缺失或已失效(code -101)。需要重新提供 SESSDATA。"""


class PermissionDeniedError(ApiError):
    """有登录态但没有权限(code -403),例如收藏夹是别人的私密收藏夹。"""


class NotFoundError(ApiError):
    """目标不存在(code -404 / -400),例如收藏夹被删除。"""


class RiskControlError(ApiError):
    """触发风控(code -352 / -412 / -509)。降低频率或稍后重试。"""


class SignatureError(ApiError):
    """WBI 签名不被接受(code -403 且消息含"签名")。

    客户端内部会自动刷新密钥重试一次;仍然失败才会抛给调用方。
    """


class VoucherRequiredError(ApiError):
    """签名/风控未通过时 B 站会返回 code=0 但 data 里塞 v_voucher。

    这是"看起来成功实际被拦"的陷阱,必须显式识别,否则会当成空收藏夹。
    """


# 每种 code 的默认提示。写"下一步",而不是复述错误码。
_MESSAGES: dict[int, tuple[type[ApiError], str]] = {
    -101: (AuthRequiredError, "登录态无效或已过期,请重新粘贴 SESSDATA"),
    -403: (PermissionDeniedError, "没有访问权限:可能是私密收藏夹,或者登录态不属于该账号"),
    -404: (NotFoundError, "目标不存在:收藏夹可能已被删除"),
    -400: (NotFoundError, "请求参数不被接受"),
    -352: (RiskControlError, "触发了 B 站风控,请降低请求频率后重试"),
    -412: (RiskControlError, "请求被风控拦截,请降低请求频率后重试"),
    -509: (RiskControlError, "请求过于频繁,请稍后重试"),
}


def looks_like_signature_failure(code: int | None, message: str) -> bool:
    """只有签名类失败才值得刷新 WBI 密钥重试,权限类失败不该重试。"""
    return code == -403 and ("签名" in (message or ""))


def is_voucher_payload(payload: dict[str, Any]) -> bool:
    """识别 `{"code":0,"data":{"v_voucher":"..."}}` 这种伪成功。"""
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    return isinstance(data, dict) and "v_voucher" in data


def error_for_code(
    code: int | None,
    message: str = "",
    *,
    url: str | None = None,
    payload: dict[str, Any] | None = None,
) -> ApiError:
    """按返回码构造最具体的异常。"""
    detail = (message or "").strip()
    if looks_like_signature_failure(code, detail):
        return SignatureError(
            f"WBI 签名未被接受:{detail or '未知原因'}", code=code, url=url, payload=payload
        )
    factory, hint = _MESSAGES.get(code or 0, (ApiError, "B 站返回了错误"))
    text = f"{hint}(code={code})"
    if detail and detail != hint:
        text = f"{text}:{detail}"
    return factory(text, code=code, url=url, payload=payload)
