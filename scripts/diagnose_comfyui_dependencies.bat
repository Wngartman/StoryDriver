@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"

if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0comfyui_dependency_check.py"
) else (
    py -3 "%~dp0comfyui_dependency_check.py"
)

exit /b %errorlevel%
