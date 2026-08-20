# StoryDriver Codex Context

Last verified: 2026-08-20
Canonical root: `D:\StoryDriver`

Read this file before changing StoryDriver.

## Product Contract

StoryDriver is a private local directed-fiction studio. User input is a director note or instruction. Model output is a scene, prose passage, or scene version, never a chat reply.

Core semantics:

- Continue creates a new scene.
- Regenerate, Rewrite, and Revise create a new version of the same scene.
- User system prompt and editable task notes are creative authority.
- Story State is factual continuity, not hidden style steering.
- Legal adult fictional writing remains supported without moralizing UI or hidden restrictions.
- Image generation is removed from the active product. Dormant compatibility data may remain, but no image routes, controls, startup checks, or production imports may re-enter normal runtime without a separate accepted project.

## Current Runtime

| Service | URL | Status/role |
|---|---|---|
| Desktop | `D:\StoryDriverApp\StoryDriver.exe` | WPF/WebView2 native shell; normal installed launch |
| Production app/API | `http://localhost:8001` | Frozen FastAPI serves React assets, API, and SQLite |
| Development frontend | `http://localhost:5173` | Vite development only; never used by the installed app |
| Built-in llama.cpp | owned loopback port | GGUF generation through bundled Windows Vulkan runtime |
| LM Studio | `http://localhost:1234/v1` | Optional local compatibility provider |
| Local OpenAI-compatible | configured local endpoint | Generic loopback/private-LAN provider |
| LM Studio REST | `http://localhost:1234/api/v1` | Discovery and loaded-instance diagnostics |
| Kokoro | `http://localhost:8880` | Local progressive narration |
| Qwen3-TTS | `http://localhost:8891` | Premium narrator; isolated worker starts/stops on demand |

Port 8000 belongs to an unrelated FishIntel service on this machine. StoryDriver launch/stop ownership checks must never terminate it. Do not alter LM Studio load/template/runtime settings unless explicitly requested.

Selected prose route: LM Studio compatibility with `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max`. The older `gemma4-26b-a4b-uncensored-hauhaucs-balanced` preset references were preserved, but the model/file was absent from the current LM Studio inventory and disk during migration. Do not claim that missing Gemma is currently selected or silently replace it.

Installed startup is windowless except for StoryDriver's native startup/main windows. The main UI opens as soon as the packaged backend is healthy; Kokoro may continue starting, with `Narration starting` shown until ready. No browser, console, Node, Vite, or system Python is required for the installed core. Private-LAN binding may require one user-approved Windows Private network firewall permission.

## Product-Polish UI Contract

- Desktop uses a 280 px expanded sidebar or 68 px collapsed rail. Mobile uses a drawer. Sidebar state is stored in backend UI settings.
- Story search is local, title-focused, debounced, keyboard accessible, and does not transmit or log story text.
- All themes share reading width, prose typography, composer sizing, UI/mobile scale, motion, and workspace-background tokens.
- Local backgrounds live under `D:\StoryDriver\backend\data\assets\backgrounds`; only PNG/JPG/WebP files that pass header, size, and dimension validation enter the library. They render only in the clipped workspace layer.
- Motion is Off, Subtle, or Full; explicit Off and `prefers-reduced-motion` suppress nonessential motion.
- Model Settings has one compact Writing view and one Advanced view. Supported per-task overrides remain available. Duplicate model IDs, duplicate reset paths, and removed-image controls must not return.
- Settings explanations come from one registry and work after a 750 ms hover delay, on focus, and on touch. Escape or scroll dismisses the portal tooltip.
- Branding sources live under `frontend/public/brand`; local Lucide UI icons retain accessible names. Exactly two optional, nonpersistent Easter eggs exist: the two-second logo hold and five title clicks.

## Definitive Generation Path

Every Continue, Regenerate, Rewrite, and Revise request uses:

