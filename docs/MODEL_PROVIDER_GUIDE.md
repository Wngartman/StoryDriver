# Model Provider Guide

## Supported Providers

StoryDriver routes all text tasks through `backend/app/providers`. The provider contract covers health, discovery, model information, load, unload, generation, streaming, cancellation, capabilities, and runtime metrics.

### Built-in llama.cpp

- Provider ID: `llama_cpp`
- Runtime: bundled Windows x64 llama.cpp `b10507`, commit `95c409c13`
- Preferred RX 7900 XTX backend: Vulkan
- CPU fallback: available through the same runtime
- Model format: compatible local GGUF
- Binding: loopback only on an owned dynamic port
- Lifecycle: started on Load/generation and stopped on Unload or app shutdown

Use Add GGUF to reference an existing file without copying it. Add Folder and Scan record compatible files in the local model library. Remove deletes only the library entry, never the model file.

The installed-runtime check loaded a 16,861,398,400-byte Qwen GGUF in 8.488 seconds and generated the exact test response in 11.446 seconds, then unloaded it and released the listener.

### Local OpenAI-Compatible

- Provider ID: `openai_compatible`
- Supports model discovery, manual model ID, streaming, timeout, and local test generation.
- Loopback and private-LAN endpoints are allowed when explicitly configured.
- Public endpoints are rejected unless `STORYDRIVER_ALLOW_REMOTE_ENDPOINTS=true` is intentionally set. That override changes the privacy boundary and is shown in diagnostics.
- Optional API keys are stored only in the local data root and are never included in presets or source control.

The provider contract was exercised against a disposable loopback fixture with exact model discovery and generation output.

### LM Studio Compatibility

- Provider ID: `lm_studio`
- Default compatibility endpoint: `http://localhost:1234/v1`
- StoryDriver discovers models and streams through LM Studio's local API.
- StoryDriver does not change LM Studio model files, templates, context, offload, reasoning, or loaded-model state.
- LM Studio may be stopped when another provider is selected; it is not required by the architecture.

## Current Migrated Selection

The currently selected route is LM Studio compatibility with:

`qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`

The older preset reference `gemma4-26b-a4b-uncensored-hauhaucs-balanced` remains in migrated settings where it previously existed, but that model was not discoverable in LM Studio and no corresponding file was found during migration. StoryDriver did not switch a real Gemma file, move a model, copy a model, or invent a replacement.

## Model Library

Settings -> Models provides:

- provider and model selection;
- current health/load state;
- model refresh;
- Add GGUF and Add Folder native dialogs;
- folder scanning;
- Load, Unload, and Test;
- local path copy/open where available;
- task assignments;
- capability details.

Built-in llama.cpp controls include context length, GPU layers/offload, threads, batch, flash attention, and parallel slots only when the runtime reports support. Generic and LM Studio routes do not show llama.cpp-only controls.

## Per-Task Routing

Profiles may independently route Story Foundation, scene planning, prose, rewrite/revise/regenerate, quality review, Story State extraction, summary, title, and utility work. Empty overrides inherit the main provider/model. A route test should be run after changing providers.

Prompt Preview remains the authority for the effective prompt order:

1. user system prompt;
2. StoryDriver task notes;
3. Story Foundation;
4. relevant character, relationship, location, blocking, object, and memory context;
5. summary and recent scenes;
6. Scene Contract and plan;
7. director note;
8. operation and writing-length instruction.

No hidden style prompt is added outside this preview.

## Troubleshooting

- A missing selected model is reported as unavailable; StoryDriver does not silently substitute one.
- Built-in Load failures are written to the local llama.cpp runtime log and shown in Diagnostics.
- Never point the generic provider at a public service unless you intentionally accept that service's privacy policy and enable the advanced override.
- Port `8000` is not StoryDriver and must not be stopped or reconfigured.
