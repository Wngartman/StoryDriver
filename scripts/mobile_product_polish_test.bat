@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%~dp0mobile_product_polish_test.py"
exit /b %errorlevel%
