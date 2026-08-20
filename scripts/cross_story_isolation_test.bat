@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tests\story_isolation_title_qwen_regression.py cross_story
exit /b %ERRORLEVEL%
