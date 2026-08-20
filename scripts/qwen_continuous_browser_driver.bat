@echo off
setlocal
cd /d D:\StoryDriver
set "TMP=D:\StoryDriver\backend\data\temp"
set "TEMP=D:\StoryDriver\backend\data\temp"
set "STORYDRIVER_BROWSER_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "NODE_PATH=C:\Users\wngar\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\.pnpm\node_modules"
node.exe scripts\tests\qwen_continuous_browser_driver.cjs %*
exit /b %errorlevel%
