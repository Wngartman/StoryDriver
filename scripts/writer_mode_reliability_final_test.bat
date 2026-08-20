@echo off
setlocal
cd /d "%~dp0.."
backend\.venv\Scripts\python.exe scripts\writer_mode_reliability_final_test.py %*
