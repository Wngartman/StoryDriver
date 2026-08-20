@echo off
setlocal
cd /d "%~dp0.."
if not defined STORYDRIVER_PYTHONHOME (
  for /f "delims=" %%P in ('dir /b /s "%USERPROFILE%\.lmstudio\extensions\backends\vendor\python.exe" 2^>nul') do (
    if not defined STORYDRIVER_PYTHONHOME set "STORYDRIVER_PYTHONHOME=%%~dpP"
  )
)
if defined STORYDRIVER_PYTHONHOME (
  if "%STORYDRIVER_PYTHONHOME:~-1%"=="\" set "STORYDRIVER_PYTHONHOME=%STORYDRIVER_PYTHONHOME:~0,-1%"
  set "PYTHONHOME=%STORYDRIVER_PYTHONHOME%"
  set "PATH=%PYTHONHOME%;%PYTHONHOME%\DLLs;%PATH%"
)
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\long_state_shakedown.py"
) else (
  python "scripts\long_state_shakedown.py"
)
exit /b %ERRORLEVEL%
