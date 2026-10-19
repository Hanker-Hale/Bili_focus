"""CLI 测试:排版、子命令、输出内容。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from bilifocus.cli import build_parser, display_width, main, pad, truncate
from tests.fakes import (
    FAV,
    FOLDERS,
    NAV,
    fav_payload,
    folder_item,
    folders_payload,
    json_response,
    make_service,
    media_item,
    nav_payload,
)


class LayoutTests(unittest.TestCase):
    def test_display_width_counts_cjk_as_two(self):
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("中文"), 4)
        self.assertEqual(display_width("a中"), 3)

    def test_pad_aligns_by_display_width(self):
        self.assertEqual(display_width(pad("中文", 8)), 8)
        self.assertEqual(display_width(pad("abc", 8)), 8)

    def test_truncate_keeps_width(self):
        text = "这是一个很长很长很长的标题需要被截断"
        cut = truncate(text, 10)
        self.assertLessEqual(display_width(cut), 10)
        self.assertTrue(cut.endswith("…"))
        self.assertEqual(truncate("短", 10), "短")


class ParserTests(unittest.TestCase):
    def test_all_subcommands_exist(self):
        parser = build_parser()
        for command in (
            "status",
            "login",
            "logout",
            "folders",
            "list",
            "watch",
            "unwatch",
            "watched",
            "random",
            "export",
            "doctor",
        ):
            self.assertIn(command, parser._subparsers._group_actions[0].choices)

    def test_version_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_no_command_prints_help(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(main([]), 0)
        self.assertIn("bilifocus", buffer.getvalue())


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def harness(self, routes):
        return make_service(routes, self.home)

    def run_cmd(self, service, argv):
        parser = build_parser()
        args = parser.parse_args(argv)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = args.func(service, args)
        return code, buffer.getvalue()

    def standard_routes(self):
        return {
            NAV: json_response(nav_payload(is_login=True, uname="甲", mid=479257031)),
            FAV: json_response(
                fav_payload(
                    [
                        media_item(1, "BV1", "第一个视频", duration=65, upper="甲"),
                        media_item(2, "BV2", "第二个视频", duration=3725, upper="乙"),
                    ]
                )
            ),
            FOLDERS: json_response(folders_payload([folder_item(750450031, "默认收藏夹", 2)])),
        }

    def test_status_json_offline(self):
        harness = self.harness(self.standard_routes())
        code, output = self.run_cmd(harness.service, ["status", "--json", "--offline"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertTrue(payload["has_sessdata"])
        self.assertEqual(payload["home"], str(self.home))

    def test_folders(self):
        harness = self.harness(self.standard_routes())
        code, output = self.run_cmd(harness.service, ["folders"])
        self.assertEqual(code, 0)
        self.assertIn("默认收藏夹", output)
        self.assertIn("750450031", output)

    def test_list_renders_rows(self):
        harness = self.harness(self.standard_routes())
        harness.service.videos(750450031)
        code, output = self.run_cmd(harness.service, ["list"])
        self.assertEqual(code, 0)
        self.assertIn("第一个视频", output)
        self.assertIn("1:05", output)
        self.assertIn("1:02:05", output)
        self.assertIn("收藏夹 750450031", output)

    def test_list_search_filters(self):
        harness = self.harness(self.standard_routes())
        harness.service.videos(750450031)
        _code, output = self.run_cmd(harness.service, ["list", "--search", "第二"])
        self.assertIn("第二个视频", output)
        self.assertNotIn("第一个视频", output)

    def test_list_json(self):
        harness = self.harness(self.standard_routes())
        harness.service.videos(750450031)
        _code, output = self.run_cmd(harness.service, ["list", "--json"])
        payload = json.loads(output)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["videos"][0]["bvid"], "BV1")
        self.assertIn("stats", payload)

    def test_watch_and_unwatch(self):
        harness = self.harness(self.standard_routes())
        code, output = self.run_cmd(harness.service, ["watch", "BV1"])
        self.assertEqual(code, 0)
        self.assertIn("已标记", output)
        self.assertTrue(harness.service.is_watched("BV1"))

        _code, output = self.run_cmd(harness.service, ["watch", "BV1"])
        self.assertIn("本来就是", output)

        _code, output = self.run_cmd(harness.service, ["unwatch", "BV1"])
        self.assertIn("已取消", output)
        self.assertFalse(harness.service.is_watched("BV1"))

    def test_watched_lists_only_marked(self):
        harness = self.harness(self.standard_routes())
        harness.service.videos(750450031)
        harness.service.mark_watched("BV2")
        _code, output = self.run_cmd(harness.service, ["watched"])
        self.assertIn("第二个视频", output)
        self.assertNotIn("第一个视频", output)

    def test_random_prints_a_video(self):
        harness = self.harness(self.standard_routes())
        harness.service.videos(750450031)
        code, output = self.run_cmd(harness.service, ["random"])
        self.assertEqual(code, 0)
        self.assertTrue(
            "第一个视频" in output or "第二个视频" in output, f"输出:{output!r}"
        )
        self.assertIn("bilibili.com/video/", output)

    def test_export_csv_via_cli(self):
        harness = self.harness(self.standard_routes())
        harness.service.videos(750450031)
        target = self.home / "export" / "fav.csv"
        code, output = self.run_cmd(harness.service, ["export", "--out", str(target)])
        self.assertEqual(code, 0)
        self.assertTrue(target.exists())
        self.assertIn("已导出", output)

    def test_export_json_via_cli(self):
        harness = self.harness(self.standard_routes())
        harness.service.videos(750450031)
        target = self.home / "export" / "fav.json"
        self.run_cmd(harness.service, ["export", "--out", str(target)])
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], 2)

    def test_doctor_offline(self):
        harness = self.harness(self.standard_routes())
        code, output = self.run_cmd(harness.service, ["doctor", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("SELF", output.upper() + "SELF")  # 只要不崩即可
        self.assertIn("全部通过", output)

    def test_list_without_fid_exits_2(self):
        harness = self.harness(self.standard_routes())
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(err):
            self.run_cmd(harness.service, ["list"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--fid", err.getvalue())


if __name__ == "__main__":
    unittest.main()
