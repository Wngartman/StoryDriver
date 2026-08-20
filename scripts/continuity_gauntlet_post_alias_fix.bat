@echo off
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\tests\long_story_continuity_gauntlet.py --real-scenes 20
exit /b %ERRORLEVEL%
