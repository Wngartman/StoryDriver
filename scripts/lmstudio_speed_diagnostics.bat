@echo off
setlocal
cd /d "%~dp0.."
if not defined STORYDRIVER_PYTHONHOME (
  for /d %%D in ("%USERPROFILE%\.lmstudio\extensions\backends\vendor\_amphibian\cpython3.11-win-x86@*") do (
    if exist "%%D\python.exe" set "STORYDRIVER_PYTHONHOME=%%D"
  )
)
if defined STORYDRIVER_PYTHONHOME set "PYTHONHOME=%STORYDRIVER_PYTHONHOME%"
backend\.venv\Scripts\python.exe scripts\lmstudio_speed_diagnostics.py %*
exit /b %ERRORLEVEL%
