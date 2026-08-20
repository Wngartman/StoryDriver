@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0wait_character_cleanup_test.py"
exit /b %errorlevel%
