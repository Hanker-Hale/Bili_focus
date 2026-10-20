"""WBI 签名:纯函数,不碰网络,方便单测。

B 站自 2023-03 起对部分接口启用 WBI 校验:请求必须在 query 里带 `wts` 与
`w_rid`。算法参考
https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md

三个容易做错的点(都有测试覆盖):

1. `img_key` 与 `sub_key` 各 32 字符,拼成 64 字符后按置换表重排,取前 32 位。
2. 参数值里的 `!'()*` 必须先剔除,否则签名对不上。
3. 百分号编码必须等价于 `encodeURIComponent`:空格是 ``%20`` 而不是 ``+``,
   十六进制大写。用 `urllib.parse.urlencode` 会踩第 3 条的坑。
"""

from __future__ import annotations

import time
import urllib.parse
from hashlib import md5
from typing import Any, Mapping

# 重排映射表,来自官方文档。长度 64,索引指向 img_key+sub_key 的字符位置。
MIXIN_KEY_ENC_TAB: tuple[int, ...] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
)

MIXIN_KEY_LENGTH = 32
RAW_KEY_LENGTH = max(MIXIN_KEY_ENC_TAB) + 1  # 64

# 这些字符留在参数值里会让签名对不上,必须先删掉而不是编码。
FORBIDDEN_CHARS = "!'()*"


def mixin_key(raw_wbi_key: str) -> str:
    """按置换表重排 `img_key + sub_key`,取前 32 位作为混流密钥。"""
    if len(raw_wbi_key) < RAW_KEY_LENGTH:
        raise ValueError(
            f"WBI 原始密钥太短:需要 {RAW_KEY_LENGTH} 个字符(img_key+sub_key),"
            f"实际 {len(raw_wbi_key)} 个"
        )
    return "".join(raw_wbi_key[index] for index in MIXIN_KEY_ENC_TAB)[:MIXIN_KEY_LENGTH]


def clean_value(value: Any) -> str:
    """去掉会让签名失效的字符。数字也统一成字符串。"""
    text = value if isinstance(value, str) else str(value)
    return "".join(ch for ch in text if ch not in FORBIDDEN_CHARS)


def encode_value(value: Any) -> str:
    """按 `encodeURIComponent` 语义编码:空格 -> %20,十六进制大写。"""
    return urllib.parse.quote(clean_value(value), safe="")


def encode_query(params: Mapping[str, Any]) -> str:
    """把参数编码成签名用的 query string:键按字典序,值已清洗并编码。"""
    pairs = []
    for key in sorted(params):
        pairs.append(f"{urllib.parse.quote(str(key), safe='')}={encode_value(params[key])}")
    return "&".join(pairs)


def sign(
    params: Mapping[str, Any],
    img_key: str,
    sub_key: str,
    *,
    timestamp: float | None = None,
) -> dict[str, str]:
    """给参数加上 `wts` 与 `w_rid`,返回新的 dict(全部值为字符串)。

    `timestamp` 可注入,便于测试复现官方样例。
    """
    signed: dict[str, str] = {str(key): clean_value(value) for key, value in params.items()}
    signed["wts"] = str(int(timestamp if timestamp is not None else time.time()))
    query = encode_query(signed)
    signed["w_rid"] = md5((query + mixin_key(img_key + sub_key)).encode()).hexdigest()
    return signed


def parse_wbi_keys(nav_payload: Mapping[str, Any]) -> tuple[str, str]:
    """从 nav 响应里取出 `img_key` / `sub_key`。

    注意 nav 对匿名访问返回 `code=-101`,但 `wbi_img` 依然有效——所以这里
    只看字段,不看 code。
    """
    data = nav_payload.get("data") if isinstance(nav_payload, Mapping) else None
    wbi = (data or {}).get("wbi_img") if isinstance(data, Mapping) else None
    if not isinstance(wbi, Mapping):
        raise ValueError("nav 响应里没有 wbi_img 字段")
    img_key = _filename(str(wbi.get("img_url") or ""))
    sub_key = _filename(str(wbi.get("sub_url") or ""))
    if not img_key or not sub_key:
        raise ValueError("nav 响应的 wbi_img 缺少 img_url 或 sub_url")
    if len(img_key) + len(sub_key) < RAW_KEY_LENGTH:
        raise ValueError(f"WBI 密钥长度异常:{len(img_key)}+{len(sub_key)}")
    return img_key, sub_key


def _filename(url: str) -> str:
    """`https://i0.hdslb.com/bfs/wbi/<key>.png` -> `<key>`。"""
    if not url:
        return ""
    tail = url.rsplit("/", 1)[-1]
    return tail.split(".", 1)[0]
