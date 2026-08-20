@echo off
call D:\StoryDriver\scripts\qwen_continuous_benchmark.bat stream-check --target-seconds 10 --max-new-tokens 384
exit /b %ERRORLEVEL%
