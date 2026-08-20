@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0backend_query_profile_test.py"
exit /b %errorlevel%
