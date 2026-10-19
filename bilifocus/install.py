"""安装 / 卸载逻辑(安装器和程序本体共用)。

设计:

- 装到当前用户的 `%LOCALAPPDATA%\\Programs\\BiliFocus`,**不需要管理员权限**;
- 只写 HKCU 的卸载注册表项(控制面板/设置里能看到、能卸载);
- 桌面 + 开始菜单快捷方式;
- 数据一直在 `%APPDATA%\\bili-focus`,**覆盖安装不动数据**,卸载默认也不删。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import sys
import time
import winreg
from pathlib import Path

from . import winproc

APP_NAME = "BiliFocus"
EXE_NAME = "BiliFocus.exe"
PUBLISHER = "BiliFocus"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "0.0.0"


def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_NAME


def data_dir() -> Path:
    """程序运行数据(登录态、缓存、日程),覆盖安装/卸载都不该删。"""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "bili-focus"


def installed_exe() -> Path:
    return install_dir() / EXE_NAME


def is_installed() -> bool:
    return installed_exe().is_file()


def installed_version() -> str | None:
    marker = install_dir() / "VERSION.txt"
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def desktop_shortcut() -> Path:
    desktop = Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Desktop"
    return desktop / f"{APP_NAME}.lnk"


def start_menu_shortcut() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


# -- 内部工具 --------------------------------------------------------------
def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def make_shortcut(lnk: Path, target: Path, icon: Path, workdir: Path, description: str) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(lnk)});"
        f"$s.TargetPath = {_ps_quote(target)};"
        f"$s.WorkingDirectory = {_ps_quote(workdir)};"
        f"$s.IconLocation = {_ps_quote(str(icon) + ',0')};"
        f"$s.Description = {_ps_quote(description)};"
        f"$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
        timeout=60,
    )


def remove_path(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        pass


def _write_registry() -> None:
    exe = installed_exe()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version())
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir()))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, f"{exe},0")
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{exe}" --uninstall')
        winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, f'"{exe}" --uninstall --silent')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        try:
            winreg.SetValueEx(key, "InstallDate", 0, winreg.REG_SZ, time.strftime("%Y%m%d"))
        except OSError:
            pass


def _remove_registry() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass
    except OSError:
        pass


# -- 正在运行的旧版本 ------------------------------------------------------
def _decode_console(data: bytes | str | None) -> str:
    """控制台输出可能是 GBK(中文 Windows),统一转成字符串。"""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    for encoding in ("mbcs", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "ignore")


def running_app_pids() -> list[int]:
    """列出正在运行的 BiliFocus.exe(不含安装器自己)。"""
    try:
        completed = winproc.run_hidden(
            ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    output = _decode_console(completed.stdout)
    pids: list[int] = []
    current = os.getpid()
    for line in (output or "").splitlines():
        line = line.strip()
        if not line or not line.startswith('"'):
            continue
        parts = [part.strip('"') for part in line.split('","')]
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        if pid != current:
            pids.append(pid)
    return pids


def close_running_app(*, force: bool = False, wait: float = 3.0) -> bool:
    """关掉正在运行的本体;force=False 时只发关闭请求。"""
    pids = running_app_pids()
    if not pids:
        return True
    for pid in pids:
        if force:
            winproc.taskkill(pid)
        else:
            try:
                winproc.run_hidden(
                    ["taskkill", "/PID", str(pid), "/T"], capture_output=True, timeout=10
                )
            except (OSError, subprocess.SubprocessError):
                pass
    for pid in pids:
        if not winproc.wait_pid_gone(pid, timeout=wait):
            return False
    return True


# -- 对外:安装 / 卸载 ------------------------------------------------------
def install(
    payload: Path,
    *,
    desktop: bool = True,
    launch: bool = False,
    progress=None,
) -> Path:
    """把 payload(打包好的 BiliFocus.exe)装到本机,返回安装目录。"""
    target_dir = install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / EXE_NAME

    running = running_app_pids()
    if running:
        if progress:
            progress(f"检测到 {APP_NAME} 正在运行,先关闭它…")
        if not close_running_app(force=True):
            raise OSError(f"无法关闭正在运行的 {APP_NAME},请手动退出后重试")

    if progress:
        progress("正在复制程序…")
    temporary = target.with_suffix(".tmp")
    shutil.copy2(payload, temporary)
    try:
        os.replace(temporary, target)  # 覆盖安装:先复制再原子替换
    except OSError:
        remove_path(temporary)
        raise

    if progress:
        progress("正在写版本信息…")
    (target_dir / "VERSION.txt").write_text(version(), encoding="utf-8")

    icon = target
    if progress:
        progress("正在创建快捷方式…")
    if desktop:
        make_shortcut(desktop_shortcut(), target, icon, target_dir, f"{APP_NAME} — 只看收藏夹")
    make_shortcut(start_menu_shortcut(), target, icon, target_dir, f"{APP_NAME} — 只看收藏夹")

    if progress:
        progress("正在登记卸载信息…")
    _write_registry()

    if launch:
        if progress:
            progress("正在启动…")
        try:
            subprocess.Popen([str(target)], cwd=str(target_dir))
        except OSError:
            pass
    return target_dir


def uninstall(*, remove_data: bool = False, progress=None) -> None:
    """删除程序、快捷方式、注册表项;默认保留用户数据。"""
    if progress:
        progress("正在删除快捷方式…")
    remove_path(desktop_shortcut())
    remove_path(start_menu_shortcut())

    if progress:
        progress("正在删除卸载信息…")
    _remove_registry()

    target_dir = install_dir()
    if progress:
        progress("正在删除程序文件…")
    # 自己删不掉自己:写一个后台批处理,等进程退出后再删目录(带重试)
    running_inside = getattr(sys, "frozen", False) and Path(
        sys.executable
    ).resolve().is_relative_to(target_dir.resolve())
    if running_inside:
        script = Path(tempfile.gettempdir()) / f"{APP_NAME.lower()}-uninstall-{os.getpid()}.cmd"
        script.write_text(
            "@echo off\r\n"
            f'ping -n 3 127.0.0.1 >nul\r\n'
            f'for /l %%i in (1,1,30) do (\r\n'
            f'  rmdir /s /q "{target_dir}" 2>nul\r\n'
            f'  if not exist "{target_dir}" goto done\r\n'
            f'  ping -n 2 127.0.0.1 >nul\r\n'
            f')\r\n'
            f':done\r\n'
            f'del "%~f0" >nul 2>nul\r\n',
            encoding="gbk",
            errors="replace",
        )
        subprocess.Popen(
            ["cmd", "/c", str(script)],
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        )
    else:
        shutil.rmtree(target_dir, ignore_errors=True)

    if remove_data:
        if progress:
            progress("正在删除本机数据…")
        shutil.rmtree(data_dir(), ignore_errors=True)


def silent_install(payload: Path) -> int:
    """给 CI/测试用的静默安装。"""
    try:
        directory = install(payload, desktop=True, launch=False)
    except OSError as error:
        print(f"安装失败:{error}")
        return 1
    print(f"已安装到 {directory}(版本 {version()})")
    return 0
