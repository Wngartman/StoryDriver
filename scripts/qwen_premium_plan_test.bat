@echo off
setlocal
cd /d D:\StoryDriver
where node.exe >nul 2>nul
if errorlevel 1 (
  echo FAIL: node.exe is not available.
  exit /b 1
)
node.exe D:\StoryDriver\scripts\tests\qwen_premium_plan_test.mjs
exit /b %ERRORLEVEL%
