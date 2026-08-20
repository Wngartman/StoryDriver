@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\ui_preset_manager_smoke_test.py
exit /b %ERRORLEVEL%
