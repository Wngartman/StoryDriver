@echo off
setlocal
D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe D:\StoryDriver\scripts\lmstudio_qwen_swap_test.py %*
exit /b %ERRORLEVEL%
