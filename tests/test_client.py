"""BiliClient 测试:签名参数、错误归因、签名失败重试、翻页。"""

from __future__ import annotations

import unittest

from bilifocus.client import BiliClient
from bilifocus.errors import (
    ApiError,
    AuthRequiredError,
    InvalidResponseError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RiskControlError,
    SignatureError,
)
from bilifocus.http import HttpResponse

from tests.fakes import (
    IMG_KEY,
    SUB_KEY,
    api_error,
    fav_payload,
    folder_item,
    folders_payload,
    json_response,
    make_client,
    media_item,
    nav_payload,
    voucher_payload,
)

NAV = "/x/web-interface/nav"
FOLDERS = "/x/v3/fav/folder/created/list"
FAV = "/x/v3/fav/resource/list"


class NavTests(unittest.TestCase):
    def test_anonymous_nav_is_not_an_error(self):
        """nav 匿名返回 code=-101,但 wbi_img 有效——不能当失败处理。"""
        client, transport = make_client({NAV: json_response(nav_payload(is_login=False))})
        state = client.check_login()
        self.assertFalse(state.is_login)
        self.assertEqual(len(transport.calls), 1)

    def test_logged_in_state(self):
        client, _ = make_client({NAV: json_response(nav_payload(is_login=True, uname="甲", mid=9))})
        state = client.check_login()
        self.assertTrue(state.is_login)
        self.assertEqual(state.uname, "甲")
        self.assertEqual(state.mid, 9)

    def test_wbi_keys_from_nav(self):
        client, _ = make_client({NAV: json_response(nav_payload())})
        self.assertEqual(client.wbi_keys(), (IMG_KEY, SUB_KEY))

    def test_wbi_keys_are_cached(self):
        client, transport = make_client({NAV: json_response(nav_payload())})
        client.wbi_keys()
        client.wbi_keys()
        self.assertEqual(len(transport.calls_matching(NAV)), 1)

    def test_cookie_header_is_sent(self):
        client, transport = make_client({NAV: json_response(nav_payload())})
        client.check_login()
        self.assertIn("SESSDATA=test-sessdata", transport.headers_seen[0]["Cookie"])


class SignedRequestTests(unittest.TestCase):
    def test_carries_wts_and_wrid(self):
        client, transport = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(fav_payload([]))}
        )
        client.fav_page(12345678)
        url = transport.calls_matching(FAV)[0]
        self.assertIn("w_rid=", url)
        self.assertIn("wts=", url)
        self.assertIn("media_id=12345678", url)

    def test_parameters_are_sorted(self):
        client, transport = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(fav_payload([]))}
        )
        client.fav_page(12345678)
        url = transport.calls_matching(FAV)[0]
        self.assertLess(url.index("pn="), url.index("ps="))
        self.assertLess(url.index("media_id="), url.index("order="))

    def test_page_size_is_capped_at_20(self):
        """B 站对 ps 有硬上限,配置更大也不能真的发出去。"""
        client, transport = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(fav_payload([]))},
            page_size=100,
        )
        client.fav_page(1)
        self.assertIn("ps=20", transport.calls_matching(FAV)[0])


class ErrorMappingTests(unittest.TestCase):
    def test_auth_required(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(api_error(-101, "账号未登录"))}
        )
        with self.assertRaises(AuthRequiredError):
            client.fav_page(1)

    def test_permission_denied(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(api_error(-403, "访问权限不足"))}
        )
        with self.assertRaises(PermissionDeniedError) as ctx:
            client.fav_page(12345678)
        self.assertEqual(ctx.exception.code, -403)

    def test_risk_control(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(api_error(-352, "风控校验失败"))}
        )
        with self.assertRaises(RiskControlError):
            client.fav_page(1)

    def test_not_found(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(api_error(-404, "啥都木有"))}
        )
        with self.assertRaises(NotFoundError):
            client.fav_page(1)

    def test_unknown_code_is_generic_api_error(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(api_error(-999, "未知"))}
        )
        with self.assertRaises(ApiError) as ctx:
            client.fav_page(1)
        self.assertNotIsInstance(ctx.exception, (AuthRequiredError, PermissionDeniedError))

    def test_http_500_is_network_error(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: HttpResponse(status=500, body="")}
        )
        with self.assertRaises(NetworkError):
            client.fav_page(1)

    def test_http_412_is_risk_control(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: HttpResponse(status=412, body="")}
        )
        with self.assertRaises(RiskControlError):
            client.fav_page(1)

    def test_non_json_is_invalid_response(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FAV: HttpResponse(status=200, body="<html>风控</html>")}
        )
        with self.assertRaises(InvalidResponseError):
            client.fav_page(1)


