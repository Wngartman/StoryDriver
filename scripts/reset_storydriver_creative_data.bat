@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo StoryDriver backend Python was not found: %PYTHON%
  exit /b 1
)

"%PYTHON%" "%~dp0reset_storydriver_creative_data.py" %*
exit /b %errorlevel%
