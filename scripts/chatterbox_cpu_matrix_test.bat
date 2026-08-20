@echo off
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\chatterbox_gate_summary.py --check matrix
exit /b %ERRORLEVEL%
