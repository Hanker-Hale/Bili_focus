"""扫码登录测试:状态映射、SESSDATA 提取、二维码矩阵。

不联网:HTTP 层用注入的 get_json 替身。
"""

from __future__ import annotations

import unittest

from favfocus.qrlogin import (
    STATUS_EXPIRED,
    STATUS_SCANNED,
    STATUS_SUCCESS,
    STATUS_WAITING,
    QrLoginError,
    QrLoginSession,
    QrTicket,
    extract_sessdata,
    render_terminal,
)

TICKET = QrTicket(url="https://account.bilibili.com/x?qrcode_key=abc", key="abc")


class FakeApi:
    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        for needle, payload in self.responses.items():
            if needle in url:
                return payload
        raise AssertionError(f"没有为这个 URL 准备响应:{url}")


def poll_payload(code: int, *, message: str = "", url: str = "") -> dict:
    return {"code": 0, "message": "OK", "data": {"code": code, "message": message, "url": url}}


class StartTests(unittest.TestCase):
    def test_parses_ticket(self):
        api = FakeApi({"qrcode/generate": {"code": 0, "data": {"url": TICKET.url, "qrcode_key": "abc"}}})
        session = QrLoginSession(get_json=api)
        ticket = session.start()
        self.assertEqual(ticket.key, "abc")
        self.assertEqual(ticket.url, TICKET.url)

    def test_missing_fields_raise(self):
        api = FakeApi({"qrcode/generate": {"code": 0, "data": {"url": ""}}})
        with self.assertRaises(Exception):
            QrLoginSession(get_json=api).start()

    def test_api_error_raises(self):
        api = FakeApi({"qrcode/generate": {"code": -400, "message": "请求错误"}})
        with self.assertRaises(QrLoginError):
            QrLoginSession(get_json=api).start()


class PollTests(unittest.TestCase):
    def session(self, payload: dict) -> QrLoginSession:
        return QrLoginSession(get_json=FakeApi({"qrcode/poll": payload}))

    def test_waiting(self):
        result = self.session(poll_payload(86101, message="未扫码")).poll(TICKET)
        self.assertEqual(result.status, STATUS_WAITING)
        self.assertFalse(result.done)

    def test_scanned(self):
        result = self.session(poll_payload(86090, message="已扫码")).poll(TICKET)
        self.assertEqual(result.status, STATUS_SCANNED)

    def test_expired(self):
        result = self.session(poll_payload(86038, message="二维码已失效")).poll(TICKET)
        self.assertEqual(result.status, STATUS_EXPIRED)
        self.assertTrue(result.expired)

    def test_success_extracts_sessdata(self):
        url = "https://passport.biligame.com/crossdomain?SESSDATA=abc%2C123&bili_jct=x&DedeUserID=1"
        result = self.session(poll_payload(0, url=url)).poll(TICKET)
        self.assertEqual(result.status, STATUS_SUCCESS)
        self.assertTrue(result.done)
        self.assertEqual(result.sessdata, "abc%2C123")

    def test_success_without_sessdata_raises(self):
        # 三个来源都没有:轮询响应无 Cookie、跨域跳转那步也没有
        def http_get(url: str):
            if "qrcode/poll" in url:
                return poll_payload(0, url="https://example.com/no-cookie"), {}
            return {"code": 0}, {}

        with self.assertRaises(QrLoginError) as caught:
            QrLoginSession(http_get=http_get).poll(TICKET)
        # 错误信息要能用于排查:带字段名,但不带任何值
        message = str(caught.exception)
        self.assertIn("没拿到 SESSDATA", message)
        self.assertIn("响应字段", message)
        self.assertIn("Set-Cookie", message)

    def test_success_from_cookie_info(self):
        payload = poll_payload(0)
        payload["data"]["cookie_info"] = {
            "cookies": [
                {"name": "bili_jct", "value": "j"},
                {"name": "SESSDATA", "value": "from-cookie-info"},
            ]
        }
        result = self.session(payload).poll(TICKET)
        self.assertEqual(result.sessdata, "from-cookie-info")

    def test_success_from_set_cookie_header(self):
        headers = {
            "set-cookie": [
                "bili_jct=j; Path=/; Domain=bilibili.com",
                'SESSDATA=abc%2C123; Path=/; Domain=bilibili.com; HttpOnly; Secure',
            ]
        }
        session = QrLoginSession(http_get=lambda _url: (poll_payload(0), headers))
        result = session.poll(TICKET)
        self.assertEqual(result.sessdata, "abc%2C123")

    def test_set_cookie_quoted_value_is_unquoted(self):
        headers = {"set-cookie": ['SESSDATA="quoted-value"; Path=/']}
        session = QrLoginSession(http_get=lambda _url: (poll_payload(0), headers))
        self.assertEqual(session.poll(TICKET).sessdata, "quoted-value")

    def test_cookie_info_wins_over_url(self):
        payload = poll_payload(0, url="https://x/y?SESSDATA=from-url&a=1")
        payload["data"]["cookie_info"] = {"cookies": [{"name": "SESSDATA", "value": "from-info"}]}
        self.assertEqual(self.session(payload).poll(TICKET).sessdata, "from-info")

    def test_lowercase_key_in_url(self):
        result = self.session(poll_payload(0, url="https://x/y?sessdata=lower&a=1")).poll(TICKET)
        self.assertEqual(result.sessdata, "lower")

    def test_semicolon_separated_url(self):
        result = self.session(poll_payload(0, url="https://x/y?a=1;SESSDATA=semi")).poll(TICKET)
        self.assertEqual(result.sessdata, "semi")

    def test_percent_encoded_url_is_retried(self):
        encoded = "https%3A%2F%2Fx%2Fy%3FSESSDATA%3Denc%252C1"
        result = self.session(poll_payload(0, url=encoded)).poll(TICKET)
        self.assertEqual(result.sessdata, "enc%2C1")

    def test_amp_escaped_url(self):
        result = self.session(
            poll_payload(0, url="https://x/y?a=1&amp;SESSDATA=escaped")
        ).poll(TICKET)
        self.assertEqual(result.sessdata, "escaped")

    def test_follows_crossdomain_url_for_cookies(self):
        """成功响应里没有 Cookie,但跨域跳转那一步的响应头里有。"""
        cross = "https://passport.biligame.com/crossDomain?DedeUserID=1"
        calls: list[str] = []

        def http_get(url: str):
            calls.append(url)
            if "qrcode/poll" in url:
                return poll_payload(0, url=cross), {}
            return {"code": 0}, {"set-cookie": ["SESSDATA=from-redirect; Path=/"]}

        session = QrLoginSession(http_get=http_get)
        result = session.poll(TICKET)
        self.assertEqual(result.sessdata, "from-redirect")
        self.assertEqual(len(calls), 2)

    def test_unknown_code_is_error_not_crash(self):
        result = self.session(poll_payload(99999)).poll(TICKET)
        self.assertEqual(result.status, "error")
        self.assertFalse(result.done)

    def test_poll_uses_qrcode_key(self):
        api = FakeApi({"qrcode/poll": poll_payload(86101)})
        QrLoginSession(get_json=api).poll(TICKET)
        self.assertIn("qrcode_key=abc", api.urls[0])


