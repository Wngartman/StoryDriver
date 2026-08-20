@echo off
setlocal
cd /d "%~dp0\.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\model_routing_smoke_test.py"
) else (
  python "scripts\model_routing_smoke_test.py"
)
exit /b %ERRORLEVEL%
