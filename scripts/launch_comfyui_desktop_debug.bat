@echo off
setlocal EnableDelayedExpansion
set "ROOT=D:\StoryDriver"
set "LOG=%ROOT%\backend\data\logs\comfyui_desktop_launch_debug.txt"
set "APPDIR=C:\Users\wngar\AppData\Local\Programs\ComfyUI"
set "DESKTOP_EXE=%APPDIR%\ComfyUI.exe"

if not exist "%DESKTOP_EXE%" (
  echo No safe ComfyUI Desktop launch command discovered: missing "%DESKTOP_EXE%"
  echo See %ROOT%\backend\data\logs\comfyui_desktop_repair_report.md
  exit /b 1
)

if not exist "%APPDIR%\icudtl.dat" (
  echo ComfyUI Desktop is missing icudtl.dat and will exit immediately.
  echo See %ROOT%\backend\data\logs\comfyui_desktop_repair_report.md
  exit /b 1
)

if not exist "%APPDIR%\d3dcompiler_47.dll" (
  echo ComfyUI Desktop is missing d3dcompiler_47.dll.
  echo See %ROOT%\backend\data\logs\comfyui_desktop_repair_report.md
  exit /b 1
)

curl.exe --max-time 2 -fsS "http://localhost:8188/system_stats" >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  echo ComfyUI is already reachable at http://localhost:8188
  exit /b 0
)

if not exist "%ROOT%\backend\data\logs" mkdir "%ROOT%\backend\data\logs"
echo ==== ComfyUI Desktop app launch %DATE% %TIME% ====>>"%LOG%"
echo Desktop executable: %DESKTOP_EXE%>>"%LOG%"
echo Launching ComfyUI Desktop on http://localhost:8188
echo Log: %LOG%
echo.

start "ComfyUI Desktop" /D "%APPDIR%" "%DESKTOP_EXE%" --enable-logging --v=1

echo Waiting for ComfyUI to respond...
for /l %%I in (1,1,60) do (
  curl.exe --max-time 2 -fsS "http://localhost:8188/system_stats" >nul 2>nul
  if "!ERRORLEVEL!"=="0" goto :ready
  timeout /t 1 >nul
)

echo ComfyUI did not become reachable within 60 seconds.
echo Check %LOG%, %APPDIR%\debug.log, and %APPDATA%\ComfyUI\logs\main.log
exit /b 1

:ready
echo ComfyUI is reachable at http://localhost:8188
exit /b 0
