"""WBI 签名测试。

关键点:**用官方文档给的向量做锚**,而不是自己算一遍再和自己比。
向量来源:
https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/sign/wbi.md
"""

from __future__ import annotations

import unittest

from bilifocus.wbi import (
    MIXIN_KEY_ENC_TAB,
    encode_query,
    encode_value,
    mixin_key,
    parse_wbi_keys,
    sign,
)

IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


class MixinKeyTests(unittest.TestCase):
    def test_official_vector(self):
        """文档明确给出:这两个 key 混流出 ea1db124af3c7062474693fa704f4ff8。"""
        self.assertEqual(mixin_key(IMG_KEY + SUB_KEY), "ea1db124af3c7062474693fa704f4ff8")

    def test_follows_permutation_table(self):
        raw = "".join(chr(ord("a") + index % 26) for index in range(64))
        expected = "".join(raw[index] for index in MIXIN_KEY_ENC_TAB)[:32]
        self.assertEqual(mixin_key(raw), expected)

    def test_rejects_short_key(self):
        # 置换表最大索引 63,短密钥必须显式报错而不是静默算出错值
        with self.assertRaises(ValueError):
            mixin_key(IMG_KEY[:10])

    def test_table_is_64_entries(self):
        self.assertEqual(len(MIXIN_KEY_ENC_TAB), 64)
        self.assertEqual(sorted(MIXIN_KEY_ENC_TAB), list(range(64)))


class SignatureTests(unittest.TestCase):
    def test_official_signature_vector(self):
        """文档 Rust 测试用例:foo/bar/zab + wts=1702204169 -> 8f6f2b5b…"""
        signed = sign(
            {"foo": "114", "bar": "514", "zab": 1919810}, IMG_KEY, SUB_KEY, timestamp=1702204169
        )
        self.assertEqual(signed["w_rid"], "8f6f2b5b3d485fe1886cec6a0be8c5d4")
        without_rid = {key: value for key, value in signed.items() if key != "w_rid"}
        self.assertEqual(encode_query(without_rid), "bar=514&foo=114&wts=1702204169&zab=1919810")

    def test_signature_is_reproducible(self):
        first = sign({"a": "1"}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        second = sign({"a": "1"}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        self.assertEqual(first, second)

    def test_parameter_order_does_not_matter(self):
        first = sign({"b": "2", "a": "1"}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        second = sign({"a": "1", "b": "2"}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        self.assertEqual(first["w_rid"], second["w_rid"])

    def test_different_key_changes_signature(self):
        base = sign({"a": "1"}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        other_key = IMG_KEY[:10] + ("0" if IMG_KEY[10] != "0" else "1") + IMG_KEY[11:]
        other = sign({"a": "1"}, other_key, SUB_KEY, timestamp=1700000000)
        self.assertNotEqual(base["w_rid"], other["w_rid"])

    def test_does_not_mutate_input(self):
        params = {"foo": "114"}
        sign(params, IMG_KEY, SUB_KEY, timestamp=1700000000)
        self.assertEqual(params, {"foo": "114"})

    def test_all_values_become_strings(self):
        signed = sign({"n": 1919810, "flag": True}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        self.assertEqual(signed["n"], "1919810")
        self.assertEqual(signed["flag"], "True")

    def test_forbidden_characters_are_removed(self):
        signed = sign({"keyword": "a!b'c(d)e*f"}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        self.assertEqual(signed["keyword"], "abcdef")

    def test_non_ascii_survives_cleanup(self):
        signed = sign({"keyword": "中文 & 空格"}, IMG_KEY, SUB_KEY, timestamp=1700000000)
        self.assertEqual(signed["keyword"], "中文 & 空格")


class EncodingTests(unittest.TestCase):
    def test_space_is_percent20_not_plus(self):
        """文档明确要求 encodeURIComponent 语义:`+` 会导致签名不匹配。"""
        self.assertEqual(encode_value("one one four"), "one%20one%20four")

    def test_chinese_encoding_uppercase_hex(self):
        self.assertEqual(encode_value("五一四"), "%E4%BA%94%E4%B8%80%E5%9B%9B")

    def test_official_encoding_vector(self):
        """文档的编码示例(与签名无关,只验证编码)。"""
        self.assertEqual(
            encode_query({"foo": "one one four", "bar": "五一四", "baz": 1919810}),
            "bar=%E4%BA%94%E4%B8%80%E5%9B%9B&baz=1919810&foo=one%20one%20four",
        )

    def test_keys_are_sorted(self):
        self.assertEqual(encode_query({"z": "1", "a": "2", "m": "3"}), "a=2&m=3&z=1")

    def test_slash_and_specials_encoded(self):
        self.assertEqual(encode_value("a/b"), "a%2Fb")


class ParseKeysTests(unittest.TestCase):
    def test_parses_anonymous_nav_payload(self):
        payload = {
            "code": -101,
            "message": "账号未登录",
            "data": {
                "isLogin": False,
                "wbi_img": {
                    "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                    "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
                },
            },
        }
        self.assertEqual(parse_wbi_keys(payload), (IMG_KEY, SUB_KEY))

    def test_missing_wbi_img_raises(self):
        with self.assertRaises(ValueError):
            parse_wbi_keys({"code": 0, "data": {}})

    def test_short_keys_raise(self):
        payload = {
            "data": {"wbi_img": {"img_url": "https://x/abc.png", "sub_url": "https://x/def.png"}}
        }
        with self.assertRaises(ValueError):
            parse_wbi_keys(payload)


if __name__ == "__main__":
    unittest.main()
