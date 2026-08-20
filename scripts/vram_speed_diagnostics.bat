@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\scripts\vram_speed_diagnostics.py" %*
endlocal
