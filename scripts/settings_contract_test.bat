@echo off
setlocal
cd /d D:\StoryDriver
set TMP=D:\StoryDriver\backend\data\temp
set TEMP=D:\StoryDriver\backend\data\temp
backend\.venv\Scripts\python.exe scripts\tests\settings_contract_test.py
exit /b %errorlevel%
