# Privacy And Data

Verified: 2026-08-20

## Default Boundary

StoryDriver is local-first and private by default. It has no telemetry, cloud LLM, cloud TTS, remote font, analytics, or remote runtime asset integration.

Configured service URLs must resolve to localhost, loopback, link-local, or a private-LAN address. Public URLs are rejected by backend validation unless the user intentionally sets:

```text
STORYDRIVER_ALLOW_REMOTE_ENDPOINTS=true
```

That is an advanced privacy override, not a normal setting.

## Runtime Data

Private data lives under the configured mutable-data root. The preserved installation currently uses `D:\StoryDriver\backend\data`; a clean installer can select another D:-based location:

- `app.db`: stories, scenes, versions, characters, settings, continuity, generation metadata, narration metadata;
- `generated_audio`: cached/generated story narration;
- `generated_images`: dormant preserved files from the removed image feature; not used by active runtime;
- `assets/backgrounds`: user-selected local workspace images plus a small local metadata index;
- `character_refs`: user-provided character references;
- `exports`: explicit exports;
- `backups`: intentionally created database backups;
- `logs`: operational reports and gated runtime logs;
- `temp`: recoverable in-progress artifacts only;
- `assets`: StoryDriver-owned source assets.

TTS experiments and retained synthetic listening samples live under `D:\StoryDriver\tts_engines`. No private real-story text is used in the benchmark corpus.

The rejected Chatterbox benchmark used only synthetic local Qwen Serena and Kokoro Aoede references. Official Chatterbox inference adds a local imperceptible PerTh watermark to generated audio; it is not telemetry and does not transmit text, audio, or identifiers. Chatterbox models and its venv were removed after testing, leaving only compact measurements and selected blind samples.

## Logging

Normal diagnostics store timings, counts, statuses, hashes, model/provider IDs, and error summaries. They do not intentionally persist complete prompts or generated prose.

Foundation diagnostics store an opening-note SHA-256 checksum, source kind, revision, and generation ID rather than copying opening prose into operational reports. Title, state, prompt, and narration jobs are bound to explicit story/scene/version identifiers; stale or deleted-story results are discarded instead of being redirected to the story currently open in the browser.

Raw story/prompt debug logs require:

```text
STORYDRIVER_DEBUG_LOGS=true
```

Use this only for a scoped local investigation, treat its output as private, and disable/clean it afterward.

## LAN Use

Opening StoryDriver from a phone sends director notes, prose, settings, and narration requests between that browser and the StoryDriver PC over the private network. No port forwarding is intended. Use a trusted private LAN and private-network Windows Firewall rules only. Windows may display a one-time firewall consent dialog when the packaged backend first binds to `0.0.0.0:8001`; approve only Private networks if LAN access is wanted. StoryDriver does not bypass this operating-system decision.

## Model And Voice Data

Built-in llama.cpp is an owned loopback-only subprocess. LM Studio, generic local OpenAI-compatible endpoints, and Kokoro are separate local services. StoryDriver sends text only to the explicitly selected local endpoint, does not change external runtime settings, and does not upload prose to model vendors. Public OpenAI-compatible URLs fail closed unless the advanced privacy override is intentionally enabled.

Qwen3-TTS is installed entirely under `D:\StoryDriver\tts_engines\qwen3_tts`. The retained model is loaded from an explicit D: path with Hugging Face/Transformers offline flags; normal runtime performs no model download. When Premium narration is selected, StoryDriver starts the isolated service on `127.0.0.1:8891`, sends text only to that local process, caches audio under D:, and shuts the worker down after generation or before writing. Runtime logs retain status/timing metadata rather than story prose. Qwen is not prewarmed and is not exposed to the LAN directly; the backend remains the API boundary.

Authorized custom-voice recordings, normalized references, safe prompt tensors, and previews stay under `D:\StoryDriver\backend\data\voices`. Upload requires explicit user-owned/authorized and adult dominant-speaker confirmations. No reference audio or transcript is sent to Qwen, Hugging Face, or another external service. Deleting a custom voice removes its database row and associated reference, prompt, and preview files.

Blind voice ratings are compact numeric records in browser local storage. The device diagnostic summary contains hostname, reachability, provider state, audio/media state, cursor coordinates, generated range, and service-worker state; it does not read or copy story prose or director notes.

Sidebar state, display scaling, theme/background choices, motion, and Easter-egg enablement are ordinary backend application settings. Story search operates only on story summaries already returned by the local backend. Background upload, folder refresh, image validation, serving, rename, and deletion remain under `D:\StoryDriver`; the browser never uploads a background externally. Product-polish screenshots use synthetic disposable stories and contain no private prose.

The acceleration benchmark used synthetic prose only. Four retained Serena samples are under `tts_engines\qwen3_tts\benchmarks\samples`; no private story text or external reference audio was used.

Voice cloning may use only user-owned or explicitly authorized adult reference recordings. No unauthorized real-person reference voice is bundled or copied. The retained acceptance samples use a synthetic Kokoro test voice and public synthetic text, not a private person or real story.

## Deletion

Deleting a story removes its scenes, versions, continuity, story-attached auto-created characters, and generated media while preserving unrelated manual reusable characters.

The full creative reset requires `--confirm-permanent-reset`, clears all creative records and media, checks SQLite integrity/foreign keys, and compacts the database. Application settings and required operational reports remain. Make a separate database backup first if recovery may be needed.

## Offline Verification

With internet disconnected but localhost/private LAN available:

1. Start StoryDriver.exe and the selected local model/TTS providers.
2. Confirm backend `/diagnostics` reports `local_only`, external endpoints blocked, and all configured endpoints local.
3. Generate and narrate synthetic text.
4. Inspect browser network activity for only localhost/private-LAN destinations.
5. Confirm no new external client or telemetry process is required.

The 2026-08-20 package privacy scan, external-endpoint rejection, unmounted-image-route, local runtime, and no-Qwen-worker tests passed. The packaged core starts and serves its UI/API without a public-network dependency; a physical NIC-disconnected writing/TTS run was not repeated because the current external selected model was not loaded. Internet access was used only during authorized development to obtain official build dependencies and runtime sources; normal StoryDriver runtime does not contact those repositories.

## Source Publication

The publish tree excludes mutable data, databases, stories, prompts, generated media, voice references/prompts, models, TTS weights, `.env`, logs, backups, caches, and machine-specific configuration. Release packages are assembled from a clean staging tree and do not include the development data root. A screenshot captured during final QA was deleted immediately because it contained private prose and a Windows firewall dialog; it was never staged or uploaded.
