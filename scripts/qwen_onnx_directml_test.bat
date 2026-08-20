@echo off
setlocal
set "ONNX_PYTHON=D:\StoryDriver\tts_engines\qwen3_tts_experiments\onnx_directml\venv\Scripts\python.exe"
if not exist "%ONNX_PYTHON%" (
  D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe D:\StoryDriver\scripts\qwen_backend_feasibility.py onnx
  exit /b %ERRORLEVEL%
)
"%ONNX_PYTHON%" D:\StoryDriver\scripts\qwen_onnx_export_test.py
exit /b %ERRORLEVEL%
