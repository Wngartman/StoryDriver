@echo off
setlocal
cd /d "%~dp0\.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\latency_smoke_test.py" %*
) else (
  python "scripts\latency_smoke_test.py" %*
)
exit /b %ERRORLEVEL%
