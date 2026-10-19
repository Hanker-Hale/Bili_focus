"""真实网络集成测试(默认跳过)。

跑法::

    BILI_FOCUS_INTEGRATION=1 python -m unittest discover -s tests -t .

这里验的是**协议正确性**,单测覆盖不到:签名是否被 B 站接受、错误归因是否符合
真实响应。匿名即可,不需要 SESSDATA。
"""

from __future__ import annotations

import os
import unittest

from bilifocus.client import BiliClient
from bilifocus.errors import AuthRequiredError, PermissionDeniedError

RUN_INTEGRATION = os.environ.get("BILI_FOCUS_INTEGRATION") == "1"
# 一个公开的、长期存在的视频,用来验证 WBI 签名被接受。
KNOWN_BVID = "BV1GJ411x7h7"
# 一个需要登录才能读的收藏夹。
PRIVATE_FID = int(os.environ.get("BILI_FOCUS_TEST_FID") or 0)  # 自己的私密收藏夹,自测时用


@unittest.skipUnless(RUN_INTEGRATION, "设置 BILI_FOCUS_INTEGRATION=1 才跑真实网络测试")
class LiveProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = BiliClient()

    def test_nav_yields_two_32_char_keys(self):
        img_key, sub_key = self.client.wbi_keys()
        self.assertEqual((len(img_key), len(sub_key)), (32, 32))

    def test_signed_request_is_accepted(self):
        """签名错误会被拒;B 站接受说明算法与编码都对。"""
        payload = self.client.signed_get("/x/web-interface/wbi/view", {"bvid": KNOWN_BVID})
        data = payload.get("data") or {}
        self.assertEqual(data.get("bvid"), KNOWN_BVID)

    def test_anonymous_favorites_is_denied(self):
        with self.assertRaises((AuthRequiredError, PermissionDeniedError)):
            self.client.fav_page(PRIVATE_FID)


if __name__ == "__main__":
    unittest.main()
