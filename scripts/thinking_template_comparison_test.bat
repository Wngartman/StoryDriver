@echo off
setlocal

cd /d "%~dp0\.."

set "PYTHON_EXE=%CD%\backend\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" "%CD%\scripts\thinking_template_comparison_test.py" %*
exit /b %ERRORLEVEL%
