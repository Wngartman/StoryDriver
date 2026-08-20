@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0deliberate_pipeline_static_test.py"
) else (
    py -3 "%~dp0deliberate_pipeline_static_test.py"
)
exit /b %errorlevel%
