@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0inventory_memory_accuracy_test.py"
exit /b %errorlevel%
