@echo off
setlocal
cd /d "%~dp0\.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\storydriver_cleanup.py" %*
) else (
  py -3 "scripts\storydriver_cleanup.py" %*
)
exit /b %errorlevel%
