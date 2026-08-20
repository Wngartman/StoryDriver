@echo off
setlocal
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\reviewer_adversarial_test.py
exit /b %ERRORLEVEL%
