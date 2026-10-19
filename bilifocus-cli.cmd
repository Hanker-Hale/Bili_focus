@echo off
rem BiliFocus 命令行启动器
rem 用法:bilifocus-cli.cmd list --fid <收藏夹id>
setlocal
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo 找不到 Python,请安装 Python 3.10+ 并勾选 Add python.exe to PATH。
  pause
  goto :done
)

%PY% -m bilifocus %*

:done
endlocal
