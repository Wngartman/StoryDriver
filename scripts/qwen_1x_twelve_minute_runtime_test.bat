@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\qwen_runtime_orchestrator.py --minutes 12 --port 8895 --custom-voice --automatic-direction
exit /b %ERRORLEVEL%
