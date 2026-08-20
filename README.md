# StoryDriver

![StoryDriver loading artwork](assets/desktop/storydriver-loading.png)

StoryDriver is a private local directed-fiction studio for Windows. You give it a director note; it plans and writes a narrated prose scene, saves versions, maintains story continuity, and can narrate the accepted text. It is a writing application, not a chat client.

## What It Does

- Continue creates a new scene.
- Regenerate, Rewrite, and Revise create versions of the selected scene.
- The normal writing path is always Story Foundation and relevant memory -> Scene Contract, blocking, agency, and viewpoint plan -> Prose v3 -> compact review -> at most one targeted repair -> save -> background state extraction.
- Story State tracks relationships, locations, blocking, objects, clothing, injuries, knowledge, memories, secrets, goals, and unresolved threads.
- The story remains the visual focus in a responsive React interface with search, themes, local backgrounds, reading controls, and a compact narration player.
- Kokoro provides fast progressive narration. Qwen3-TTS 0.6B provides the selected high-quality local custom-voice path and falls back to Kokoro when unavailable.
- Image generation remains paused and is not exposed in the active product.

## Native Windows App

The installed product launches from `StoryDriver.exe`. It uses a native WPF/WebView2 shell with a minimalist startup screen, one-instance activation, native window controls and file dialogs, optional tray operation, and hidden child processes. It does not open an external browser or console.

The window becomes available after the packaged backend is ready. Kokoro continues starting in the background; the app shows `Narration starting` until the health check reports ready. No Node.js, Vite server, or system Python is required for the installed core application.

```text
StoryDriver.exe (WPF + WebView2)
  -> hidden StoryDriverBackend.exe on :8001
     -> production React assets and FastAPI API
     -> SQLite data root
     -> built-in llama.cpp Vulkan server, on demand
     -> existing local OpenAI-compatible/LM Studio endpoint, when selected
     -> Kokoro supervisor on :8880
     -> Qwen3-TTS worker on :8891, on demand
```

The shell was implemented in WPF because this machine did not have the Rust/MSVC toolchain required for a bounded Tauri proof, and installing system toolchains would have violated the no-system-change constraint. Electron would have added another bundled browser runtime. WebView2 reuses the Windows runtime and preserves the existing web UI.

## Local Model Providers

StoryDriver supports three text-provider paths:

1. **Built-in llama.cpp**: select an existing GGUF file or scan a local model folder. The bundled Windows x64 runtime uses Vulkan on this AMD RX 7900 XTX, with CPU fallback.
2. **Local OpenAI-compatible**: connect to loopback or an explicitly allowed private-LAN endpoint, discover models, select a model ID, stream, and test the route.
3. **LM Studio compatibility**: preserves the existing API route without making LM Studio an architectural dependency or modifying its settings.

The Model Library supports Add GGUF, Add Folder, Scan, Refresh, Load, Unload, Test, and remove-entry-without-deleting-model. Provider-specific controls appear only when supported. Per-task provider/model routing, system prompts, task notes, context budgets, sampling settings, writing lengths, and presets remain editable.

The previously referenced `gemma4-26b-a4b-uncensored-hauhaucs-balanced` file was not present in the current LM Studio inventory or on disk during migration. Its saved preset references were preserved, but StoryDriver did not fabricate or silently substitute that missing file. The actual pre-migration selected model, `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`, remains selected through LM Studio compatibility.

See [MODEL_PROVIDER_GUIDE.md](docs/MODEL_PROVIDER_GUIDE.md).

## Installation And Portable Use

Release outputs are written to `release`:

- `StoryDriver-Setup-x64.exe`
- `StoryDriver-Portable-x64.zip`
- `SHA256SUMS.txt`
- `RELEASE_NOTES.md`

The installer lets you choose the application and mutable-data locations, can preserve an existing D:-based data root, creates Start menu shortcuts, optionally creates a desktop shortcut, and keeps data by default during uninstall. Explicit remove-data uninstall is separate and scoped.

Portable mode keeps data beside the extracted application. The package contains no story database, model weights, voice references, generated audio, private logs, Node runtime, or Python installation.

The build is unsigned. Windows SmartScreen may warn until releases are signed with a trusted Windows code-signing certificate.

See [INSTALLER_AND_UPGRADES.md](docs/INSTALLER_AND_UPGRADES.md) and [DESKTOP_PACKAGING.md](docs/DESKTOP_PACKAGING.md).

## First Run

1. Start `StoryDriver.exe`.
2. Choose the data folder during installation, or use the portable folder.
3. Open Settings -> Models and select built-in llama.cpp, a local OpenAI-compatible endpoint, or LM Studio compatibility.
4. Add/select a model and run Test.
5. Open Settings -> Narration and configure a local voice, or leave Kokoro as fallback.
6. Confirm Settings -> LAN & Privacy before enabling private-network access.
7. Create a story.

No account or internet connection is required. A clean machine still needs a user-supplied local GGUF or local model server and, for narration, locally installed TTS assets. StoryDriver never downloads model weights at runtime.

## Narration

