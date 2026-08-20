@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0scene_contract_blocking_static_test.py"
) else (
    py -3 "%~dp0scene_contract_blocking_static_test.py"
)
exit /b %errorlevel%
