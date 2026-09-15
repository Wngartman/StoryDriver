# Building StoryDriver

Windows x64 build prerequisites: Python 3.12, Node 22+, .NET SDK 10 and NSIS 3.12.
Use isolated local environments; installed releases require none of these developer tools.

The exact tested build recipe, runtime URLs and SHA256 checks are in .github/workflows/windows-release.yml.

```powershell
python -m venv tools/packaging/.venv
tools/packaging/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt pyinstaller==6.22.2
python -m venv tools/narration/.venv
tools/narration/.venv/Scripts/python.exe -m pip install -r apps/narration/requirements.lock.txt
npm ci --prefix frontend
tools/packaging/.venv/Scripts/python.exe scripts/tests/run_release_checks.py
tools/packaging/.venv/Scripts/python.exe scripts/build_native_release.py
```

Before the final command, prepare the pinned llama.cpp Vulkan archive under .tools/downloads and extract it into runtimes/llama.cpp; extract NSIS into .tools/nsis-3.12/nsis-3.12. Follow the workflow for exact verified URLs/checksums.

The builder prepares pinned public Kokoro assets, freezes backend/CLI/narration, publishes the self-contained desktop, collects licenses, and produces installer, portable ZIP, corresponding-source ZIP and checksums under release.
It uses project-local temp, npm/pip/NuGet/PyInstaller caches.

No writing model, private reference voice, story database or user configuration is included.
The separate narration worker uses GPL dependencies; ship its corresponding-source archive with binaries.
Do not sign using someone else's certificate or upload private local build data.

For local frontend development use npm run dev --prefix frontend with an initialized backend.
For production testing use the packaged app and scripts/tests/native_package_smoke_test.py.
