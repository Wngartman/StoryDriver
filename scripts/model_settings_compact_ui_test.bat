@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tts_refinement_contract_test.py model-settings
if errorlevel 1 exit /b %ERRORLEVEL%
call scripts\model_routing_smoke_test.bat
exit /b %ERRORLEVEL%
