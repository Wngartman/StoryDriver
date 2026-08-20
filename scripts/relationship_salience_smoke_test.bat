@echo off
setlocal
cd /d "%~dp0.."

if not exist "backend\.venv\Scripts\python.exe" (
  echo StoryDriver backend venv was not found at backend\.venv\Scripts\python.exe
  exit /b 1
)

set "PYTHONHOME="
set "PYTHONPATH="

"backend\.venv\Scripts\python.exe" "scripts\relationship_salience_smoke_test.py"
exit /b %ERRORLEVEL%
