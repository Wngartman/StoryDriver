@echo off
setlocal
cd /d "%~dp0.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\mobile_tts_chunk_resume_static_test.py"
) else (
  python "scripts\mobile_tts_chunk_resume_static_test.py"
)
