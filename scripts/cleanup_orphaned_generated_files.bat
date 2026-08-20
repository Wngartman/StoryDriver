@echo off
setlocal
cd /d "%~dp0\.."
backend\.venv\Scripts\python.exe scripts\cleanup_orphaned_generated_files.py %*
