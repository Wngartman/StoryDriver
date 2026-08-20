@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0tts_latency_smoke_test.py" %*
) else (
    py -3 "%~dp0tts_latency_smoke_test.py" %*
)
exit /b %errorlevel%