class ExtractTests(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(extract_sessdata("https://a/b?SESSDATA=xyz&c=1"), "xyz")

    def test_encoded(self):
        self.assertEqual(extract_sessdata("https://a/b?SESSDATA=abc%2C123"), "abc%2C123")

    def test_missing(self):
        self.assertIsNone(extract_sessdata(""))
        self.assertIsNone(extract_sessdata("https://a/b?c=1"))

    def test_empty_value(self):
        self.assertIsNone(extract_sessdata("https://a/b?SESSDATA="))


class MatrixTests(unittest.TestCase):
    def test_matrix_is_square_with_quiet_zone(self):
        matrix = QrLoginSession.make_qr("https://example.com/login")
        self.assertEqual(len(matrix), len(matrix[0]))
        # 静默区:最外一圈全白
        self.assertFalse(any(matrix[0]))
        self.assertFalse(any(matrix[-1]))
        self.assertFalse(any(row[0] for row in matrix))
        self.assertFalse(any(row[-1] for row in matrix))

    def test_has_finder_pattern(self):
        matrix = QrLoginSession.make_qr("https://example.com/login", border=0)
        # 左上角定位图案:7x7,外圈黑、第二圈白、中心 3x3 黑
        self.assertTrue(matrix[0][0])
        self.assertTrue(matrix[6][6])
        self.assertFalse(matrix[1][1])
        self.assertTrue(matrix[3][3])

    def test_matrix_size_grows_with_content(self):
        small = QrLoginSession.make_qr("a")
        big = QrLoginSession.make_qr("https://account.bilibili.com/h5/account-h5/auth/scan-web?navhide=1&callback=close&qrcode_key=" + "f" * 32 + "&from=")
        self.assertGreaterEqual(len(big), len(small))

    def test_terminal_render(self):
        matrix = QrLoginSession.make_qr("x")
        text = render_terminal(matrix)
        lines = text.split("\n")
        self.assertEqual(len(lines), len(matrix))
        self.assertEqual(len(lines[0]), len(matrix[0]) * 2)


if __name__ == "__main__":
    unittest.main()
