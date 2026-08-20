@echo off
setlocal
set "TMP=D:\StoryDriver\backend\data\temp"
set "TEMP=%TMP%"
set "PIP_CACHE_DIR=%TMP%\pip-cache"
D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe D:\StoryDriver\scripts\qwen_backend_feasibility.py directml
exit /b %ERRORLEVEL%
