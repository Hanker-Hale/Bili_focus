#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 FavFocus 打包成单文件 exe(Windows)。

用法(仓库根目录):

    python dev/build_exe.py

产物:`dist/FavFocus.exe`(双击运行,不需要装 Python)。

需要先装 PyInstaller:

    python -m pip install pyinstaller
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# CI 的 Windows 控制台可能是 cp1252,中文 print 会直接抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "assets" / "favfocus-icon.ico"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("没装 PyInstaller,先运行:python -m pip install pyinstaller")
        return 1

    if not ICON.is_file():
        print(f"找不到图标:{ICON}")
        return 1

    # 干净打包,避免上一次的缓存混进来
    for path in (BUILD, DIST):
        if path.exists():
            shutil.rmtree(path)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",              # 打成单个 exe
        "--windowed",              # 双击不弹黑框
        "--name", "FavFocus",
        "--icon", str(ICON),
        "--add-data", f"{ICON.parent}{';'}{ICON.parent.name}",  # 图标进包,运行时能取到
        "--collect-submodules", "favfocus",                      # 懒加载的模块也收进来
        "--paths", str(ROOT),
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        str(ROOT / "favfocus.py"),   # 入口(兼容壳 → ui_tk.main)
    ]
    print("开始打包…")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败,看上面的输出。")
        return result.returncode

    exe = DIST / "FavFocus.exe"
    if not exe.is_file():
        print("打包似乎成功,但没找到 exe?")
        return 1
    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"完成:{exe}({size_mb:.1f} MB)")
    print("双击即可运行,不需要装 Python。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
