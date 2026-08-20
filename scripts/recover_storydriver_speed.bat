@echo off
setlocal
set "ROOT=%~dp0.."
"%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\scripts\recover_storydriver_speed.py" %*
endlocal
