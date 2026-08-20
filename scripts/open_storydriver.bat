@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0storydriver_services.py" open-browser
) else (
    py -3 "%~dp0storydriver_services.py" open-browser
)
exit /b %errorlevel%
