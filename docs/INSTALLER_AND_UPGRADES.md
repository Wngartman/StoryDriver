# Installer And Upgrades

## Installer

`StoryDriver-Setup-x64.exe` is built with NSIS 3.12 for Windows x64. It provides:

- selectable application directory;
- selectable mutable-data directory;
- Start menu shortcut;
- optional desktop shortcut;
- optional private-LAN mode;
- upgrade over an existing installation;
- default data-preserving uninstall;
- explicit remove-data uninstall.

The installer writes a local `storydriver.config.json` in the install directory. The file points to the selected data root and records LAN/close behavior. The real machine configuration is ignored by Git; the release contains only a clean sample/default.

## Existing Data Migration

On this machine, `D:\StoryDriverApp` was installed against the existing `D:\StoryDriver\backend\data` root after a full backup. SQLite migrations were additive. Semantic comparison against the master backup confirmed that stories, scenes, versions, prompts, task settings, presets, TTS settings, custom-voice metadata, appearance, and LAN settings were preserved. The only database differences were expected provider fields and timestamps.

The installer never copies the development database into a clean package. Portable mode creates its own database under its extracted data folder.

## Verified Upgrade And Uninstall

A disposable clean install was upgraded in place after a sentinel database/file was added. Both were byte-identical afterward. Default silent uninstall removed the application, registry entry, and Start menu shortcut while preserving the separate data root. A second disposable install using explicit `/REMOVEDATA=1` removed only its clearly named disposable data directory.

The final installed app is at:

`D:\StoryDriverApp\StoryDriver.exe`

The preserved data root remains:

`D:\StoryDriver\backend\data`

## Firewall And LAN

When private-LAN mode binds the backend to `0.0.0.0:8001`, Windows Firewall may display a one-time consent dialog for `StoryDriverBackend.exe`. This is an operating-system security decision and is not bypassed. Approve only Private networks when phone access is desired. Cancel and disable LAN mode when it is not.

StoryDriver does not create port forwarding, public firewall rules, or router changes.

## Recovery

Before an upgrade:

```text
StoryDriverCLI.exe backup
StoryDriverCLI.exe export
```

To diagnose the installed data root:

```text
StoryDriverCLI.exe status
StoryDriverCLI.exe doctor
```

To restore a verified backup while StoryDriver is closed:

```text
StoryDriverCLI.exe restore --backup D:\path\to\app.db
```

The release checkpoint is the annotated Git tag `pre_native_desktop_packaging_revamp_20260819`. The master data backup and exact hashes are recorded in `backend/data/logs/NATIVE_DESKTOP_PACKAGING_ROLLBACK.md` in the private workspace.

## Signing

This release is unsigned. A trusted Authenticode code-signing certificate and protected signing process are required to reduce SmartScreen warnings. No signature is claimed in metadata or documentation.
