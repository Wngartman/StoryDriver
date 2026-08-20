@echo off
setlocal
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\automatic_breath_api_test.py
exit /b %errorlevel%
