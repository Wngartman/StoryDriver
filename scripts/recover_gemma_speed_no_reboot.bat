@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\scripts\recover_gemma_speed_no_reboot.py" %*
endlocal
