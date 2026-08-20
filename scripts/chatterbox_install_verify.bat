@echo off
setlocal
set "ROOT=D:\StoryDriver\tts_engines\chatterbox"
set "TEMP=D:\StoryDriver\backend\data\temp"
set "TMP=%TEMP%"
set "PIP_CACHE_DIR=D:\StoryDriver\tts_engines\cache\pip"
set "HF_HOME=%ROOT%\models\huggingface"
set "HUGGINGFACE_HUB_CACHE=%HF_HOME%\hub"
set "HF_HUB_DISABLE_TELEMETRY=1"
if not exist "%ROOT%\venv\Scripts\python.exe" (
  D:\StoryDriver\backend\.venv\Scripts\python.exe D:\StoryDriver\scripts\chatterbox_gate_summary.py --check all || exit /b 1
  echo PASS: rejected Chatterbox environment was removed; retained benchmark evidence is complete.
  exit /b 0
)
"%ROOT%\venv\Scripts\python.exe" -m pip check || exit /b %ERRORLEVEL%
"%ROOT%\venv\Scripts\python.exe" -c "import chatterbox, torch, torchaudio; assert torch.version.cuda is None and not torch.cuda.is_available(); print('chatterbox', chatterbox.__version__, 'torch', torch.__version__, 'torchaudio', torchaudio.__version__)" || exit /b %ERRORLEVEL%
"%ROOT%\venv\Scripts\python.exe" D:\StoryDriver\scripts\chatterbox_benchmark.py download
exit /b %ERRORLEVEL%
