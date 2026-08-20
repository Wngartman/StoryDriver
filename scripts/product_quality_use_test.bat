@echo off
setlocal
set "ROOT=%~dp0.."
set "TMP=%ROOT%\backend\data\temp"
set "TEMP=%ROOT%\backend\data\temp"
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0product_quality_use_test.py" %*
exit /b %errorlevel%
