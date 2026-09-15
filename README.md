# StoryDriver

![StoryDriver](assets/desktop/storydriver-loading.png)

A local directed-fiction studio for Windows. Direct a scene, develop a story, keep its people and objects consistent, and listen to it with local narration.

## Install

Download **StoryDriver-Setup-x64.exe** from [Releases](https://github.com/Wngartman/StoryDriver/releases/latest), or extract the portable ZIP and open **StoryDriver.exe**.

1. Choose application and data folders during installation.
2. Open **Settings > Writing**. Add your own GGUF model, or select LM Studio and its running local model.
3. Choose **Load** and **Test**, then create a story and enter a director note.
4. Kokoro narration is included and starts automatically.

Windows 10/11 x64 and Microsoft Edge WebView2 Evergreen Runtime are required. If WebView2 is missing, the app links to Microsoft's installer. No Python, Node, CUDA, account, or cloud TTS is needed for the installed application. Writing-model weights are not included. Use a model that fits your GPU/RAM; smaller models reduce resource requirements but may change writing quality.

The installer is unsigned; Windows SmartScreen may warn. Check the release's SHA256SUMS.txt. Keep the data folder on a local writable disk, separate from the install folder.

## Writing

- Continue creates a new scene.
- Rewrite, Revise and Regenerate create versions of the selected scene.
- Every generation uses Story Foundation, Scene Contract, blocking, character agency, Prose v3, continuity review and at most one major-failure repair.
- Your editable system prompt and task notes are the creative authority.
- Story State supplies factual continuity: people, relationships, locations, objects, injuries, clothing, knowledge and unresolved threads.
- Stored system prompts have no artificial character cap. The model's actual context window still limits what it can process.
- Images are disabled.

## Narration And Settings

One settings drawer contains **Writing**, **Narration**, **Appearance**, and **App**. Advanced writing controls remain available without separate quality modes.

The default narrator is bundled CPU **Kokoro / Aoede**. Narration uses natural chunks, prebuffering, local caching, pronunciation aliases, saved position, pause/resume and browser recovery. A browser voice is the final fallback. Legacy optional local-provider adapters remain for compatibility, but no custom voice recordings or experimental model environments ship.

## Privacy And LAN

No telemetry, accounts, cloud inference, remote fonts or automatic writing-model downloads. Normal inference endpoints must be local/private LAN. Your stories, prompts, settings and audio stay in the chosen data folder.

Phone access is opt-in under **Settings > App** and requires an app restart. Open the displayed private-LAN address on the same trusted network; allow only Private networks in Windows Firewall. LAN mode has **no authentication**. Never expose or port-forward it to the internet. Physical phone lock-screen/background playback varies by browser and remains a manual acceptance item.

## Maintenance

Quit StoryDriver before restoring:

```powershell
.\StoryDriverCLI.exe backup
.\StoryDriverCLI.exe export
.\StoryDriverCLI.exe restore --backup "D:\StoryDriverData\backups\your-backup.db"
.\StoryDriverCLI.exe cleanup --dry-run
```

Backups and exports are local. Uninstall removes shipped application files, not your data or unrelated files.

## Development

See [Building](docs/BUILDING.md), [Architecture](docs/CURRENT_ARCHITECTURE.md), [Recommended Settings](docs/CURRENT_RECOMMENDED_SETTINGS.md), and [Human Test Plan](docs/HUMAN_TEST_PLAN.md).

Source layout: `apps/` native shell and frozen entrypoints; `backend/` API/state; `frontend/` React UI; `scripts/` development and validation; `installer/` packaging; `runtimes/` pinned manifests; `docs/` guidance. Build outputs and all private data are ignored by Git.

## Licenses

Core application: MIT. The separate narration worker and its phonemizer/eSpeak chain: GPL-3.0-or-later. Kokoro model and stock voices: Apache-2.0. Other bundled components retain their licenses. See [Third-Party Notices](THIRD_PARTY_NOTICES.md). Corresponding narration sources accompany each binary release.

This is a working 1.0 release, not a guarantee of flawless model output or universal hardware compatibility. Long scenes and repair passes may exceed two minutes.
