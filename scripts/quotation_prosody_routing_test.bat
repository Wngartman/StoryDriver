@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tts_refinement_contract_test.py prosody
if errorlevel 1 exit /b %ERRORLEVEL%
node --test frontend\src\services\ttsPremiumPlan.test.js
exit /b %ERRORLEVEL%
