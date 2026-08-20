@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%\backend;%ROOT%\scripts"
set "PYTHONHOME=C:\Users\wngar\.lmstudio\extensions\backends\vendor\_amphibian\cpython3.11-win-x86@6"
"%ROOT%\backend\.venv\Scripts\python.exe" "%ROOT%\scripts\manual_comfyui_closed_speed_test.py" %*
