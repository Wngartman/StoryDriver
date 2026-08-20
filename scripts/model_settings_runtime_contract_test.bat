@echo off
setlocal
set "ROOT=%~dp0.."
call "%~dp0settings_contract_test.bat"
if errorlevel 1 exit /b %errorlevel%
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0model_settings_runtime_contract_test.py"
exit /b %errorlevel%