1. Bounded Memory / Character / Place v3 retrieval.
2. Story Foundation and relevant Story State projection.
3. Compact scene plan with Scene Contract, blocking, agency, viewpoint, world anchors, and forbidden future events.
4. Prose v3 generation with streaming status/prose.
5. Compact anti-fixation, scope, continuity, and director-note review.
6. At most one repair, only for a major repeatable failure.
7. Save scene or same-scene version plus `generation_runs` metadata.
8. Background state extraction and rolling-summary maintenance.

The effective values are always `prose_prompt_mode=standard`, `writing_process_mode=deliberate`, `writing_path=deliberate_pipeline`, and `app_planning_enabled=true`. Do not reintroduce selectable direct/fast writing modes.

Scene planning must enforce time span, start/end boundary, required/optional beats, forbidden future events, introduction obligations, backstory handling, scene question, emotional progression, ending handoff, present-character blocking, object ownership, agency, and viewpoint. Long requested word counts deepen the current scene instead of granting an implicit time skip.

The planner asks the model only for compact high-value refinements and merges them into a deterministic complete contract. Strict structured output is capped at one model call; malformed or timed-out output falls back immediately to the deterministic plan rather than spending another model call on JSON repair. Review uses deterministic high-confidence checks plus one compact structured semantic call. Only a major issue can trigger the single repair pass.

## Memory Authority

- `sessions` is the compatibility table name for stories and remains the single story root.
- Story Foundation/character bible stores stable setup and explicit canon.
- Manual character/world/state edits outrank extracted facts.
- Story State v2 stores live facts and history for people, relationships, places, scenes, objects, injuries, clothing, knowledge, secrets, goals, memories, and plot threads.
- `next_prompt_memory_cache_v3` is the bounded prompt projection.
- Rolling summaries are created for longer stories; recent scene context stays bounded.
- `STORY FOUNDATION / CHARACTER BIBLE` compatibility markers must remain intact.
- Auto-created story characters are deleted when their story is permanently deleted; manual reusable characters are preserved unless explicitly deleted.

## Backend Boundaries

Canonical packages:

- `backend/app/generation`: orchestration, prompt building, provider-neutral task routing.
- `backend/app/providers`: built-in llama.cpp, generic local OpenAI-compatible, and LM Studio compatibility implementations.
- `backend/app/memory`: retrieval/context, state engine, foundation, summaries, character identity.
- `backend/app/tts`: provider registry, Kokoro client, chunk/cache service, profiles.
- `backend/app/settings`: persisted settings/presets.
- `backend/app/repositories`: generation persistence boundaries.
- `backend/app/diagnostics`: privacy and gated debug behavior.
- `backend/app/routes`: HTTP surface.

Do not recreate deleted duplicate modules under `backend/app/services`.

## TTS

The current saved selection is Qwen3-TTS 0.6B Base with authorized custom profile `Arabella`. Kokoro `Aoede` is the immediate automatic fallback. Do not silently replace either saved selection. Both paths retain natural progressive chunks, pronunciation aliases, exact cached chunk URLs, actual-duration rewind/forward, generated-only seeking, and saved cursor/resume.

Qwen3-TTS 0.6B CustomVoice is installed under `D:\StoryDriver\tts_engines\qwen3_tts` with built-in Serena; the pinned 0.6B Base model handles authorized local custom voices. The worker starts on demand, uses CPU bfloat16 + SDPA + 4/4 threads, continuously fills an ordered batch-four queue, caches every chunk, and shuts down after generation. Latest measured backend first audio is 23.974 seconds cold, 16.736 seconds warm, and 0.064 seconds from exact cache. The current six/twelve-minute runs played 361.12/720.08 seconds with 12/25 ms maximum transitions and zero underruns. Any Qwen batch failure returns Kokoro chunks while persisting requested/effective/fallback truth. StoryDriver cancels/shuts down Qwen before text generation but does not unload Gemma. Browser fallback exists but is disabled by default. Only user-owned or explicitly authorized reference audio may be used for voice cloning.

