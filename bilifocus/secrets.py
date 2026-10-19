"""把登录凭证加密后再写盘(Windows DPAPI)。

`state.json` 里的 SESSDATA 等价于账号密码:任何人拷走这个文件都能用你的身份
访问 B 站。这里用 Windows 自带的 DPAPI(`CryptProtectData`) 给它加密:

- 密钥由 Windows 按**当前用户**管理,不需要我们自己保存密码;
- 换一台机器、换个用户账户,密文都解不开;
- 解密只在这台机器、这个用户下有效。

非 Windows 平台退化为 `plain:` 前缀的 base64(仅避免肉眼直接看到,不具备
真正的保护能力),测试和跨平台开发不受影响。

密文格式(存在 state.json 的 cookies.SESSDATA 里):

    dpapi:<base64>     Windows DPAPI 加密
    plain:<base64>     非 Windows 的退化形式
    <原样字符串>        旧版本直接存的明文,读取时仍兼容
"""

from __future__ import annotations

import base64
import ctypes
import sys

PREFIX_DPAPI = "dpapi:"
PREFIX_PLAIN = "plain:"
_ENTROPY = b"BiliFocus::SESSDATA::v1"
_DESCRIPTION = "BiliFocus SESSDATA"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _platform_supported() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _protect_windows(plain: str) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data_in, _buf_in = _blob(plain.encode("utf-8"))
    data_entropy, _buf_entropy = _blob(_ENTROPY)
    data_out = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(data_in),
        ctypes.c_wchar_p(_DESCRIPTION),
        ctypes.byref(data_entropy),
        None,
        None,
        0,
        ctypes.byref(data_out),
    ):
        raise OSError("CryptProtectData 失败")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(data_out.pbData)


def _unprotect_windows(cipher: bytes) -> str:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    data_in, _buf_in = _blob(cipher)
    data_entropy, _buf_entropy = _blob(_ENTROPY)
    data_out = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        None,
        ctypes.byref(data_entropy),
        None,
        None,
        0,
        ctypes.byref(data_out),
    ):
        raise OSError("CryptUnprotectData 失败(换了机器/用户,或密文损坏)")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(data_out.pbData)


def protect(value: str | None) -> str | None:
    """加密成可以写进 JSON 的字符串。空值原样返回。"""
    if not value:
        return value
    if _platform_supported():
        try:
            cipher = _protect_windows(value)
            return PREFIX_DPAPI + base64.b64encode(cipher).decode("ascii")
        except OSError:
            pass
    return PREFIX_PLAIN + base64.b64encode(value.encode("utf-8")).decode("ascii")


def unprotect(stored: str | None) -> str | None:
    """把存储值还原成明文。无法解密时返回 None(当作未登录)。"""
    if not stored:
        return stored
    if stored.startswith(PREFIX_DPAPI):
        if not _platform_supported():
            return None
        payload = stored[len(PREFIX_DPAPI) :]
        try:
            return _unprotect_windows(base64.b64decode(payload))
        except (OSError, ValueError):
            return None
    if stored.startswith(PREFIX_PLAIN):
        try:
            return base64.b64decode(stored[len(PREFIX_PLAIN) :]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    # 旧版明文:原样返回,下次保存时会被加密
    return stored


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and (
        stored.startswith(PREFIX_DPAPI) or stored.startswith(PREFIX_PLAIN)
    )


def backend() -> str:
    """给 doctor/文档看的当前加密方式。"""
    if _platform_supported():
        return "Windows DPAPI(当前用户)"
    return "base64(当前平台不支持 DPAPI)"
