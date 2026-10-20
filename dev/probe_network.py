#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手动联调:用新底层打一次真实接口(匿名),确认协议链路通畅。

单元测试用替身,这里用真网络。日常排查时跑一下,几秒出结果。
"""

import sys

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from favfocus import BiliClient
from favfocus.errors import FavFocusError

import argparse

parser = argparse.ArgumentParser(description="手动联调:匿名打真实接口")
parser.add_argument("--mid", type=int, default=0, help="收藏夹所属用户 mid(必填)")
parser.add_argument("--fid", type=int, default=0, help="收藏夹 id(必填)")
args = parser.parse_args()
MID = args.mid
FID = args.fid


def show(label, fn):
    try:
        print(f"[ok]   {label}: {fn()}")
    except FavFocusError as error:
        print(f"[code={error.code}] {label}: {error}")
    except Exception as error:  # noqa: BLE001
        print(f"[ERR]  {label}: {type(error).__name__}: {error}")


client = BiliClient()  # 匿名

print("== WBI ==")
show("wbi_keys", lambda: client.wbi_keys())

print("== 登录态 ==")
show("check_login().is_login", lambda: client.check_login().is_login)

print("== 鉴权接口 ==")
show(
    "signed_get wbi/view",
    lambda: (client.signed_get("/x/web-interface/wbi/view", {"bvid": "BV1GJ411x7h7"})["data"]["bvid"]),
)

print("== 收藏夹(匿名应无结果或无权限) ==")
show("folders", lambda: len(client.folders(MID)))
show("fav_page", lambda: client.fav_page(FID, 1))

print()
print("说明:匿名读私密收藏夹会被拒;真正可用需要 SESSDATA:")
print("  python -m favfocus login")