## Story Isolation And Jobs

Every audited story-owned foundation, detail, prompt, title, state, generation, and narration path captures an immutable `session_id` (story ID) and, where applicable, scene/version/job/revision identity. Background results are discarded if their captured story was deleted, their revision changed, or their target no longer matches. The frontend clears story-detail state on a story switch and rejects stale context, prompt-preview, scene, streaming, title, and narration responses. Automatic title work is nonblocking and updates the sidebar, header, active store, and document title without refresh; manual titles clear pending job identity and cannot be overwritten.

Foundation fallback is derived only from the current opening note and current story inputs. The prior apparent cross-story leak was a deterministic fallback defect: it forced two characters and reused hardcoded modern example names in independently generated foundations. Fallback now respects an explicitly lone protagonist, chooses setting-appropriate names only when invention is necessary, and records story/revision/source/generation/checksum provenance.

Automatic breath performance is hidden metadata, never story markup. Settings exposes only `Off`, `Natural`, and `Cinematic`; persisted default is `Natural`. Explicit prose cues are confidence-gated, ordinary Natural insertions are limited to roughly one per 45 seconds, and romance/adult/dark/suspense language alone never triggers breathing. Each custom voice may hold six optional authorized WAV/FLAC/MP3 breath references under `backend/data/voices/breaths`; missing references produce no separate breath. Web Audio schedules accepted samples at 1x between speech units and includes their decoded duration in seek, progress, pause/resume, and refresh cursors. Cache identity includes mode/event/sample revision while cursor position survives voice, speed, provider, and mode identity changes. See `backend/data/logs/AUTOMATIC_BREATH_PERFORMANCE_REPORT.md`.

Official Chatterbox Turbo, Original English, and Multilingual V3 were tested in an isolated D:-only CPU environment. Best Turbo RTF was `1.1414`; Original/V3 were `2.37-2.47`. Chatterbox therefore failed the continuous-playback gate, was not integrated, and its 12.06 GiB environment/models were removed. Retain compact evidence under `tts_engines\chatterbox` and the blind pack under `tts_engines\samples\chatterbox_comparison`. Chatterbox output carries the official local imperceptible PerTh watermark; it is not telemetry and transmits nothing. Settings -> Narration provides a local Kokoro/Qwen blind comparison and prose-free device diagnostics.

## Privacy And Data

- Runtime service endpoints must be localhost or private-LAN by default.
- External endpoints fail closed unless `STORYDRIVER_ALLOW_REMOTE_ENDPOINTS=true` is intentionally set.
- Raw story/prompt debug logging is off unless `STORYDRIVER_DEBUG_LOGS=true`.
- No cloud LLM, cloud TTS, telemetry, remote fonts, or remote runtime assets.
- Keep StoryDriver code, databases, caches, venvs, reports, and media on D:.
- Do not use global pip/npm or install CUDA/NVIDIA packages.

## Operations

Installed users launch `D:\StoryDriverApp\StoryDriver.exe`. Open/status/LAN/quit operations are in the tray. `StoryDriverCLI.exe` provides `start`, `status`, `doctor`, `backup`, `export`, `restore`, and `cleanup`. Root `start_all.bat`, `check_services.bat`, and `stop_all.bat` are removed; developer/test wrappers may remain under `scripts` but are not product launchers.

Permanent creative reset, only with explicit authorization:

```bat
D:\StoryDriver\scripts\reset_storydriver_creative_data.bat --confirm-permanent-reset
```

The Qwen/streamline checkpoint is Git commit `9603ac97829a4365ed1b844c7388b1fa60e21d23`, tag `pre_qwen_tts_streamline_memory_reliability_20260713`. Its database/settings backup and exact rollback instructions are recorded in `backend/data/logs/QWEN_TTS_STREAMLINE_ROLLBACK.md`.

