@echo off
setlocal
cd /d D:\StoryDriver
set TMP=D:\StoryDriver\backend\data\temp
set TEMP=D:\StoryDriver\backend\data\temp
backend\.venv\Scripts\python.exe scripts\tests\long_story_continuity_gauntlet.py --real-scenes 20
exit /b %errorlevel%
