# Project Structure

The source tree remains conservative: mature frontend/backend modules were not moved merely to match a diagram.

- `apps/desktop`: self-contained .NET 10 WPF/WebView2 shell, startup screen, tray, one-instance lock, and child supervision.
- `apps/backend`: frozen backend and `StoryDriverCLI` entry points.
- `assets/desktop`: native icon and neutral loading artwork.
- `backend/app`: FastAPI application, generation, providers, memory, TTS, settings, repositories, diagnostics, routes, and retained compatibility services.
- `backend/data`: current private mutable data root; ignored by Git except safe placeholders.
- `frontend`: React/Vite source and project-local dependencies; Vite is development-only.
- `installer`: NSIS source.
- `runtimes/llama.cpp`: local bundled runtime location; downloaded binaries are ignored and verified by manifest/hash.
- `scripts`: developer builds, maintenance, diagnostics, migrations, and tests.
- `docs`: living product, architecture, provider, packaging, privacy, installer, and release documentation.
- `tts_engines`: isolated D:-only local TTS engines, benchmarks, and selected synthetic samples.
- `release`: ignored local installer/portable artifacts.
- `tools`: ignored D:-only build environments and package tools.

Installed immutable binaries currently live under `D:\StoryDriverApp`. The existing mutable data remains at `D:\StoryDriver\backend\data`; the installer can select a separate D:-based data root for clean installations. Portable mode stores data beside the extracted app.

Root `start_all.bat`, `check_services.bat`, and `stop_all.bat` are removed. Users launch `StoryDriver.exe`; the tray and packaged `StoryDriverCLI.exe` provide operations. Batch wrappers under `scripts` remain developer/test helpers and are not installed product entry points.

Generated databases, backups, stories, media, voices, model weights, logs, temp files, local configuration, provider secrets, TTS caches, Node dependencies, venvs, build trees, and release artifacts are ignored by Git.
