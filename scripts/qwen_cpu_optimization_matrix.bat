@echo off
call D:\StoryDriver\scripts\qwen_continuous_benchmark.bat matrix --trials 2 --target-seconds 10 --max-new-tokens 384
exit /b %ERRORLEVEL%
