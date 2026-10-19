@echo off
rem BiliFocus 图形界面启动器(双击用)
rem 可选参数:--fid <收藏夹id>  --mid <你的mid>
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

%PY% -m bilifocus.ui_tk %*

:done
endlocal