The current saved selection is Qwen3-TTS 0.6B Base with the authorized local `Arabella` profile. Kokoro `Aoede` is the fast automatic fallback. `Premium Female Narrator` is the built-in Qwen/Serena profile; StoryDriver does not bundle an unauthorized real person's recording.

Narration uses natural sentence/paragraph chunks, prebuffering, local chunk caching, pronunciation aliases, provider-native settings where safe, gap-aware playback, rewind/forward, buffered seeking, pause/resume, page-refresh cursor recovery, and mobile user-gesture recovery. Expensive Qwen starts only when requested and unloads after use; Kokoro can continue becoming ready after the desktop window opens.

## LAN And Mobile

When LAN mode is enabled, the packaged backend serves both desktop and phone interfaces from port `8001`; production does not use Vite `5173`. Open `http://YOUR_PRIVATE_IP:8001` on a phone on the same private network.

Windows may ask once whether `StoryDriverBackend.exe` may use private networks. Approve only **Private networks** if phone access is desired. StoryDriver does not create port-forwarding rules or expose itself publicly.

Automated viewport checks cover `430x932`, `390x844`, `844x390`, tablet, and desktop layouts without horizontal overflow. LAN routing, PWA assets, reload recovery, and private-host API selection were verified. Physical iPhone lock-screen/background-audio behavior remains a manual acceptance item.

## Privacy And Offline Operation

Normal runtime is local-only. StoryDriver has no telemetry, cloud inference, remote fonts, CDN assets, online account, or automatic remote model download. Public service endpoints are rejected unless the advanced remote override is explicitly enabled. Local creative data stays in the configured mutable-data root.

Runtime data includes:

- `app.db`
- generated and cached narration
- custom voices and voice prompts
- local backgrounds
- exports, backups, logs, cache, and model-library entries

The installed code is separate from mutable data, so upgrades do not replace stories or settings. See [PRIVACY_AND_DATA.md](docs/PRIVACY_AND_DATA.md).

## Backup And Recovery

The packaged `StoryDriverCLI.exe` provides local maintenance commands:

```text
StoryDriverCLI.exe start
StoryDriverCLI.exe status
StoryDriverCLI.exe doctor
StoryDriverCLI.exe backup
StoryDriverCLI.exe export
StoryDriverCLI.exe restore --backup <path>
StoryDriverCLI.exe cleanup --dry-run
```

Use the desktop tray menu to open, inspect service status/LAN address, or quit the running app. Root `start_all.bat`, `check_services.bat`, and `stop_all.bat` launchers are intentionally removed.

## Source Development

Source development remains under `D:\StoryDriver`:

```text
apps/desktop        native WPF/WebView2 shell
apps/backend        packaged backend and CLI entry points
backend/app         FastAPI, generation, memory, TTS, settings, repositories
backend/data        current private development data root (Git-ignored)
frontend/src        React application
installer           NSIS installer source
runtimes/llama.cpp  local bundled runtime location (binaries ignored)
assets/desktop      icon and startup artwork
scripts             build, maintenance, diagnostics, and tests
docs                living architecture and operations documentation
release             local release artifacts (Git-ignored)
```

Source prerequisites are project-local Python under `backend\.venv`, frontend dependencies under `frontend\node_modules`, .NET 10 for the desktop shell, and NSIS/PyInstaller only for release builds. Development helper scripts remain under `scripts`; they are not installed and are not the user launch path.

Build checks:

```text
backend\.venv\Scripts\python.exe -m compileall backend\app apps\backend
cd frontend && npm run build
dotnet build apps\desktop\StoryDriver.Desktop.csproj -c Release
backend\.venv\Scripts\python.exe scripts\tests\native_desktop_contract_test.py
```

See [CURRENT_ARCHITECTURE.md](docs/CURRENT_ARCHITECTURE.md), [MAINTENANCE.md](docs/MAINTENANCE.md), and [GITHUB_RELEASE_PROCESS.md](docs/GITHUB_RELEASE_PROCESS.md).

## Troubleshooting

- Use Settings -> Diagnostics or `StoryDriverCLI.exe doctor`; do not start a second backend manually.
- StoryDriver owns port `8001`. The unrelated service on port `8000` must be left alone.
- If generation is unavailable, inspect the selected provider in Settings -> Models and run Test.
- If narration is still starting, wait for the header status to become `Narration ready`; Kokoro startup does not block the editor.
- If phone access fails, verify LAN mode, the private IP, and the Windows **Private network** firewall permission.
- A failed generation does not save a partial scene version. Narration resumes from its saved cursor and cached chunks.

## Current Limitations

- The current model/runtime produced focused deliberate scenes in roughly 3-6 minutes during the final live length contract, not the ideal 1-2 minutes. Planning/review quality remains enabled; this release does not trade away Scene Contract or continuity to hide latency.
- Long chapter output is correspondingly slower. First visible prose follows the planner and model reasoning stage.
- Qwen premium first audio is slower than Kokoro and depends on progressive prebuffering; Kokoro remains the fallback.
- A Windows signing certificate is needed to avoid normal unsigned-installer SmartScreen warnings.
- A physical phone test and subjective narrator listening remain user acceptance steps.
- Images remain paused.
