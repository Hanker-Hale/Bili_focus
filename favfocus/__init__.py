"""FavFocus 底层包。

设计原则:**底层不依赖任何界面**。`favfocus` 只负责"拿到收藏夹数据、记住
你看过什么",界面(当前是 `favfocus.ui_tk`)只是它的一个调用方。换掉界面
不需要改动这里的任何一行。

分层::

    wbi / http / models      纯函数与数据,无状态
    client                   一个 B 站账号视角的 API 客户端
    store                    本机持久化(state.json + 收藏夹缓存)
    service                  编排:缓存、已看标记、导出
    cli                      命令行入口(python -m favfocus)
    ui_tk                    可替换的桌面壳

典型用法::

    from favfocus import FavoritesService

    service = FavoritesService()
    service.login("你的 SESSDATA")
    view = service.videos(12345678)          # 走缓存
    view = service.videos(12345678, force=True)  # 强制刷新
    for video in view.videos:
        print(video.title, video.url)
"""

from __future__ import annotations

from .errors import (
    ApiError,
    AuthRequiredError,
    FavFocusError,
    InvalidResponseError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RiskControlError,
    SignatureError,
    VoucherRequiredError,
)
from .models import Folder, FavoritesView, LoginState, Page, Video
from .service import FavoritesService
from .store import CacheStore, StateStore, default_home
from .wbi import encode_query, mixin_key, sign

__version__ = "2.2.1"

__all__ = [
    "__version__",
    # service
    "FavoritesService",
    # data
    "Folder",
    "Video",
    "Page",
    "FavoritesView",
    "LoginState",
    # storage
    "StateStore",
    "CacheStore",
    "default_home",
    # signing
    "sign",
    "mixin_key",
    "encode_query",
    # errors
    "FavFocusError",
    "NetworkError",
    "InvalidResponseError",
    "ApiError",
    "AuthRequiredError",
    "PermissionDeniedError",
    "NotFoundError",
    "RiskControlError",
    "SignatureError",
    "VoucherRequiredError",
]
