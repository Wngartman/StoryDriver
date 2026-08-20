# StoryDriver 1.0.0-rc.1

This release candidate introduces the native Windows StoryDriver application.

## Included

- Native WPF/WebView2 desktop window with no browser or console launcher.
- Minimal local startup screen while the backend starts; narration reports its own readiness in the app.
- Hidden packaged FastAPI backend serving the production React interface on one local port.
- Optional tray operation and single-instance activation.
- Built-in llama.cpp Vulkan provider for local GGUF models.
- Generic local OpenAI-compatible and optional LM Studio providers.
- Local model library, provider-aware task routing, and compact searchable settings.
- Existing StoryDriver writing, Story State, continuity, Kokoro, Qwen3-TTS, themes, and LAN/PWA paths.
- `StoryDriverCLI.exe` for status, diagnostics, backup, export, restore, cleanup, and launch.

## Privacy

No story database, prompt, voice reference, generated narration, model weight, local log, backup, API key, or user background is included. Runtime network access is restricted to loopback and explicitly configured private-LAN endpoints.

## Known limitations

- The build is unsigned and Windows SmartScreen may warn on first launch.
- A compatible Microsoft Edge WebView2 Runtime must be installed. It is included with current supported Windows installations and is not downloaded by StoryDriver.
- Model and TTS weights are intentionally not bundled.
- Private-LAN access may require approving StoryDriver in Windows Firewall on first enablement.
