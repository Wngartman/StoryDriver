@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\quick_delete_thinking_tts_highlight_smoke_test.py
