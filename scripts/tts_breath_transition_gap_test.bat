@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tests\story_isolation_title_qwen_regression.py breath
if errorlevel 1 exit /b %ERRORLEVEL%
call scripts\automatic_breath_performance_test.bat
exit /b %ERRORLEVEL%
