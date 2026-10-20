#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按项目规则升版本号。

规则(作者约定):

- 修 bug          → 第二段 +1:2.1.0 → 2.1.1
- 增加功能        → 第一段 +1,第二段归零:2.1.1 → 2.2.0
- 不兼容的大改版  → 第一段 +1(极少用):2.x.x → 3.0.0
- 网页 / 文案 / 纯视觉调整(配色、图标、截图、排版)
                  → **不升版本**:功能没变就不用重新打包、不用打 tag

用法(仓库根目录):

    python dev/bump_version.py patch    # 修 bug 后
    python dev/bump_version.py minor    # 加功能后
    python dev/bump_version.py --show   # 只打印当前版本

会同时改 `pyproject.toml` 和 `bilifocus/__init__.py` 两处,并提示下一步。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_INIT = ROOT / "bilifocus" / "__init__.py"
KINDS = ("patch", "minor", "major")


def next_version(current: str, kind: str) -> str:
    """算出下一个版本号;不修改任何文件。"""
    parts = [int(part) for part in current.split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    if kind == "patch":
        patch += 1
    elif kind == "minor":
        minor += 1
        patch = 0
    elif kind == "major":
        major += 1
        minor = patch = 0
    else:
        raise ValueError(f"未知的版本类型:{kind}")
    return f"{major}.{minor}.{patch}"


def read_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml 里没找到 version")
    return match.group(1)


def write_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(r'^version = "[^"]+"', f'version = "{version}"', text, count=1, flags=re.MULTILINE)
    PYPROJECT.write_text(text, encoding="utf-8")

    text = PACKAGE_INIT.read_text(encoding="utf-8")
    text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text, count=1)
    PACKAGE_INIT.write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    kind = argv[0].lstrip("-")
    if kind == "show":
        print(read_version())
        return 0
    if kind not in KINDS:
        print(f"用法:python dev/bump_version.py patch|minor|major|--show")
        return 2

    current = read_version()
    version = next_version(current, kind)
    write_version(version)
    print(f"版本号:{current} → {version}  ({kind})")
    print("下一步:")
    print("  python dev/build_exe.py && python dev/build_installer.py   # 重新打包")
    print(f"  git tag v{version} && git push origin v{version}            # 发版(CI 自动挂 Release)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
