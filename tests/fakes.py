"""测试替身:假传输 + 响应构造器。单元测试一律不联网。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bilifocus.client import BiliClient
from bilifocus.http import HttpResponse
from bilifocus.service import FavoritesService
from bilifocus.store import CacheStore, StateStore

# 官方文档里的样例密钥(每天会变,但作为固定输入足够)
IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"

# 测试里按 URL 子串路由用的路径片段
NAV = "/x/web-interface/nav"
FOLDERS = "/x/v3/fav/folder/created/list"
FAV = "/x/v3/fav/resource/list"


def json_response(payload: Any, *, status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(payload, ensure_ascii=False))


class FakeTransport:
    """按 URL 子串路由到预置响应;同一路由给列表就按顺序消费。"""

    def __init__(self, routes: dict[str, Any] | None = None):
        self.routes: dict[str, list[HttpResponse]] = {}
        for key, value in (routes or {}).items():
            self.routes[key] = list(value) if isinstance(value, list) else [value]
        self.calls: list[str] = []
        self.headers_seen: list[dict[str, str]] = []

    def get(self, url: str, *, headers=None, timeout=None) -> HttpResponse:
        self.calls.append(url)
        self.headers_seen.append(dict(headers or {}))
        for key, queue in self.routes.items():
            if key in url:
                if not queue:
                    raise AssertionError(f"路由 {key} 的响应已用尽(请求:{url})")
                if len(queue) > 1:
                    return queue.pop(0)
                return queue[0]
        raise AssertionError(f"FakeTransport 没有为这个 URL 配置响应:{url}")

    def calls_matching(self, needle: str) -> list[str]:
        return [url for url in self.calls if needle in url]


# --------------------------------------------------------------------------
# 响应构造
# --------------------------------------------------------------------------
def nav_payload(
    *,
    is_login: bool = False,
    uname: str = "测试用户",
    mid: int = 479257031,
    img_key: str = IMG_KEY,
    sub_key: str = SUB_KEY,
) -> dict:
    data: dict[str, Any] = {
        "isLogin": bool(is_login),
        "wbi_img": {
            "img_url": f"https://i0.hdslb.com/bfs/wbi/{img_key}.png",
            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{sub_key}.png",
        },
    }
    if is_login:
        data.update({"uname": uname, "mid": mid})
        return {"code": 0, "message": "0", "data": data}
    return {"code": -101, "message": "账号未登录", "data": data}


def api_error(code: int, message: str) -> dict:
    return {"code": code, "message": message, "ttl": 1, "data": None}


def voucher_payload() -> dict:
    """签名/风控未通过时 B 站会返回的"伪成功"。"""
    return {"code": 0, "message": "0", "ttl": 1, "data": {"v_voucher": "voucher_test"}}


def folder_item(fid: int, title: str, count: int, attr: int = 0) -> dict:
    return {"id": fid, "title": title, "media_count": count, "attr": attr}


def folders_payload(items: list[dict], *, has_more: bool = False) -> dict:
    return {"code": 0, "message": "0", "data": {"list": items, "has_more": has_more}}


def media_item(
    aid: int,
    bvid: str,
    title: str,
    *,
    duration: int = 125,
    upper: str = "某UP",
    fav_time: int = 1700000000,
    attr: int = 0,
    intro: str = "",
) -> dict:
    return {
        "id": aid,
        "bvid": bvid,
        "title": title,
        "duration": duration,
        "fav_time": fav_time,
        "attr": attr,
        "intro": intro,
        "upper": {"name": upper, "mid": 1},
    }


def fav_payload(medias: list[dict], *, has_more: bool = False, total: int | None = None) -> dict:
    return {
        "code": 0,
        "message": "0",
        "data": {
            "info": {"media_count": total if total is not None else len(medias)},
            "medias": medias,
            "has_more": bool(has_more),
        },
    }


# --------------------------------------------------------------------------
# 构造被测对象
# --------------------------------------------------------------------------
def make_client(routes: dict[str, Any], **kwargs) -> tuple[BiliClient, FakeTransport]:
    transport = FakeTransport(routes)
    client = BiliClient(transport=transport, cookies={"SESSDATA": "test-sessdata"}, **kwargs)
    return client, transport


class Clock:
    """可控时钟,用来测缓存过期。"""

    def __init__(self, start: float = 1_700_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Harness:
    """一组绑好的被测对象,方便测试里同时访问服务、传输替身和时钟。"""

    service: FavoritesService
    client: BiliClient
    transport: FakeTransport
    clock: "Clock"


def make_service(
    routes: dict[str, Any],
    tmp_path,
    *,
    clock: Clock | None = None,
    cache_ttl: float = 600.0,
) -> Harness:
    clock = clock or Clock()
    store = StateStore(home=tmp_path)
    # 让持久化状态与客户端凭证一致:真实使用中 SESSDATA 就存在 store 里。
    store.set_sessdata("test-sessdata")
    cache = CacheStore(home=tmp_path)
    transport = FakeTransport(routes)
    client = BiliClient(transport=transport, cookies={"SESSDATA": "test-sessdata"}, clock=clock)
    service = FavoritesService(
        store=store, cache=cache, client=client, cache_ttl=cache_ttl, clock=clock
    )
    return Harness(service=service, client=client, transport=transport, clock=clock)
