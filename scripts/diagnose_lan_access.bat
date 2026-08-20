@echo off
setlocal

cd /d "%~dp0.."

if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\diagnose_lan_access.py" %*
) else (
  py -3 "scripts\diagnose_lan_access.py" %*
)

exit /b %errorlevel%
