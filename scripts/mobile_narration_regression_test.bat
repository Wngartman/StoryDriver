@echo off
setlocal
cd /d D:\StoryDriver
backend\.venv\Scripts\python.exe scripts\mobile_narration_ui_static_test.py || exit /b %ERRORLEVEL%
call scripts\mobile_tts_chunk_resume_static_test.bat || exit /b %ERRORLEVEL%
call scripts\tts_buffered_seek_test.bat || exit /b %ERRORLEVEL%
call scripts\qwen_premium_plan_test.bat || exit /b %ERRORLEVEL%
call scripts\tts_provider_comparison_test.bat || exit /b %ERRORLEVEL%
exit /b 0
