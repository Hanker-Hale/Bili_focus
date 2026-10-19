@echo off
rem 跑全部离线测试(不需要联网,也不需要 SESSDATA)
setlocal
cd /d "%~dp0.."

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

%PY% -m unittest discover -s tests -t . %*

:done
endlocal
