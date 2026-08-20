@echo off
setlocal EnableExtensions

set "STORYDRIVER_ROOT=%~dp0.."
set "ENV_FILE=%STORYDRIVER_ROOT%\.env"
set "KOKORO_WORKING_DIR="
set "KOKORO_PYTHON_EXE="
set "KOKORO_BASE_URL=http://localhost:8880"
set "KOKORO_PORT=8880"

if exist "%ENV_FILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /i "%%A"=="KOKORO_WORKING_DIR" set "KOKORO_WORKING_DIR=%%B"
        if /i "%%A"=="KOKORO_PYTHON_EXE" set "KOKORO_PYTHON_EXE=%%B"
        if /i "%%A"=="KOKORO_BASE_URL" set "KOKORO_BASE_URL=%%B"
    )
)

if "%KOKORO_WORKING_DIR%"=="" set "KOKORO_WORKING_DIR=D:\Kokoro-FastAPI"
if "%KOKORO_PYTHON_EXE%"=="" set "KOKORO_PYTHON_EXE=%KOKORO_WORKING_DIR%\.venv\Scripts\python.exe"

for /f "tokens=3 delims=:" %%P in ("%KOKORO_BASE_URL%") do (
    for /f "tokens=1 delims=/" %%Q in ("%%P") do set "KOKORO_PORT=%%Q"
)
if "%KOKORO_PORT%"=="" set "KOKORO_PORT=8880"

set "LOG_DIR=%STORYDRIVER_ROOT%\backend\data\logs"
set "LOG_FILE=%LOG_DIR%\kokoro_launch.log"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul

echo StoryDriver Kokoro launcher> "%LOG_FILE%"
echo Started: %DATE% %TIME%>> "%LOG_FILE%"
echo Working dir: %KOKORO_WORKING_DIR%>> "%LOG_FILE%"
echo Python: %KOKORO_PYTHON_EXE%>> "%LOG_FILE%"
echo Port: %KOKORO_PORT%>> "%LOG_FILE%"

if not exist "%KOKORO_WORKING_DIR%" (
    echo Kokoro working directory was not found: "%KOKORO_WORKING_DIR%"
    echo Kokoro working directory was not found: "%KOKORO_WORKING_DIR%">> "%LOG_FILE%"
    pause
    exit /b 1
)

if not exist "%KOKORO_PYTHON_EXE%" (
    echo Kokoro Python was not found: "%KOKORO_PYTHON_EXE%"
    echo Kokoro Python was not found: "%KOKORO_PYTHON_EXE%">> "%LOG_FILE%"
    pause
    exit /b 1
)

cd /d "%KOKORO_WORKING_DIR%"

if exist "C:\Program Files (x86)\eSpeak NG\libespeak-ng.dll" (
    set "PHONEMIZER_ESPEAK_LIBRARY=C:\Program Files (x86)\eSpeak NG\libespeak-ng.dll"
) else if exist "C:\Program Files\eSpeak NG\libespeak-ng.dll" (
    set "PHONEMIZER_ESPEAK_LIBRARY=C:\Program Files\eSpeak NG\libespeak-ng.dll"
) else (
    set "PHONEMIZER_ESPEAK_LIBRARY="
)
set "PYTHONHOME="
set "PYTHONUTF8=1"
set "PROJECT_ROOT=%CD%"
set "USE_GPU=false"
set "USE_ONNX=false"
set "PYTHONPATH=%PROJECT_ROOT%;%PROJECT_ROOT%\api"
set "MODEL_DIR=src/models"
set "VOICES_DIR=src/voices/v1_0"
set "WEB_PLAYER_PATH=%PROJECT_ROOT%\web"
set "API_LOG_LEVEL=INFO"
set "DEVICE=cpu"

echo Probing Python runtime...>> "%LOG_FILE%"
"%KOKORO_PYTHON_EXE%" -c "import difflib, ssl, uvicorn; import api.src.main; print('kokoro import ok')" >> "%LOG_FILE%" 2>>&1
if errorlevel 1 (
    echo Kokoro Python runtime probe failed. See:
    echo %LOG_FILE%
    echo Kokoro Python runtime probe failed.>> "%LOG_FILE%"
    pause
    exit /b 1
)

echo Starting Kokoro-FastAPI on 127.0.0.1:%KOKORO_PORT% ...
echo Starting uvicorn...>> "%LOG_FILE%"
"%KOKORO_PYTHON_EXE%" -m uvicorn api.src.main:app --host 127.0.0.1 --port %KOKORO_PORT% >> "%LOG_FILE%" 2>>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo Kokoro exited with code %EXIT_CODE%. See:
echo %LOG_FILE%
echo Kokoro exited with code %EXIT_CODE%.>> "%LOG_FILE%"
pause
exit /b %EXIT_CODE%
