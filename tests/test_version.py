"""版本号一致性测试:两处版本必须一致,且符合 semver 规则。"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

from bilifocus import __version__

ROOT = Path(__file__).resolve().parent.parent


def load_bump_module():
    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "dev" / "bump_version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionTests(unittest.TestCase):
    def test_pyproject_matches_package(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version = re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)
        self.assertEqual(version, __version__)

    def test_semver_shape(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_bump_rules(self):
        bump = load_bump_module()
        # 修 bug:第二位 +1
        self.assertEqual(bump.next_version("2.1.0", "patch"), "2.1.1")
        self.assertEqual(bump.next_version("2.1.9", "patch"), "2.1.10")
        # 加功能:第一位 +1,第二位归零
        self.assertEqual(bump.next_version("2.1.1", "minor"), "2.2.0")
        self.assertEqual(bump.next_version("2.9.3", "minor"), "2.10.0")
        # 不兼容大改
        self.assertEqual(bump.next_version("2.10.0", "major"), "3.0.0")
        self.assertEqual(bump.next_version("2.1.0", "major"), "3.0.0")

    def test_unknown_kind_raises(self):
        bump = load_bump_module()
        with self.assertRaises(ValueError):
            bump.next_version("2.1.0", "oops")


if __name__ == "__main__":
    unittest.main()
