@echo off
setlocal
cd /d D:\StoryDriver
set TMP=D:\StoryDriver\backend\data\temp
set TEMP=D:\StoryDriver\backend\data\temp
backend\.venv\Scripts\python.exe scripts\tests\relationship_location_accuracy_test.py
exit /b %errorlevel%
