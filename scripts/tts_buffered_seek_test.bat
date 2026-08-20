@echo off
setlocal
cd /d D:\StoryDriver
"C:\Program Files\nodejs\node.exe" scripts\tests\tts_buffered_seek_test.mjs
exit /b %ERRORLEVEL%
