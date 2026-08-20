@echo off
setlocal EnableExtensions
set "ROOT=D:\StoryDriver"
set "EXPERIMENT=%ROOT%\tts_engines\qwen3_tts_experiments\torchao"
set "VENV=%EXPERIMENT%\venv"
set "SOURCE_PYTHON=%ROOT%\tts_engines\qwen3_tts\venv\Scripts\python.exe"
set "TMP=%ROOT%\backend\data\temp"
set "TEMP=%TMP%"
set "PIP_CACHE_DIR=%TMP%\pip-cache"
if not exist "%EXPERIMENT%" mkdir "%EXPERIMENT%"
if not exist "%VENV%\Scripts\python.exe" "%SOURCE_PYTHON%" -m venv --copies "%VENV%"
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV%\Scripts\python.exe" -c "from pathlib import Path; Path(r'%VENV%\Lib\site-packages\qwen_shared_dependencies.pth').write_text(r'%ROOT%\tts_engines\qwen3_tts\venv\Lib\site-packages' + '\n', encoding='ascii')"
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV%\Scripts\python.exe" -m pip install --no-cache-dir --no-deps torchao
exit /b %ERRORLEVEL%
