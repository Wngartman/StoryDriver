@echo off
setlocal
cd /d "%~dp0.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\tts_realism_test.py" %*
) else (
  python "scripts\tts_realism_test.py" %*
)
set ERR=%ERRORLEVEL%
endlocal & exit /b %ERR%
