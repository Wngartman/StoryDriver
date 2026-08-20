@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\qwen_custom_voice_acceptance.py
exit /b %ERRORLEVEL%
