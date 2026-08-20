@echo off
setlocal EnableExtensions
set "ROOT=D:\StoryDriver"
set "EXPERIMENT=%ROOT%\tts_engines\qwen3_tts_experiments\hip_windows"
set "VENV=%EXPERIMENT%\venv"
set "SOURCE_PYTHON=%ROOT%\tts_engines\qwen3_tts\venv\Scripts\python.exe"
set "TMP=%ROOT%\backend\data\temp"
set "TEMP=%TMP%"
set "PIP_CACHE_DIR=%TMP%\pip-cache"
set "HF_HOME=%ROOT%\tts_engines\cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%ROOT%\tts_engines\cache\huggingface\hub"
set "TRANSFORMERS_CACHE=%ROOT%\tts_engines\cache\transformers"
set "TORCH_HOME=%ROOT%\tts_engines\cache\torch"
set "XDG_CACHE_HOME=%ROOT%\tts_engines\cache"
if not exist "%EXPERIMENT%" mkdir "%EXPERIMENT%"
if not exist "%VENV%\Scripts\python.exe" "%SOURCE_PYTHON%" -m venv --copies "%VENV%"
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV%\Scripts\python.exe" -c "from pathlib import Path; Path(r'%VENV%\Lib\site-packages\qwen_shared_dependencies.pth').write_text(r'%ROOT%\tts_engines\qwen3_tts\venv\Lib\site-packages' + '\n', encoding='ascii')"
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV%\Scripts\python.exe" -m pip install --no-cache-dir ^
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl ^
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl ^
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl ^
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm-7.2.1.tar.gz
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV%\Scripts\python.exe" -m pip install --no-cache-dir ^
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torch-2.9.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl ^
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/torchaudio-2.9.1%%2Brocm7.2.1-cp312-cp312-win_amd64.whl
if errorlevel 1 exit /b %ERRORLEVEL%
"%VENV%\Scripts\python.exe" -c "import json, torch; print(json.dumps({'torch': torch.__version__, 'hip': torch.version.hip, 'available': torch.cuda.is_available(), 'device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}, indent=2))"
exit /b %ERRORLEVEL%
