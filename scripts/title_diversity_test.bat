@echo off
setlocal
cd /d D:\StoryDriver
set TMP=D:\StoryDriver\backend\data\temp
set TEMP=D:\StoryDriver\backend\data\temp
backend\.venv\Scripts\python.exe scripts\tests\title_diversity_test.py --live 9
exit /b %errorlevel%
