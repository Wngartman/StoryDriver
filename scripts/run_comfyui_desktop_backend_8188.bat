@echo off
setlocal
set "ROOT=D:\StoryDriver"
set "LOG=%ROOT%\backend\data\logs\comfyui_desktop_launch_debug.txt"
set "WORKDIR=C:\Users\wngar\Documents\ComfyUI"
set "PYTHON=C:\Users\wngar\Documents\ComfyUI\.venv\Scripts\python.exe"
set "MAIN=C:\Users\wngar\AppData\Local\Programs\ComfyUI\resources\ComfyUI\main.py"
set "FRONTEND=C:\Users\wngar\AppData\Local\Programs\ComfyUI\resources\ComfyUI\web_custom_versions\desktop_app"
set "EXTRA=C:\Users\wngar\AppData\Roaming\ComfyUI\extra_models_config.yaml"

cd /d "%WORKDIR%"
set PYTHONHOME=
set PYTHONPATH=
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
chcp 65001 >nul

echo ==== ComfyUI Desktop debug launch %DATE% %TIME% ====>>"%LOG%"
echo Working directory: %WORKDIR%>>"%LOG%"
echo Python: %PYTHON%>>"%LOG%"
echo Port: 8188>>"%LOG%"

"%PYTHON%" -c "import sqlalchemy, torch" >nul 2>&1
if errorlevel 1 (
  echo ComfyUI backend preflight failed.>>"%LOG%"
  echo The configured venv is missing required backend packages such as SQLAlchemy and/or torch.>>"%LOG%"
  echo ComfyUI backend preflight failed.
  echo The configured venv is missing required backend packages such as SQLAlchemy and/or torch.
  echo Open ComfyUI Desktop and let its repair/update finish, or configure a proven backend command.
  echo StoryDriver will keep working; ComfyUI image generation remains offline until this runtime is repaired.
  exit /b 1
)

"%PYTHON%" "%MAIN%" ^
  --user-directory "%WORKDIR%\user" ^
  --input-directory "%WORKDIR%\input" ^
  --output-directory "%WORKDIR%\output" ^
  --front-end-root "%FRONTEND%" ^
  --base-directory "%WORKDIR%" ^
  --database-url sqlite:///C:/Users/wngar/Documents/ComfyUI/user/comfyui.db ^
  --extra-model-paths-config "%EXTRA%" ^
  --log-stdout ^
  --listen 127.0.0.1 ^
  --port 8188 ^
  --enable-manager ^
  --preview-size 256 ^
  --auto ^
  --reserve-vram 3 ^
  >>"%LOG%" 2>>&1

echo.
echo ComfyUI exited with code %ERRORLEVEL%.
echo See %LOG%
