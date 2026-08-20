@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0invalid_character_extraction_test.py"
exit /b %errorlevel%
