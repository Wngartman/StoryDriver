@echo off
setlocal
cd /d "%~dp0.."
backend\.venv\Scripts\python.exe scripts\writer_mode_error_handling_probe.py %*
