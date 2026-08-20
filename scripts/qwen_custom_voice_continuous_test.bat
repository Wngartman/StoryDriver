@echo off
setlocal
cd /d "%~dp0\.."
tts_engines\qwen3_tts\venv\Scripts\python.exe scripts\qwen_continuous_playback_test.py --minutes 12 --port 8895 --backend cpu --dtype bfloat16 --attention sdpa --batch-size 4 --threads 4 --interop-threads 4 --no-manage-gemma --custom-voice --automatic-direction
exit /b %ERRORLEVEL%
