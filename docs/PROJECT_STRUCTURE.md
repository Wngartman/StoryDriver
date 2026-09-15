# Project Structure

- apps/desktop: WPF/WebView2 shell, file dialogs, tray and owned process supervision.
- apps/backend: frozen backend and maintenance CLI entrypoints.
- apps/narration: standalone CPU Kokoro worker and exact dependency lock.
- backend/app: API, generation, state, settings, persistence and narration services.
- frontend: React/Vite source. Vite is development-only.
- assets/desktop and frontend/public/brand: shipped branding.
- installer: NSIS and PyInstaller definitions.
- runtimes: pinned manifests; downloaded binaries/models are ignored.
- scripts: build, maintenance, diagnostics and regression tests.
- docs: current product and developer documentation.
- third_party/licenses: redistribution notices; third_party/sources is ignored.
- tools: isolated build environments.
- tts_engines/qwen3_tts/service: optional legacy adapter source, not installed by default.
- build, release and .tools: ignored generated outputs.

Installed files and mutable data are separate. On the development workstation they are D:\StoryDriverApp and D:\StoryDriverData.
Other installations choose their own folders; portable data is inside the extracted application's data directory.
Normal users launch StoryDriver.exe. The root start_all.bat is only a convenience launcher for the installed executable.
No databases, user prompts, reference recordings, model weights, logs or caches are committed.
