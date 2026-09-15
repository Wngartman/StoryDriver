# StoryDriver Context

Last updated: 2026-09-14. Canonical development root: D:\StoryDriver.

1.0 is published at https://github.com/Wngartman/StoryDriver/releases/tag/v1.0.0 (sanitized source commit c27dbad).
The final installer was tested for fresh installation, upgrade configuration preservation and data-preserving uninstall.
Use the Windows GetFullPathNameW API in the installer: NSIS GetFullPathName can empty a nonexistent destination. Never remove the fail-closed path/payload checks.

## Product Contract

StoryDriver is a local directed-fiction studio, not a chatbot. Director note -> scene -> saved version -> narration.
Continue appends a scene. Regenerate, Rewrite and Revise create versions of the same scene.
User system prompt and editable task notes are creative authority. Story State supplies factual continuity.
No hidden moralizing/style overrides. Images are disabled.

## 1.0 Runtime

- Native WPF/WebView2 shell, frozen FastAPI backend, React production assets, SQLite.
- Bundled llama.cpp b10507 Vulkan on loopback :12345; selected GGUF loads on demand.
- Optional LM Studio or local OpenAI-compatible service; never silently alter external model settings.
- Bundled CPU Kokoro ONNX on :8880, Aoede stock voice, starts independently after core startup.
- Legacy Qwen adapter remains optional and disabled by default; no private reference audio ships.
- One window, no visible service consoles. Native-owned processes belong to a Windows job object.
- Default port :8001, loopback only. LAN is opt-in, trusted networks only, no authentication.
- On this workstation :8000 belongs to another application and must never be stopped.

The fresh local installation uses D:\StoryDriverApp and D:\StoryDriverData. The previous data was moved once to D:\StoryDriverBackups\pre-1.0-20260914 before the user-authorized refresh. Do not publish either data directory.
Other users can choose their own folders; portable data lives beside the executable.

## Mandatory Writing Path

Bounded memory -> Story Foundation -> compact deterministic-plus-model Scene Contract, blocking, agency and viewpoint -> Prose v3 -> compact review -> at most one major-failure repair -> scene/version save -> background Story State and summary.
Never reintroduce multiple quality modes. First-person only when requested; explicit time/scope boundaries remain hard.
Stored system prompt length is uncapped, but actual model context remains finite.
Manual canon overrides extraction. Object ownership and character position must remain consistent.

## UI

Writing-first sidebar, central prose, compact composer and mini-player.
One Settings drawer: Writing, Narration, Appearance, App. Writing has an Advanced disclosure.
Settings flush on close; failed saves remain visible. Model/task-note drafts must not reset during typing.
Narration shows starting/ready/offline accurately. No fake connection or model-load success.
Browser narration permits only explicitly local voices and fails closed when none are available.
All themes retain responsive geometry and readable touch inputs.

## Safety And Release

Preserve current user data unless explicitly authorized otherwise. One meaningful backup before risky changes.
Keep local tools, caches, data and build outputs on D: here. No global packages or CUDA.
Use project-local dependencies. Do not modify C: intentionally.
Development history may contain private legacy material. Public main is a sanitized successor to the existing public-release root, not a push of development history.
Exclude DBs, caches, logs, private recordings, model weights and environment files from Git.
Kokoro public model assets are bundled only in binaries, with licenses and GPL corresponding sources.
Unsigned builds may trigger SmartScreen. WebView2 is a documented Microsoft prerequisite.

## Verification

Run tools/packaging/.venv/Scripts/python.exe scripts/tests/run_release_checks.py.
Run frontend build, actual local generation, actual narration, browser desktop/mobile checks, packaged startup and backup/restore checks.
Distinguish deterministic tests, live model results, visual inspection and subjective human acceptance.
Latest consolidated local evidence: backend/data/logs/STORYDRIVER_1_0_RELEASE_REPORT.md.
See docs/BUILDING.md and docs/CURRENT_ARCHITECTURE.md for packaging and boundaries.
