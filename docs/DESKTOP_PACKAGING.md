# Desktop Packaging

The production shell is self-contained .NET 10 WPF with WebView2. Python backend and CPU Kokoro worker are frozen in separate PyInstaller distributions.
No visible service console, external browser, Node process or system Python is required at runtime.

Startup: native artwork -> owned hidden backend -> identity/data-root health verification -> writing UI.
Kokoro warms independently; its readiness is shown inside the app. GGUF loads on demand.
Missing WebView2 displays an actionable Microsoft download button. The app does not download prerequisites silently.

All owned processes are placed in a Windows job object. Existing external services are checked but never killed or reconfigured.
One instance, native GGUF/folder pickers, window controls and optional tray behavior are retained.

The package includes backend, frontend, llama.cpp Vulkan, CPU Kokoro model and stock voices, CLI, branding and licenses.
Writing-model weights and user data are excluded.

See [Building](BUILDING.md) for reproducible commands and [Installation](INSTALLER_AND_UPGRADES.md) for data behavior.
