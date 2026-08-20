@echo off
setlocal
cd /d "%~dp0.."
backend\.venv\Scripts\python.exe scripts\auto_story_title_static_test.py
exit /b %ERRORLEVEL%
