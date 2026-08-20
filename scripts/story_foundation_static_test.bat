@echo off
setlocal
cd /d "%~dp0\.."
if not exist "backend\.venv\Scripts\python.exe" (
  echo StoryDriver backend venv was not found at backend\.venv\Scripts\python.exe
  exit /b 1
)
set "PYTHONPATH=%CD%\backend"
"backend\.venv\Scripts\python.exe" "scripts\story_foundation_static_test.py" %*
