@echo off
chcp 936 >nul
rem 双击运行卡片实验室。出错时会停住,方便复制报错。
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo 找不到 Python,请先确认 python 能用。
  pause
  goto :done
)

python -X utf8 dev\lab_card.py
if errorlevel 1 (
  echo.
  echo ================================================
  echo  运行出错了。把上面的报错文字整段复制下来即可。
  echo ================================================
  pause
)

:done
endlocal
