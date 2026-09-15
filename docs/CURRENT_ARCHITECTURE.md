# Current Architecture

## Processes

StoryDriver.exe (WPF/WebView2) -> owned hidden StoryDriverBackend.exe -> FastAPI + React production assets on :8001.
SQLite, settings, caches, logs, exports and WebView storage live in the configured data root, separate from application files.
Built-in llama.cpp uses its owned :12345 Vulkan process and a user-supplied GGUF. External local providers are optional.
Bundled StoryDriverNarration.exe runs CPU Kokoro ONNX on :8880. It is independently supervised and reports ready only after model warmup.

The desktop validates both backend identity and data-root identity before reusing a listener. It never terminates unrelated listeners.
Owned children use a Windows job object. Model load/unload is serialized and failed or cancelled loads are cleaned up.
The app never silently unloads an external writing model.

## Writing And Persistence

backend/app/generation owns the deliberate pipeline and provider routing.
backend/app/memory owns bounded context, Story Foundation, extraction and Story State.
backend/app/repositories owns scene/version persistence.
backend/app/settings owns prompts, task overrides and settings.
backend/app/routes exposes the HTTP API.

Continue creates a scene. Rewrite, Revise and Regenerate produce same-scene versions.
Every request retains scene contract, time boundary, blocking, agency, viewpoint, world anchors and forbidden future events.
The writer receives concise relevant continuity; review is bounded and only a major failure can trigger one repair.
User prompts remain authoritative; no fixed character cap is imposed on their storage.

## Narration

backend/app/tts owns provider abstraction, normalization, pronunciation aliases, chunk cache and metadata.
frontend narration services own progressive prebuffering, playback, generated-only seeking and persisted cursor.
The bundled stock Aoede voice requires no reference recording. Optional legacy adapters do not download or enable themselves.
The standalone worker is GPL-3.0-or-later; core application is MIT. It communicates through the local speech API.
Browser fallback explicitly selects an offline system voice; it never silently chooses an online/default browser voice.

## UI And Security

One Settings drawer: Writing, Narration, Appearance, App.
Native file dialogs add GGUF paths. Model IDs for built-in generation are full local paths.
Settings changes save serially and flush before the drawer closes.
Native bridge messages are restricted to the application origin.
Unknown API routes return 404, not frontend HTML. Cross-origin mutation guard blocks public website origins.
Loopback is the default binding. Opt-in private LAN has no authentication; it is not an internet service.
Images are disabled. Runtime data and build outputs never belong in the public source tree.

## Release Verification

Version 1.0.0 has installer and portable releases. Actual Windows installation, upgrade configuration preservation and data-preserving uninstall were exercised with disposable fixtures.
Destination normalization uses checked Windows GetFullPathNameW so a directory that does not exist yet cannot become an empty installation path.
The release checks include live writing/version operations, CPU narration, 60-chunk browser playback, reload resume, desktop/mobile geometry and SQLite backup/restore.
Model output quality and physical-phone background playback remain separate from deterministic build/runtime acceptance.
