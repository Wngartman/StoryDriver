# Current Architecture

Verified: 2026-08-20

## System Shape

StoryDriver is a native Windows WPF/WebView2 application backed by packaged FastAPI/SQLite, a production React build, provider-neutral local text generation, Kokoro, and on-demand Qwen3-TTS. React/Vite remains the source-development frontend only.

```text
Director note
  -> StoryDriver.exe (WPF/WebView2)
  -> hidden StoryDriverBackend.exe (:8001)
  -> packaged React frontend and FastAPI API on the same port
  -> bounded continuity and scene plan
  -> selected local provider
     -> bundled llama.cpp/Vulkan + GGUF
     -> generic local OpenAI-compatible endpoint
     -> optional LM Studio compatibility (:1234)
  -> review and optional single repair
  -> SQLite + background extraction
  -> selected narration provider
     -> Qwen3-TTS (:8891, configured quality provider started/stopped on demand)
     -> Kokoro (:8880, automatic fast fallback)
```

All normal runtime endpoints are localhost or private-LAN. Image generation routes, controls, settings, imports, startup checks, and normal-flow code are removed. Dormant image tables/files remain only to preserve old data safely.

## Backend Packages

| Package | Responsibility |
|---|---|
| `app.generation` | Definitive orchestration, prompt construction, and provider-neutral task routing |
| `app.providers` | Built-in llama.cpp, generic local OpenAI-compatible, and LM Studio compatibility providers |
| `app.memory` | Memory v3 projection, Story State engine, Story Foundation, summaries, character identity |
| `app.tts` | Provider interface, Kokoro and on-demand Qwen clients, custom-voice library, prosody routing, batch/chunk/cache/resume/seek |
| `app.settings` | Model/TTS/UI settings and presets |
| `app.repositories` | Generation-run persistence |
| `app.diagnostics` | Local endpoint validation, privacy status, debug-log gate |
| `app.routes` | HTTP API and streaming boundary |
| `app.services` | Remaining specialized compatibility/resource/deletion services; no mounted image runtime |

The package move is mechanical and compatibility-preserving. Large mature state and pipeline modules remain internally substantial; they were not rewritten merely to reduce line count.

## Database Model

`backend/data/app.db` is the sole runtime database. SQLite foreign keys are enabled by the application and checked after destructive operations.

The historical table name `sessions` means stories. Renaming it would add migration risk without improving authority, so it remains the root for:

- `scenes` and `scene_versions`;
- `session_characters`, character visual profiles, and references;
- Story Foundation and world notes;
- live character, relationship, scene, object, and world state;
- state events, memories, plot threads, snapshots, and extraction runs;
- summaries and Memory v3 prompt cache;
- generated image/audio records;
- deletion jobs and quality records.

The revamp adds normalized operational tables:

- `generation_runs`: one diagnostic row per saved generation/version, without storing raw prose logs;
- `narration_jobs` and `narration_chunks`: provider/chunk/cache/resume metadata;
- `pronunciation_aliases`: global/story pronunciation control;
- `schema_migrations`: applied migration bookkeeping.

Foundation rows carry `foundation_revision`, source kind, source generation ID, and opening-note checksum. Title jobs capture story plus first accepted scene/version. Narration jobs persist requested/effective provider, model, voice, prose checksum, and pronunciation/prosody/breath/custom-voice revisions. Late background results must match their captured immutable target before writing.

Indexes support story, scene, status, and cache lookup. Existing continuity tables remain canonical rather than being copied into a second competing schema.

## Generation Pipeline

The effective path is mandatory for Continue, Regenerate, Rewrite, and Revise:

1. Resolve selected scene, operation semantics, director note, and user-owned prompt/settings.
2. Retrieve bounded Memory / Character / Place v3 context plus relevant Story Foundation and Story State facts.
3. Build a compact plan with a Scene Contract, relative blocking map, agency matrix, viewpoint contract, world anchors, required beats, forbidden events, and ending handoff.
4. Generate Prose v3 while streaming status and visible prose.
5. Run compact scope, continuity, anti-fixation, and director-adherence review.
6. Run at most one repair when a major failure is identified.
7. Save a new scene for Continue or a new version on the same scene for Regenerate/Rewrite/Revise.
8. Record generation metrics and run state extraction/summary maintenance after save.

Canonical settings are reapplied after preset resolution, preventing a stale preset from disabling planning or selecting a direct path.

The planner sends a compact strict-JSON refinement request, merges field-level results into the deterministic full contract, and falls back after one malformed/timeout response. It no longer spends a second LM call repairing planner JSON. Review combines deterministic high-confidence guards with one small strict-JSON semantic review; deterministic major failures skip the review call and go directly to the single allowed repair.

## Scene Contract And Blocking

The plan defines start time, allowed duration, start location, end boundary, required and optional beats, forbidden future events, introduction obligations, backstory to dramatize, time-skip permission, scene question, emotional progression, and ending handoff.

