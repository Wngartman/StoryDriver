@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tests\story_isolation_title_qwen_regression.py narration_scope
if errorlevel 1 exit /b %ERRORLEVEL%
backend\.venv\Scripts\python.exe scripts\tests\qwen_startup_evidence_check.py first
exit /b %ERRORLEVEL%
