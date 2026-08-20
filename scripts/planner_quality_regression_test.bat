@echo off
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\planner_quality_regression_test.py
exit /b %ERRORLEVEL%
