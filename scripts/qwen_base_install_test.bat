@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tts_refinement_contract_test.py base-install
exit /b %ERRORLEVEL%
