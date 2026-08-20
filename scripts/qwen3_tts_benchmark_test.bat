@echo off
setlocal
set "ROOT=D:\StoryDriver"
set "QWEN=%ROOT%\tts_engines\qwen3_tts"
set "TMP=%ROOT%\backend\data\temp"
set "TEMP=%TMP%"
set "PIP_CACHE_DIR=%ROOT%\backend\data\temp\pip-cache"
set "HF_HOME=%ROOT%\tts_engines\cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%ROOT%\tts_engines\cache\huggingface\hub"
set "TRANSFORMERS_CACHE=%ROOT%\tts_engines\cache\transformers"
set "TORCH_HOME=%ROOT%\tts_engines\cache\torch"
set "XDG_CACHE_HOME=%ROOT%\tts_engines\cache"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "HF_HUB_DISABLE_TELEMETRY=1"
"%QWEN%\venv\Scripts\python.exe" "%QWEN%\benchmark.py" %*
exit /b %ERRORLEVEL%
