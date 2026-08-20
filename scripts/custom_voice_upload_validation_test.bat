@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\custom_voice_upload_validation_test.py
exit /b %ERRORLEVEL%
