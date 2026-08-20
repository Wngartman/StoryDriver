@echo off
setlocal
set "TMP=D:\StoryDriver\backend\data\temp"
set "TEMP=D:\StoryDriver\backend\data\temp"
set "PIP_CACHE_DIR=D:\StoryDriver\backend\data\temp\pip-cache"
set "HF_HOME=D:\StoryDriver\tts_engines\cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=D:\StoryDriver\tts_engines\cache\huggingface\hub"
set "TRANSFORMERS_CACHE=D:\StoryDriver\tts_engines\cache\transformers"
set "TORCH_HOME=D:\StoryDriver\tts_engines\cache\torch"
set "XDG_CACHE_HOME=D:\StoryDriver\tts_engines\cache"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
cd /d D:\StoryDriver
D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe D:\StoryDriver\scripts\qwen_interrupt_for_writing_test.py
exit /b %ERRORLEVEL%