Blocking is human-readable: zones, openings, furniture/terrain, relative character positions and orientation, carried/placed objects, sight/hearing, entries/exits, and mobility limits. Only relevant facts enter the prompt.

Present main characters also receive goals, hidden goals when supported, emotions, relationship pressure, known/hidden information, preferred plan, likely objection, and an action they may initiate. The viewpoint contract is close third by default unless the director or plan justifies controlled omniscient, explicit rotating close third, or requested first person.

## Memory Architecture

Authority order:

1. Director note and explicit manual edits.
2. Story Foundation / Character Bible stable canon.
3. Current Story State and event history.
4. Model-extracted facts with calibrated confidence.
5. Recent prose and bounded summaries as evidence.

`next_prompt_memory_cache_v3` is the prompt projection layer, not a second canon. It retrieves only relevant characters, places, objects, relationships, memories, and unresolved threads for its explicit story. Recent-scene context is bounded, and longer stories gain rolling summaries so prompt growth does not track the entire manuscript. Foundation fallback never queries the newest global foundation or character row; it derives from the current opening note and current story attachments only.

The post-alias 20-scene continuity gauntlet kept writer prompts bounded at an average 28,771 characters and maximum 29,950. Relevant old memory retrieval took 12.63 ms. Database growth was 105,677 bytes per scene, projecting about 105.68 MB decimal for 1,000 scenes.

Relationships use one canonical unordered pair with concrete history, trust/affection/resentment/pressure, promises/secrets, each party's wants, current issue, last meaningful change, confidence, and source version. Blocking accepts only physically meaningful locations, positions, movement, presence, visibility/hearing, and object placement. Object state separates owner, holder, location, condition, and placement; identity normalization prevents aliases such as `key`, `key from`, or `key tight` from creating duplicate holdings.

## Model Discovery And Routing

`GET /models` discovers models from the selected provider and local model library. Settings can add an existing GGUF, add/scan folders, refresh an external local provider, select, load, unload, and test without copying or deleting model files. Provider capabilities control which runtime settings are shown.

Task routing uses one selected local model by default, with editable provider/model profiles for planning, prose, rewrite/revision, review, title, summary, foundation, state extraction, and utility work. The current selected route is LM Studio compatibility with `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`. The older Gemma preset references remain intact, but its file/model was absent during migration and is not claimed as selected.

The bundled llama.cpp Windows x64 runtime is `b10507` commit `95c409c13`. Vulkan is the tested RX 7900 XTX backend, with CPU fallback. An installed runtime test loaded an existing 16.86 GB GGUF, generated exact expected text, and unloaded cleanly. The generic provider passed discovery and generation against a disposable loopback fixture. LM Studio remains compatible but optional, and StoryDriver never changes its model/template/runtime configuration.

## Native Desktop And Packaging

`apps/desktop` is the self-contained .NET 10 WPF shell. It owns the startup image, single-instance activation, native title bar, file dialogs, tray, close policy, and Windows job-object child cleanup. `apps/backend` contains the frozen backend and maintenance CLI entry points. Production uses no external browser and no visible console.

The shell shows its native startup screen while the hidden backend becomes healthy. It opens the main editor after backend readiness and allows Kokoro to finish asynchronously; the React header exposes only the calm narration loading/ready state. Heavy Qwen and llama.cpp processes start on demand.

PyInstaller one-directory packages the backend and CLI. NSIS builds the installer. Mutable data is configured independently from immutable installed binaries; clean and portable packages contain no private database, model weights, voice files, generated media, logs, or secrets. The current source layout remains conservative rather than moving mature packages only to match a diagram.

## Narration

The provider interface exposes health, voices, profiles, chunk synthesis/streaming, duration metadata, cancel, unload, and diagnostics.

- `high_quality_local`: configured Qwen3-TTS 0.6B provider; built-in Serena uses CustomVoice and authorized local clones use Base, with on-demand CPU bfloat16/SDPA continuous ordered batch-four generation and Kokoro fallback.
- `kokoro`: immediate fast fallback, local chunk cache/resume, pronunciation aliases.
- `browser`: last-resort provider, disabled by default.

Natural chunking respects paragraphs, dialogue blocks, quote/attribution pairs, sentences, and phrases. It packs roughly 160-240 characters when boundaries allow. Playback preloads and decodes the actual next audio element, adopts that same element at transition time, and keeps a measured reserve rather than discarding the preload and assigning the URL again. It uses measured audio durations for cross-chunk rewind/forward, restricts scrubbing to the contiguous generated range, and persists a canonical text-hash/chunk/time cursor so provider loss or page refresh does not discard position. Legacy text hashes remain accepted for existing saved cursors. Cached audio/manifest pairs use access-refreshed LRU eviction with a configurable 5 GB default ceiling.

