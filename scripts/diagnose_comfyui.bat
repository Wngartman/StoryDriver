@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0comfyui_diagnostics.py" diagnose
) else (
    py -3 "%~dp0comfyui_diagnostics.py" diagnose
)
exit /b %errorlevel%
