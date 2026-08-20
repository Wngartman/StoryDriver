@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\scripts\verify_gemma_fast_runtime.py" %*
endlocal
