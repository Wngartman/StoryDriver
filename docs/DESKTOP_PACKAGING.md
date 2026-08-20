# Desktop Packaging

## Shell Decision

The production shell is self-contained .NET 10 WPF with Microsoft WebView2. Tauri 2 was preferred initially, but the required Rust/Cargo and MSVC toolchain were absent. Installing system toolchains was outside the task's safety boundary. Electron was rejected because it would bundle a second browser runtime and increase package size and idle memory.

The selected shell preserves Windows snap/maximize behavior, taskbar grouping, one-instance activation, native file dialogs, a system tray, and the existing React UI. WebView2 runtime package `1.0.4129.50` is pinned in the project.

## Startup Sequence

1. `StoryDriver.exe` acquires the single-instance mutex.
2. A native startup window displays `assets/desktop/storydriver-loading.png` and progress.
3. The shell starts `StoryDriverBackend.exe` hidden with the configured immutable install root and mutable data root.
4. After `/health` succeeds, the main WebView opens `http://127.0.0.1:8001`.
5. Kokoro continues booting independently. The React header polls a compact service endpoint and shows `Narration starting` until ready.
6. Qwen3-TTS and built-in llama.cpp remain unloaded until requested.

No external browser, PowerShell window, command prompt, Vite server, Node process, or system Python is part of installed core startup.

## Process Ownership

The WPF shell places owned child processes in a Windows job object so they are cleaned up on a normal exit or shell crash. It validates ports before reuse and does not kill external LM Studio, external OpenAI-compatible providers, or unrelated services. Port `8000` is explicitly outside StoryDriver ownership.

Close behavior is configurable as Exit or Minimize to tray. Tray actions are Open StoryDriver, Service status, LAN address, and Quit. Narration can continue while minimized.

## Packaged Backend

The backend and CLI use PyInstaller 6.22.2 one-directory builds. One-directory was selected over one-file extraction for predictable startup and dependency behavior. Production React assets are embedded under the backend package and served by FastAPI on the same port as the API.

The core package includes:

- WPF/WebView2 desktop shell;
- frozen FastAPI backend;
- frozen `StoryDriverCLI.exe`;
- production frontend assets;
- llama.cpp Windows Vulkan runtime;
- icon and startup artwork;
- migrations and clean sample configuration.

It excludes databases, models, TTS weights, voices, generated media, logs, backups, caches, `.env`, and local provider secrets.

## Build

Run from the repository root with project-local dependencies already prepared:

```text
backend\.venv\Scripts\python.exe -m compileall backend\app apps\backend
cd frontend && npm run build
dotnet build apps\desktop\StoryDriver.Desktop.csproj -c Release
tools\packaging\.venv\Scripts\python.exe scripts\build_native_release.py
```

The release builder validates required files, builds the frozen backend/CLI and desktop shell, assembles a clean application tree, creates portable and NSIS outputs, and writes SHA-256 checksums.

## Verified Local Measurements

- Portable window visible: 2.238 seconds
- Portable backend ready: 5.565 seconds
- Installed window visible: 1.134 seconds
- Installed backend ready: 3.347 seconds
- WPF shell working set: 187.9 MB
- Six WebView2 process working sets: 523.9 MB combined
- Frozen backend working set: 104.5 MB
- Kokoro parent/worker/hidden conhost working sets after ready: 1,385.3 MB combined
- Total process working-set sum after Kokoro ready: 2,201.6 MB (includes shared pages)
- Unpacked core: 389,025,347 bytes across 610 files
- Installer: 103,116,875 bytes
- Portable ZIP: 153,277,549 bytes

Generation and Qwen narration model memory are workload-dependent and are not counted as idle core memory.
