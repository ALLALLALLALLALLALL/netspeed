@echo off
cd /d "%~dp0"
set "PY=C:\Users\23250\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if exist "%PY%" (
  "%PY%" server.py
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python server.py
  ) else (
    echo 没有找到 Python，请修改本文件里的 PY 路径。
    pause
  )
)
pause
