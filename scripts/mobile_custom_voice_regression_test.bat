@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tts_refinement_contract_test.py mobile-ui
if errorlevel 1 exit /b %ERRORLEVEL%
call scripts\mobile_tts_chunk_resume_static_test.bat
exit /b %ERRORLEVEL%