The Chatterbox/planner checkpoint is Git commit `d074c2ea360a34042104e205ce09ed15cb5c1c9b`, tag `pre_chatterbox_planner_continuity_refinement_20260714`. Its DB/settings backup and restore steps are in `backend/data/logs/CHATTERBOX_PLANNER_REFINEMENT_ROLLBACK.md`.

The custom-voice/player checkpoint is Git commit `61caed8b306e1f9957db5202a7344f96637f15a7`, tag `pre_qwen_custom_voice_gap_player_refinement_20260714`. Its exact database/settings backup and restore steps are in `backend/data/logs/QWEN_CUSTOM_VOICE_PLAYER_ROLLBACK.md`.

The automatic-breath checkpoint is Git commit `c4e2c91c61befb3716e1a299e5992216cefcce86`, tag `pre_automatic_breath_performance_20260714`. Its database/settings backup is `backend/data/backups/pre_automatic_breath_performance_20260714`; implementation evidence and rollback details are in `backend/data/logs/AUTOMATIC_BREATH_PERFORMANCE_REPORT.md`.

The native-desktop master checkpoint is Git commit `038110c5225ef55e826e34375ceee0e3a605d834`, annotated tag `pre_native_desktop_packaging_revamp_20260819`. Its master backup and exact restoration procedure are in `backend/data/backups/native_desktop_packaging_20260819/master` and `backend/data/logs/NATIVE_DESKTOP_PACKAGING_ROLLBACK.md`.

## Verification Baseline

Native/package verification on 2026-08-20:

- Installed window visible in 1.134 seconds; backend ready in 3.347 seconds. Portable window/backend were 2.238/5.565 seconds.
- Final ready-process snapshot: WPF shell 187.9 MB, six WebView2 processes 523.9 MB combined, frozen backend 104.5 MB, and Kokoro parent/worker/hidden conhost 1,385.3 MB combined. Total process working sets were 2,201.6 MB; shared pages make this a conservative sum rather than unique physical RAM.
- Built-in llama.cpp `b10507` Vulkan loaded an existing 16,861,398,400-byte GGUF in 8.488 seconds, generated the exact test string in 11.446 seconds, and unloaded cleanly.
- Generic local OpenAI-compatible discovery/generation and LM Studio compatibility contracts passed. No provider switched or reconfigured LM Studio.
- Installer, disposable upgrade, preserve-data uninstall, explicit disposable remove-data uninstall, and portable launch passed. Existing database semantics and private data were preserved.
- Installed desktop and private-LAN app/API/PWA served together on port 8001. Automated 430x932 mobile reload/overflow checks passed; physical iPhone background audio remains manual.
- Kokoro direct synthesis completed in 1.154 seconds. Authorized Arabella/Qwen synthesis returned 5.12 seconds of audio with no fallback; the first cold request took 47.014 seconds including worker/model startup.
- Final live writing-length contract preserved all same-scene identities and cleaned every disposable story, but focused/long deliberate operations took 197-381 seconds on the currently loaded model. The custom target exceeded its requested maximum repeatedly; a deterministic over-length gate and stronger one-repair instruction were added and statically verified. Do not claim the current runtime always meets the 1-2 minute ideal.
- Installer size is 103,116,875 bytes; portable ZIP is 153,277,549 bytes; unpacked core is 389,025,347 bytes across 610 files.

Historical generation/TTS baselines below remain useful comparisons from the earlier model/runtime configuration; they are not current native-package timing claims.

