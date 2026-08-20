@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
set "DESKTOP_DIR=C:\Users\wngar\AppData\Local\Programs\ComfyUI"
set "DESKTOP_EXE=%DESKTOP_DIR%\ComfyUI.exe"
set "USER_DIR=C:\Users\wngar\Documents\ComfyUI"

echo StoryDriver ComfyUI Desktop Diagnostics
echo.
echo Desktop install: %DESKTOP_DIR%
echo Desktop exe:     %DESKTOP_EXE%
echo User data:       %USER_DIR%
echo.

if exist "%DESKTOP_EXE%" (
  echo [OK] ComfyUI.exe exists.
) else (
  echo [Missing] ComfyUI.exe was not found.
)

if exist "%DESKTOP_DIR%\icudtl.dat" (
  echo [OK] icudtl.dat exists.
) else (
  echo [Missing] icudtl.dat is missing. Desktop will exit before writing normal app logs.
)

if exist "%DESKTOP_DIR%\d3dcompiler_47.dll" (
  echo [OK] d3dcompiler_47.dll exists.
) else (
  echo [Missing] d3dcompiler_47.dll is missing.
)

if exist "%USER_DIR%\.venv\Scripts\python.exe" (
  echo [OK] ComfyUI venv Python exists.
  "%USER_DIR%\.venv\Scripts\python.exe" --version
  "%USER_DIR%\.venv\Scripts\python.exe" -c "import argparse, ssl; print('argparse ok'); print('ssl ok')"
) else (
  echo [Missing] ComfyUI venv Python is missing.
)

echo.
echo Checking http://localhost:8188 ...
curl.exe --max-time 5 -fsS "http://localhost:8188/system_stats" >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  echo [OK] ComfyUI /system_stats is reachable.
) else (
  echo [Offline] ComfyUI /system_stats is not reachable.
)

echo.
echo Refreshing repair report...
if exist "%PYTHON%" (
  "%PYTHON%" "%~dp0comfyui_desktop_repair.py"
) else (
  py -3 "%~dp0comfyui_desktop_repair.py"
)

echo.
echo Running service-level ComfyUI diagnostics...
call "%~dp0diagnose_comfyui.bat"

echo.
echo Reports:
echo   %ROOT%\backend\data\logs\comfyui_desktop_repair_report.md
echo   %ROOT%\backend\data\logs\comfyui_diagnostics.txt
echo   %ROOT%\backend\data\logs\comfyui_desktop_launch_debug.txt
echo.
echo Next step:
echo   If ComfyUI is offline, run scripts\launch_comfyui_desktop_debug.bat and inspect the reports above.

exit /b 0
