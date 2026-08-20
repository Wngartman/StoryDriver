@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tests\qwen_startup_evidence_check.py cache
exit /b %ERRORLEVEL%
