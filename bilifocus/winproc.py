"""Windows 上"静默执行外部命令"的小工具。

背景:播放器需要偶尔调 `powershell` / `taskkill` 查/清理自己的浏览器进程。
如果直接 `subprocess.run(...)`,Windows 会给控制台程序弹一个黑窗口;
全屏看视频时这个窗口还会抢焦点,把全屏踢出去(用户看到的就是"视频退出了")。

所以所有这类调用都必须带上 `CREATE_NO_WINDOW`,再配一个隐藏的 STARTUPINFO 兜底。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0


def hidden_startupinfo():
    """给控制台子进程用的隐藏窗口参数(非 Windows 返回 None)。"""
    if sys.platform != "win32":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= STARTF_USESHOWWINDOW
    info.wShowWindow = SW_HIDE
    return info


def run_hidden(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """跑一条命令,保证不弹窗。"""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    if "startupinfo" not in kwargs and sys.platform == "win32":
        kwargs["startupinfo"] = hidden_startupinfo()
    return subprocess.run(command, **kwargs)


def run_detached(command: list[str], **kwargs) -> subprocess.Popen:
    """完全脱离当前进程跑一条命令(不等待、不弹窗)。"""
    kwargs.setdefault("creationflags", DETACHED_PROCESS | CREATE_NO_WINDOW)
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    return subprocess.Popen(command, **kwargs)


def taskkill(pid: int, timeout: int = 15) -> None:
    """结束进程树(静默,不弹窗)。"""
    try:
        run_hidden(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def wait_pid_gone(pid: int, timeout: float = 3.0) -> bool:
    """等某个进程真正退出(避免"旧的浏览器还没死,新实例抢不到 profile")。"""
    if sys.platform != "win32":
        return True
    PROCESS_SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_SYNCHRONIZE, False, int(pid))
    if not handle:
        return True  # 进程已经不在了
    try:
        result = kernel32.WaitForSingleObject(handle, int(max(0.0, timeout) * 1000))
        return result == 0
    finally:
        kernel32.CloseHandle(handle)
