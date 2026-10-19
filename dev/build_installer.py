#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打 Windows 安装包:dist/BiliFocus-Setup-<版本>.exe

分成两步:

1. `dev/build_exe.py` 打出 `dist/BiliFocus.exe`(程序本体);
2. 把本体作为 payload 塞进一个带 Tk 向导的安装器,再打成单文件安装包。

    python dev/build_installer.py
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
sys.path.insert(0, str(ROOT))

from bilifocus import install  # noqa: E402

VERSION = install.version()
APP_EXE = ROOT / "dist" / install.EXE_NAME
INSTALLER_NAME = f"BiliFocus-Setup-{VERSION}"
SETUP_EXE = ROOT / "dist" / f"{INSTALLER_NAME}.exe"
BUILD_DIR = ROOT / "build-installer"


def run(command: list[str]) -> int:
    print(" ".join(command[:6]), "…")
    return subprocess.run(command, cwd=ROOT).returncode


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("没装 PyInstaller,先运行:python -m pip install pyinstaller")
        return 1

    # 1) 先确认程序本体是最新的
    if not APP_EXE.is_file():
        print("还没打程序本体,先跑 dev/build_exe.py")
        return 1

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    icon = ROOT / "assets" / "bilifocus-icon.ico"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        INSTALLER_NAME,
        "--icon",
        str(icon),
        "--add-data",
        f"{APP_EXE}{';'}payload",          # 程序本体作为 payload
        "--add-data",
        f"{icon.parent}{';'}assets",        # 安装器自己的图标
        "--paths",
        str(ROOT),
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
        str(ROOT / "dev" / "installer_app.py"),
    ]
    print(f"开始打包安装包 {INSTALLER_NAME} …")
    if run(command) != 0:
        print("打包失败。")
        return 1

    if not SETUP_EXE.is_file():
        print("没找到安装包产物?")
        return 1
    size_mb = SETUP_EXE.stat().st_size / 1024 / 1024
    print(f"完成:{SETUP_EXE}({size_mb:.1f} MB)")
    print("用户双击 → 点「安装」即可;卸载在 Windows 设置 → 应用里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
