@echo off
setlocal
cd /d "%~dp0.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\real_story_quality_shakedown.py" %*
) else (
  python "scripts\real_story_quality_shakedown.py" %*
)
exit /b %ERRORLEVEL%
