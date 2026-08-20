@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0easter_egg_safety_test.py"
exit /b %errorlevel%
