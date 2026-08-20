@echo off
setlocal
cd /d "%~dp0\.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\story_state_v2_continuity_static_test.py"
) else (
  python "scripts\story_state_v2_continuity_static_test.py"
)
exit /b %ERRORLEVEL%
