@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0background_rotation_test.py"
exit /b %errorlevel%
