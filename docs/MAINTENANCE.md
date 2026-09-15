# Maintenance

Quit StoryDriver before a restore or moving its data directory.

```powershell
.\StoryDriverCLI.exe status
.\StoryDriverCLI.exe doctor
.\StoryDriverCLI.exe backup
.\StoryDriverCLI.exe export
.\StoryDriverCLI.exe restore --backup "D:\StoryDriverData\backups\your-backup.db"
.\StoryDriverCLI.exe cleanup --dry-run
.\StoryDriverCLI.exe cleanup --apply --older-than-days 7
```

Pass --data-root before the command to operate on an explicit data directory.
Backup uses SQLite's online backup API, including live WAL contents. Restore validates database identity/integrity and makes a pre-restore backup.
Cleanup only removes old files inside the selected data root's temp directory. It does not delete stories or narration.
Keep one known-good backup before upgrades. Uninstall preserves data.

Narration readiness appears in the app; App settings exposes service diagnostics and restart.
If a model fails, inspect its saved path and available RAM/VRAM before changing runtime settings.
Do not terminate unrelated services or silently alter LM Studio.
Images are disabled; old image experiments are not part of normal startup or support.
