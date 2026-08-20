@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHONHOME="
set "PYTHONPATH=%ROOT%\backend;%ROOT%\scripts"
"%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\scripts\lmstudio_current_speed_test.py" %*
