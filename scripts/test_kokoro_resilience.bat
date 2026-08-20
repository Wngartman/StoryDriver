@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\test_kokoro_resilience.py %*