class SignatureRetryTests(unittest.TestCase):
    def test_refreshes_keys_and_retries_once(self):
        """-403 且消息含"签名"时,刷新 WBI 密钥重试;权限类 -403 不该重试。"""
        other_key = IMG_KEY[:10] + ("0" if IMG_KEY[10] != "0" else "1") + IMG_KEY[11:]
        client, transport = make_client(
            {
                NAV: [json_response(nav_payload()), json_response(nav_payload(img_key=other_key))],
                FAV: [json_response(api_error(-403, "签名错误")), json_response(fav_payload([]))],
            }
        )
        page = client.fav_page(1)
        self.assertEqual(len(page.videos), 0)
        self.assertEqual(len(transport.calls_matching(NAV)), 2, "应重新取一次密钥")
        self.assertEqual(len(transport.calls_matching(FAV)), 2, "应重试一次")

    def test_signature_failure_twice_raises_signature_error(self):
        client, _ = make_client(
            {
                NAV: [json_response(nav_payload()), json_response(nav_payload())],
                FAV: [
                    json_response(api_error(-403, "签名错误")),
                    json_response(api_error(-403, "签名错误")),
                ],
            }
        )
        with self.assertRaises(SignatureError):
            client.fav_page(1)

    def test_permission_403_is_not_retried(self):
        client, transport = make_client(
            {NAV: json_response(nav_payload()), FAV: json_response(api_error(-403, "访问权限不足"))}
        )
        with self.assertRaises(PermissionDeniedError):
            client.fav_page(1)
        self.assertEqual(len(transport.calls_matching(FAV)), 1, "权限错误重试没有意义")

    def test_voucher_triggers_retry(self):
        """code=0 但带 v_voucher 是伪成功,必须识别并重试。"""
        client, transport = make_client(
            {
                NAV: [json_response(nav_payload()), json_response(nav_payload())],
                FAV: [json_response(voucher_payload()), json_response(fav_payload([]))],
            }
        )
        page = client.fav_page(1)
        self.assertEqual(len(page.videos), 0)
        self.assertEqual(len(transport.calls_matching(FAV)), 2)

    def test_voucher_twice_becomes_risk_control(self):
        client, _ = make_client(
            {
                NAV: [json_response(nav_payload()), json_response(nav_payload())],
                FAV: [json_response(voucher_payload()), json_response(voucher_payload())],
            }
        )
        with self.assertRaises(RiskControlError):
            client.fav_page(1)


class FoldersTests(unittest.TestCase):
    def test_paginates_and_dedupes(self):
        client, transport = make_client(
            {
                NAV: json_response(nav_payload()),
                FOLDERS: [
                    json_response(
                        folders_payload(
                            [folder_item(1, "默认", 10), folder_item(2, "私密", 3, attr=1)],
                            has_more=True,
                        )
                    ),
                    json_response(
                        folders_payload([folder_item(2, "私密", 3), folder_item(3, "合集", 7)])
                    ),
                ],
            }
        )
        folders = client.folders(87654321)
        self.assertEqual([folder.fid for folder in folders], [1, 2, 3])
        self.assertEqual(len(transport.calls_matching(FOLDERS)), 2)

    def test_empty_result(self):
        client, _ = make_client(
            {NAV: json_response(nav_payload()), FOLDERS: json_response(folders_payload([]))}
        )
        self.assertEqual(client.folders(87654321), ())


class AllFavoritesTests(unittest.TestCase):
    def test_aggregates_pages(self):
        client, transport = make_client(
            {
                NAV: json_response(nav_payload()),
                FAV: [
                    json_response(
                        fav_payload([media_item(1, "BV1", "一")], has_more=True, total=2)
                    ),
                    json_response(
                        fav_payload([media_item(2, "BV2", "二"), {"id": 0, "bvid": ""}],
                                    has_more=False, total=2)
                    ),
                ],
            }
        )
        progress_calls = []
        videos, total = client.all_favorites(1, progress=lambda *args: progress_calls.append(args))
        self.assertEqual([video.bvid for video in videos], ["BV1", "BV2"])
        self.assertEqual(total, 2)
        self.assertEqual(progress_calls, [(1, 1, 2), (2, 2, 2)])
        self.assertEqual(len(transport.calls_matching(FAV)), 2)

    def test_stops_on_empty_page(self):
        client, transport = make_client(
            {
                NAV: json_response(nav_payload()),
                FAV: [
                    json_response(fav_payload([media_item(1, "BV1", "一")], has_more=True)),
                    json_response(fav_payload([], has_more=True)),
                ],
            }
        )
        videos, _total = client.all_favorites(1)
        self.assertEqual(len(videos), 1)
        self.assertEqual(len(transport.calls_matching(FAV)), 2, "空页应立即停止,不再请求")

    def test_respects_max_pages(self):
        client, transport = make_client(
            {
                NAV: json_response(nav_payload()),
                FAV: json_response(fav_payload([media_item(1, "BV1", "一")], has_more=True)),
            },
            max_pages=3,
        )
        client.all_favorites(1)
        self.assertEqual(len(transport.calls_matching(FAV)), 3)


class SetCookiesTests(unittest.TestCase):
    def test_clearing_cookies_removes_header(self):
        client, transport = make_client({NAV: json_response(nav_payload())})
        client.set_cookies({})
        client.check_login()
        self.assertNotIn("Cookie", transport.headers_seen[0])

    def test_invalidates_cached_keys(self):
        client, transport = make_client({NAV: json_response(nav_payload())})
        client.wbi_keys()
        client.set_cookies({"SESSDATA": "new"})
        client.wbi_keys()
        self.assertEqual(len(transport.calls_matching(NAV)), 2)


if __name__ == "__main__":
    unittest.main()
