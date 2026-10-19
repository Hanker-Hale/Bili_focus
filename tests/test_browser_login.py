"""浏览器自动登录的测试。

真正的浏览器交互需要图形环境,这里覆盖不依赖界面的部分:
Cookie 过滤、WebSocket 帧格式、以及服务层把捕获到的值交给登录流程。
"""

from __future__ import annotations

import unittest

from bilifocus.browser_login import BrowserLogin, BrowserLoginError, _MiniWebSocket


class ExtractSessdataTests(unittest.TestCase):
    def test_picks_cookie_from_bilibili_domain(self):
        cookies = [
            {"name": "buvid3", "value": "x", "domain": ".bilibili.com"},
            {"name": "SESSDATA", "value": "good", "domain": ".bilibili.com"},
        ]
        self.assertEqual(BrowserLogin.extract_sessdata(cookies), "good")

    def test_ignores_other_domains(self):
        """同名 Cookie 出现在别的域时必须忽略——这是防串号的关键。"""
        cookies = [
            {"name": "SESSDATA", "value": "evil", "domain": ".evil.example"},
            {"name": "SESSDATA", "value": "good", "domain": ".bilibili.com"},
        ]
        self.assertEqual(BrowserLogin.extract_sessdata(cookies), "good")

    def test_rejects_lookalike_domain(self):
        cookies = [{"name": "SESSDATA", "value": "evil", "domain": ".bilibili.com.evil.example"}]
        self.assertIsNone(BrowserLogin.extract_sessdata(cookies))

    def test_accepts_bare_domain(self):
        cookies = [{"name": "SESSDATA", "value": "v", "domain": "bilibili.com"}]
        self.assertEqual(BrowserLogin.extract_sessdata(cookies), "v")

    def test_missing_or_empty(self):
        self.assertIsNone(BrowserLogin.extract_sessdata([]))
        self.assertIsNone(
            BrowserLogin.extract_sessdata([{"name": "SESSDATA", "value": "", "domain": ".bilibili.com"}])
        )

    def test_other_cookie_names_ignored(self):
        cookies = [{"name": "bili_jct", "value": "v", "domain": ".bilibili.com"}]
        self.assertIsNone(BrowserLogin.extract_sessdata(cookies))


class FrameTests(unittest.TestCase):
    def _unmask(self, frame: bytes) -> tuple[int, int, bytes]:
        first, second = frame[0], frame[1]
        length = second & 0x7F
        offset = 2
        if length == 126:
            length = int.from_bytes(frame[offset:offset + 2], "big")
            offset += 2
        elif length == 127:
            length = int.from_bytes(frame[offset:offset + 8], "big")
            offset += 8
        masked = bool(second & 0x80)
        mask = frame[offset:offset + 4] if masked else b""
        offset += 4 if masked else 0
        payload = frame[offset:offset + length]
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return first, second, payload

    def test_small_frame_is_masked_text(self):
        frame = _MiniWebSocket.build_frame(b"hi", opcode=0x1)
        first, second, payload = self._unmask(frame)
        self.assertEqual(first, 0x81, "FIN + text opcode")
        self.assertTrue(second & 0x80, "客户端帧必须带掩码")
        self.assertEqual(payload, b"hi")

    def test_medium_frame_uses_16bit_length(self):
        data = b"x" * 200
        frame = _MiniWebSocket.build_frame(data)
        _first, second, payload = self._unmask(frame)
        self.assertEqual(second & 0x7F, 126)
        self.assertEqual(payload, data)

    def test_large_frame_uses_64bit_length(self):
        data = b"y" * 70000
        frame = _MiniWebSocket.build_frame(data)
        _first, second, payload = self._unmask(frame)
        self.assertEqual(second & 0x7F, 127)
        self.assertEqual(len(payload), 70000)
        self.assertEqual(payload[:10], b"y" * 10)

    def test_binary_opcode(self):
        frame = _MiniWebSocket.build_frame(b"x", opcode=0x2)
        first, _second, payload = self._unmask(frame)
        self.assertEqual(first & 0x0F, 0x2)
        self.assertEqual(payload, b"x")


class ErrorTypeTests(unittest.TestCase):
    def test_error_is_catchable_as_library_error(self):
        """CLI 只 catch BiliFocusError,所以浏览器登录的错误要能被它接住。"""
        from bilifocus.errors import BiliFocusError

        self.assertTrue(issubclass(BrowserLoginError, BiliFocusError))


if __name__ == "__main__":
    unittest.main()