- Post-alias continuity gauntlet: 20 scenes plus Rewrite/Regenerate/Revise, 23/23 state runs, accepted-version semantics correct, 50.917-second mean, 72.151-second maximum, four targeted repairs, and bounded writer context averaging 28,771 characters.
- Planner: isolated mean improved from 31.984 to 5.744 seconds (82.0%); the growing 20-scene run averaged 14.671 seconds and never pushed total generation over 120 seconds.
- Reviewer: 18/18 injected major failures caught, zero major false positives across five acceptable controls, seven semantic model reviews, and zero JSON repairs.
- Memory retrieval: 12.63 ms in the post-alias gauntlet; projected 1,000-scene growth is about 105.68 MB decimal.
- Length contracts: Beat 535 words, Scene 1,119, Chapter 2,198, Custom 1,310; rewrite/revise/regenerate retained the selected same-scene target.
- Title diversity: 50/50 unique deterministic titles plus nine live titles; no repeated first words and maximum pair similarity 0.333.
- Kokoro: 21/21 chunks, first audio 3.16 seconds in the long gauntlet, exact cache reuse, breath-aware actual-duration seek, and final mobile page-refresh progression from a saved 10.0-second cursor to 13.25 seconds.
- Qwen3-TTS built-in automatic-breath regression: 360.48/721.04 seconds, 84/169 chunks, RTF 0.8526/0.8680, first audio 32.381/37.880 seconds, 12/18 ms maximum transition, and zero underruns.
- Qwen3-TTS custom voice: 720.08 seconds, 215 chunks, 214 transitions, RTF 0.9269, first audio 31.26 seconds, 25 ms maximum loaded transition, zero underruns, 147 automatic style switches, and no cache-identity conflict.
- Qwen startup: backend first audio 23.974 seconds cold, 16.736 seconds warm, and 0.064 seconds exact-cache; cold worker health readiness was 3.415 seconds and Base model load was 0.656 seconds.
- Frontend build: initial bundle is 470.78 KB (139.19 KB gzip); lazy Settings is 41.20 KB (11.82 KB gzip); lazy Model Settings is 54.19 KB (13.43 KB gzip).
- Mobile browser: 430x932, 390x844, 844x390, and 1440x1000 pass without horizontal overflow; hidden breathing controls, narration, private diagnostics, breath-aware seeking, and refresh-resume contracts pass.
- Final data state: four preserved real stories, SQLite integrity `ok`, zero foreign-key violations, no unfinished title/narration/state/deletion jobs, no orphan generated files, and no Qwen worker.
- Product-polish quality run: 10 connected scenes, three same-scene versions, eight targeted genre cases, bounded 4,575-character relevant memory, no duplicate holders/locations, and 21/21 Kokoro chunks with 3.371-second first audio and cache reuse.
- Product-polish generation: connected scenes averaged 51.46 seconds total; the longest targeted long chapter completed in 93.19 seconds. Rewrite/Regenerate/Revise averaged 57.22 seconds and retained one scene identity.
- Product-polish frontend: initial JS 485.40 KB, CSS 100.79 KB, zero hot whole-store subscriptions in the audited surfaces, one background timer, and visibility-aware 30-second health refresh.
- Product-polish mobile: 430x932, 390x844, 844x390, 1024x768, 1440x1000, and 1920x1080 passed with no horizontal overflow. Browser pinch zoom remains allowed and mobile form inputs remain 16 px.

Run targeted static tests, backend compile, frontend build, live service smoke checks, SQLite integrity/FK checks, privacy scan, and cleanup dry-run after meaningful changes. Runtime work requires a real end-to-end test; a compile alone is not success.

## Current Human-Only Checks

- Score the blind pack in `D:\StoryDriver\tts_engines\samples\chatterbox_comparison` and use Settings -> Narration -> Compare voices for current Kokoro/Qwen samples; no automated subjective winner is claimed.
- Record a clean 10-20 second, single-speaker, user-owned or authorized female reference and judge the resulting long-form narrator manually. Automated tests cannot decide identity fit or listening comfort.
- Record isolated authorized breath references only if desired, then judge sample matching, loudness, placement, and repeated-listening comfort in Natural mode before considering Cinematic.
- Test physical iPhone LAN playback, backgrounding, refresh resume, and user-gesture recovery.
- Judge prose subjectively across personal long-form stories; automated continuity checks cannot prove taste.
- Image generation requires a separate future decision, implementation, and acceptance project.
