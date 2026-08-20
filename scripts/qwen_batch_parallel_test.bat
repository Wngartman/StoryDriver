@echo off
setlocal
set "THREADS=%~2"
set "INTEROP=%~3"
if not defined THREADS set "THREADS=4"
if not defined INTEROP set "INTEROP=4"
if /I "%~1"=="parallel" (
  call D:\StoryDriver\scripts\qwen_continuous_benchmark.bat parallel --threads %THREADS% --interop-threads %INTEROP% --target-seconds 10
  exit /b %ERRORLEVEL%
)
call D:\StoryDriver\scripts\qwen_continuous_benchmark.bat batches --trials 2 --threads %THREADS% --interop-threads %INTEROP% --target-seconds 10
exit /b %ERRORLEVEL%
