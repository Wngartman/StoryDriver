@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0settings_tooltip_accessibility_test.py"
exit /b %errorlevel%
