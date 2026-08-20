@echo off
setlocal
cd /d "%~dp0\.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\prose_v3_static_test.py"
) else (
  python "scripts\prose_v3_static_test.py"
)
exit /b %ERRORLEVEL%
