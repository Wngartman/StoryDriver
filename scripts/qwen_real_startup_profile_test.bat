@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\qwen_real_startup_profile_test.py
exit /b %ERRORLEVEL%