The isolated Qwen worker exposes health/status/voices/load/unload/synthesize/synthesize-batch/voice-prompt/cancel/metrics on `127.0.0.1:8891`. A Premium request starts the lightweight service on demand; cache hits are returned without loading the model. Built-in voices load the explicit D: CustomVoice model; cloned voices load the pinned D: Base model and a checksum-versioned safe prompt. Cache identity includes model, requested/effective provider and voice, custom voice/prompt revision, style, pronunciation profile, speed, and normalized text. The frontend pipelines ordered batches, measures its reserve, preloads/decode-schedules the existing audio player, and persists the same provider/text/chunk/time cursor used by Kokoro. When all chunks are generated the worker shuts down. Before text generation, the backend cancels active Qwen work and shuts down the worker; it never silently unloads an external writing model.

Custom voice records live in SQLite and files live under `backend/data/voices/{references,prompts,previews,temp}`. Upload requires authorization and adult dominant-speaker confirmations. WAV, FLAC, and MP3 are normalized locally to mono 24 kHz PCM16 WAV with silence trimming, modest level normalization, and clipping/noise/speech-duration checks. Deleting a voice removes its files and makes any selected deleted voice fall safely back to Kokoro. Conservative prosody routing uses explicit quoted-dialogue and whisper/soft/heightened cues; ordinary atmospheric words do not trigger a style. The installed 0.6B built-in model does not support instruct-style delivery, so unsupported expression is reported as normal rather than claimed falsely.

The official installed Qwen wrapper does not emit decodable output packets; StoryDriver does not claim true Qwen streaming. Progressive behavior comes from a producer that continuously generates ordered sentence batches independently of playback boundaries. The current six-minute run played 361.12 seconds across 84 chunks with zero underruns and a 12 ms maximum gap. The current custom twelve-minute run played 720.08 seconds across 215 chunks with zero underruns and a 25 ms maximum gap, including 147 automatic style switches. A failed Qwen batch is regenerated by Kokoro with requested/effective/fallback metadata, preserving the playback cursor.

Official Chatterbox Turbo, Original English, and Multilingual V3 were benchmarked but are not providers. Turbo's best supported CPU RTF was `1.1414`; Original/V3 ranged `2.3713-2.4715`, so every variant failed the finite-buffer continuous gate. The isolated venv/models were removed after compact JSON evidence and blind samples were retained. The local PerTh watermark in Chatterbox output is an imperceptible provenance mark, not cloud telemetry.

## Frontend

The first screen is the working studio: a 280 px expanded desktop sidebar or 68 px rail, prose feed, aligned composer, and mini-player when narration is active. Mobile uses a drawer. Expanded search filters the loaded story summaries locally after a 140 ms debounce. Settings and other advanced drawers are lazy-loaded. There are no user-facing writing quality modes; the definitive pipeline is shown as an invariant. Completed scene cards show only raw visible prose `t/s`; model IDs, warning scripts, timing detail, More Stats, Scene QA Checklist, readiness copy, and all image controls are absent from the normal flow.

Active-story loads use request sequences and story-ID checks. Switching stories clears prior detail data immediately; late context, prompt-preview, scene, stream, title, or narration responses cannot overwrite the newly active story. Title completion polling is bounded and runs concurrently with auto-read, so narration generation cannot delay the title UI update.

Display settings are backend-persisted and shared by every theme: reading width, prose font/size/line height/paragraph spacing, composer size/text/spacing, global and mobile scale, motion, and workspace backgrounds. A background is a pointer-free, clipped layer beneath the prose feed and composer. Only current and next images are preloaded; one timer handles ordered or random rotation and pauses while the tab is hidden. Files remain under `backend/data/assets/backgrounds`.

Settings is searchable and organized as General, Writing, Models, Narration, Memory & Continuity, Appearance, LAN & Privacy, Diagnostics, and About. Normal model controls are compact; provider-specific and per-task controls live under Advanced. One central explanation registry drives delayed hover, keyboard-focus, and touch help. Audited hot surfaces use scoped Zustand selectors and memoized scene/story components. The desktop header polls the compact narration/startup state every two seconds only while visible; broader health refresh remains bounded. Browser acceptance passed at six viewports from 390x844 through 1920x1080 without horizontal overflow.

## Privacy And Failure Boundaries

Pydantic endpoint validation rejects public service URLs unless `STORYDRIVER_ALLOW_REMOTE_ENDPOINTS=true` is intentionally enabled. Diagnostics expose the configured endpoint map and local status. Raw prompt/prose logs require `STORYDRIVER_DEBUG_LOGS=true`.

The native supervisor identifies processes by port, command line, ownership marker, and Windows job membership. It stops only owned StoryDriver processes. The unrelated service on port 8000, LM Studio, and external local providers are outside its stop boundary.

Generation saves only on successful completion. Scene versions protect same-scene edits. Narration cache/cursor data supports reconnect and resume. Story deletion is backgrounded, audited without prose content, removes attached auto-created characters/media, and preserves unrelated manual reusable characters.
