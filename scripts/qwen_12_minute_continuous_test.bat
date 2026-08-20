@echo off
setlocal
set "PYTHON=D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe"
"%PYTHON%" D:\StoryDriver\scripts\qwen_continuous_playback_test.py --minutes 12 --port 8894 --backend cpu --dtype bfloat16 --attention sdpa --batch-size 4 --threads 4 --interop-threads 4 --no-manage-gemma
exit /b %ERRORLEVEL%
