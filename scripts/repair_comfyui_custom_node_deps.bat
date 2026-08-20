@echo off
setlocal

set "COMFY_PYTHON=C:\Users\wngar\Documents\ComfyUI\.venv\Scripts\python.exe"
set "SCRIPT=%~dp0repair_comfyui_custom_node_deps.py"

if not exist "%COMFY_PYTHON%" (
    echo ComfyUI venv Python was not found:
    echo   %COMFY_PYTHON%
    echo No packages were installed.
    exit /b 1
)

if /i "%~1"=="install" (
    "%COMFY_PYTHON%" "%SCRIPT%" --install
) else (
    "%COMFY_PYTHON%" "%SCRIPT%"
)

exit /b %errorlevel%
