# StoryDriver 1.0.0

A local directed-fiction studio for Windows 10/11 x64.

## Start

Install StoryDriver-Setup-x64.exe, or extract the portable ZIP and open StoryDriver.exe.
In Settings > Writing, add your own GGUF or connect a running local LM Studio model.
Kokoro narration is included and starts automatically. No Python, Node, CUDA, account or cloud TTS is required.

## Included

- Native startup artwork, hidden services, truthful narration readiness.
- One Settings drawer: Writing, Narration, Appearance, App.
- Built-in llama.cpp Vulkan and optional local model providers.
- Scene Contract, blocking, character agency, Story State, Prose v3 and bounded review/repair.
- Same-scene Rewrite/Revise/Regenerate versions.
- Uncapped stored system prompt; actual model context remains finite.
- Bundled CPU Kokoro Aoede, progressive caching, pronunciation and saved playback position.
- Local backup/restore CLI and data-preserving upgrades/uninstall.
- Images remain disabled. No personal stories, prompts, recordings or writing-model weights ship.

## Requirements And Limits

Microsoft Edge WebView2 Evergreen Runtime is required. The app offers Microsoft's download page if missing.
Use a writing model that fits your GPU/RAM. Latency and quality depend on the model; long scenes and repairs may exceed two minutes.
Unsigned binaries may trigger Windows SmartScreen. Verify SHA256SUMS.txt.
LAN has no authentication: trusted private networks only, never port-forward.
Physical phone background playback and subjective narration quality require human acceptance.

## Licenses

Core MIT; separate narration worker/phonemizer/eSpeak chain GPL-3.0-or-later; Kokoro model and stock voices Apache-2.0.
See THIRD_PARTY_NOTICES.md and installed LICENSES. Corresponding narration sources accompany this release.
