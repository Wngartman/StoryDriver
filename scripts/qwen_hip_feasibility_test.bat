@echo off
setlocal
set "HIP_PYTHON=D:\StoryDriver\tts_engines\qwen3_tts_experiments\hip_windows\venv\Scripts\python.exe"
if not exist "%HIP_PYTHON%" (
  D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe D:\StoryDriver\scripts\qwen_backend_feasibility.py hip-info
  exit /b %ERRORLEVEL%
)
"%HIP_PYTHON%" D:\StoryDriver\scripts\qwen_hip_benchmark.py
exit /b %ERRORLEVEL%
