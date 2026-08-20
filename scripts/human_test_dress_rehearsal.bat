@echo off
setlocal
cd /d "%~dp0\.."

set "PYTHON=%CD%\backend\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo StoryDriver backend venv Python was not found: %PYTHON%
  exit /b 1
)

"%PYTHON%" "%CD%\scripts\human_test_dress_rehearsal.py" %*
