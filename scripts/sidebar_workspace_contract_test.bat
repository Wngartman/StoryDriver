@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0sidebar_workspace_contract_test.py"
exit /b %errorlevel%
