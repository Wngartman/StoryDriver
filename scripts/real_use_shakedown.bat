@echo off
setlocal
cd /d "%~dp0.."

if not exist "backend\.venv\Scripts\python.exe" (
  echo StoryDriver backend venv not found.
  exit /b 1
)

if "%STORYDRIVER_PYTHONHOME%"=="" (
  for /d %%D in ("%USERPROFILE%\.lmstudio\extensions\backends\vendor\_amphibian\cpython3.11-win-x86@*") do (
    if exist "%%~fD\Lib\html\entities.py" set "STORYDRIVER_PYTHONHOME=%%~fD"
  )
)

if not "%STORYDRIVER_PYTHONHOME%"=="" (
  set "PYTHONHOME=%STORYDRIVER_PYTHONHOME%"
)

"backend\.venv\Scripts\python.exe" "scripts\real_use_shakedown.py" %*
exit /b %ERRORLEVEL%
