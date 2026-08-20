@echo off
setlocal
cd /d "%~dp0.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\state_prompt_integration_smoke_test.py"
) else (
  python "scripts\state_prompt_integration_smoke_test.py"
)
exit /b %ERRORLEVEL%
