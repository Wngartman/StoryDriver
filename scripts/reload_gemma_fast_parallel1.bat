@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\scripts\reload_gemma_fast_parallel1.py" %*
endlocal
