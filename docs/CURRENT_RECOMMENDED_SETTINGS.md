# Recommended Settings

There is one normal writing path. Do not select speed/quality modes.

## Writing

- Provider: built-in llama.cpp for an existing GGUF, or an already configured local LM Studio server.
- Scene length: Scene (medium). Use Chapter only for a deliberately longer passage.
- Prose v3 system/task defaults, Scene Contract, blocking, agency, continuity and review are always active.
- Temperature 0.8, top-p 0.95; maximum output 8,000 tokens.
- Built-in context: 16,384; one slot, automatic GPU layers, runtime reasoning off.
- Keep user-authored prompt and task-note edits. Actual model context is finite even though prompt storage is uncapped.
- At most one targeted repair for a major issue.
- Built-in runtime setting changes take effect on the next model load.

Use a model that fits the machine. A 27B Q4 model requires substantially more memory than a 7B/8B model.
Normal scenes aim for one to two minutes, not a hard guarantee. Planning, background state extraction and repairs all consume inference time.
Do not change LM Studio templates, reload its model or alter external runtime configuration silently.

## Narration

Kokoro, stock female Aoede, speed 0.95, natural chunking/pacing.
CPU narration avoids consuming GPU memory needed by the writing model.
Keep progressive buffering, local chunk cache, pronunciation aliases and saved cursor enabled.
Browser narration is the final fallback. Experimental high-quality adapters are disabled by default.
No reference audio is required or bundled.

## App

Images disabled. Loopback access by default. Enable LAN only on a trusted private network.
Close exits by default; minimize-to-tray is optional. Restart to apply LAN/tray settings.
Start from StoryDriver.exe, not a development server.
