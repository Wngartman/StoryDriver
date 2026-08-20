@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\automatic_narration_api_test.py
exit /b %ERRORLEVEL%
