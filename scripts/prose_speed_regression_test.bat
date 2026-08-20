@echo off
setlocal

if not defined STORYDRIVER_PYTHONHOME (
  for /d %%D in ("%USERPROFILE%\.lmstudio\extensions\backends\vendor\_amphibian\cpython3.11-win-x86@*") do (
    if exist "%%D\python.exe" set "STORYDRIVER_PYTHONHOME=%%D"
  )
)
if defined STORYDRIVER_PYTHONHOME set "PYTHONHOME=%STORYDRIVER_PYTHONHOME%"

set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\backend\.venv\Scripts\python.exe"
if exist "%PYTHON%" (
    "%PYTHON%" "%~dp0prose_speed_regression_test.py" %*
) else (
    py -3 "%~dp0prose_speed_regression_test.py" %*
)
exit /b %errorlevel%
