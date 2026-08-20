@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0motion_reduced_motion_test.py"
exit /b %errorlevel%
