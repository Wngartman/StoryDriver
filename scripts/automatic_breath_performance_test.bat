@echo off
setlocal
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\automatic_breath_performance_test.py
exit /b %errorlevel%
