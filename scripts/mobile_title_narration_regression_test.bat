@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\tests\story_isolation_title_qwen_regression.py mobile
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%~dp0\..\frontend"
call npm.cmd run build
if errorlevel 1 exit /b %ERRORLEVEL%
cd /d "%~dp0\.."
set "TMP=D:\StoryDriver\backend\data\temp"
set "TEMP=D:\StoryDriver\backend\data\temp"
set "STORYDRIVER_BROWSER_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "NODE_PATH=C:\Users\wngar\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\.pnpm\node_modules"
node.exe scripts\tests\mobile_title_narration_regression_test.cjs
exit /b %ERRORLEVEL%
