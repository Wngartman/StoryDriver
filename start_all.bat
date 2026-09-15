@echo off
if exist "%~dp0StoryDriver.exe" (
  start "" "%~dp0StoryDriver.exe"
  exit /b
)
if exist "D:\StoryDriverApp\StoryDriver.exe" (
  start "" "D:\StoryDriverApp\StoryDriver.exe"
  exit /b
)
start "" "https://github.com/Wngartman/StoryDriver/releases/latest"
