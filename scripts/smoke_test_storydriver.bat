@echo off
setlocal
set "ROOT=D:\StoryDriver"
set "PY=%ROOT%\backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%ROOT%\scripts\smoke_test_storydriver.py"
exit /b %ERRORLEVEL%
