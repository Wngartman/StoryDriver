# Installation And Upgrades

Run StoryDriver-Setup-x64.exe or extract the portable ZIP to a writable dedicated folder.
Choose separate application and data directories. Portable mode stores data in its own data directory.
Windows 10/11 x64 and Microsoft Edge WebView2 Evergreen Runtime are required.
The unsigned installer may trigger SmartScreen; verify release checksums.

Kokoro stock narration is bundled. Add a user-owned GGUF or configure a local text server under Settings > Writing.
No writing-model weights are downloaded automatically.

Quit StoryDriver, including its tray instance, before updating. The installer rejects active instances.
Existing storydriver.config.json is preserved on upgrade; /DATAROOT does not override an existing config.
Uninstall removes only shipped files and shortcuts. Data and unrelated files are retained.

Silent new install:
```powershell
.\StoryDriver-Setup-x64.exe /S /DATAROOT=D:\StoryDriverData /LAN=0 /D=D:\StoryDriverApp
```
/D must be last. For a new destination, /DESKTOP=1 adds a desktop shortcut.
Back up the data root before restoring or moving it. Quit the app before a CLI restore.
LAN and tray preferences live under the data root's config/desktop.json and take effect after restart.
