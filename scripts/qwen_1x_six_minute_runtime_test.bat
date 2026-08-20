@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\qwen_runtime_orchestrator.py --minutes 6 --port 8895
exit /b %ERRORLEVEL%
