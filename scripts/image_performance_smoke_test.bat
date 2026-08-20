@echo off
setlocal
cd /d "%~dp0.."
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" "scripts\image_performance_smoke_test.py" %*
) else (
  python "scripts\image_performance_smoke_test.py" %*
)
endlocal
