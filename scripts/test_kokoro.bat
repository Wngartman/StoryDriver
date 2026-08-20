@echo off
setlocal
set "ROOT=D:\StoryDriver"
set "PY=%ROOT%\backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%ROOT%\scripts\kokoro_smoke_test.py"
exit /b %ERRORLEVEL%
