@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\automatic_narration_direction_test.py
if errorlevel 1 exit /b %ERRORLEVEL%
node --test frontend\src\services\ttsPremiumPlan.test.js
exit /b %ERRORLEVEL%
