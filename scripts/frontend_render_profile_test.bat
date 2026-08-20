@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0frontend_render_profile_test.py"
exit /b %errorlevel%
