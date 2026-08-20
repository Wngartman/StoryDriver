@echo off
setlocal
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\planner_latency_profile_test.py %*
exit /b %ERRORLEVEL%
