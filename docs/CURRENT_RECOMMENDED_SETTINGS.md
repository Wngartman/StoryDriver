# Current Recommended Settings

Verified: 2026-08-20

StoryDriver has one normal writing behavior. The priority is the best balanced quality on every operation, not a collection of quality modes. One to two minutes remains the ideal for focused work when the selected model/runtime supports it; the latest current-model measurements are slower and are documented below.

## Writing Defaults

| Setting | Effective value |
|---|---|
| Provider/model | LM Studio compatibility / `qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max` |
| Prose prompt | `standard` / Prose v3 |
| Process | `deliberate` |
| Path | `deliberate_pipeline` |
| App planning | enabled and mandatory |
| Streaming | enabled |
| Review | compact mandatory review |
| Repair | at most one, major failures only |
| Images | unavailable in active product |

These four effective settings are canonical and cannot be disabled by an old preset: `prose_prompt_mode=standard`, `writing_process_mode=deliberate`, `writing_path=deliberate_pipeline`, and `app_planning_enabled=true`.

## Pipeline Behavior

Every writing operation keeps:

- Story Foundation and user-authored system/task prompts;
- bounded Memory / Character / Place v3 retrieval;
- Scene Contract, blocking map, character agency, viewpoint, and world anchors;
- Story State continuity;
- compact anti-fixation and scope review;
- one targeted repair ceiling;
- post-save extraction and rolling summaries.

Do not trade these away to chase raw tokens per second. Measure LM throughput separately from full scene latency.

## Local Model Provider

The current migrated selection remains on LM Studio compatibility. LM Studio is optional: built-in llama.cpp/Vulkan can load compatible local GGUF files directly, and a generic local OpenAI-compatible endpoint can be selected. StoryDriver reports provider capabilities but does not silently change an external provider's loaded model, template, context, GPU offload, or reasoning behavior.

The older `gemma4-26b-a4b-uncensored-hauhaucs-balanced` preset reference is preserved, but that model/file was absent during the final migration audit. Do not select a substitute and call it Gemma. If the exact GGUF is restored later, add its existing path to the Model Library without copying it, verify its template, and run the writing gauntlet before switching.

Global/task sampling currently resolves to temperature `1.0`, top-p `0.95`, top-k `40`, min-p `0.05`, repeat penalty `1.1`, and up to `8000` output tokens. These remain editable because creative preferences and model revisions can justify changes. Preserve a settings snapshot before bulk edits.

Use Settings -> Models to refresh and select models. Keep planning/prose/review on the main selected route unless a per-task change is deliberately benchmarked across the full gauntlet.

## Writing Lengths

| Mode | Target | Final measured sample |
|---|---:|---:|
| Beat | 300-700 words | 698 words |
| Scene | 800-1,400 words | 1,603 words |
| Chapter | 1,800-2,600 words | 2,667 words |
| Custom | user value within safe limits | 1,457 words for a 950-1,150 target before the final over-length repair fix |

Explicit short-moment, chapter-scale, or word-count instructions override the default within safe limits. Rewrite, Revise, and Regenerate retain the selected target while creating a same-scene version.

## Latency Expectations

The earlier 20-scene/23-version continuity gauntlet averaged 50.917 seconds and stayed below 120 seconds on its then-current model/runtime. The installed current-model length contract on 2026-08-20 measured:

- Beat: 99.469 seconds to first visible prose, 197.415 seconds total.
- Scene: 184.534 seconds to first visible prose, 380.976 seconds total.
- Chapter: 133.046 seconds to first visible prose, 373.887 seconds total.
- Custom: 205.880 seconds to first visible prose, 362.514 seconds total.
- Rewrite/Revise/Regenerate: 317.138-344.141 seconds total; all remained versions of one target scene.

These timings include planner/model reasoning, prose, review, and one repair when needed; they are not raw model t/s. The custom case repeatedly overshot its requested ceiling, so material over-length is now a deterministic major issue and the single repair receives both the measured count and binding final range. The fix passed static regression; a second full live length gauntlet was not run because the first consumed approximately 39 minutes. Do not weaken Scene Contract, continuity, or review to hide these measurements.

## TTS Defaults And Fallback

| Setting | Value |
|---|---|
| Selected provider | `Qwen3-TTS 0.6B` |
| Profile | authorized custom profile `Arabella` |
| Effective model | `Qwen3-TTS 0.6B Base` |
| Fast fallback | `Kokoro / Aoede` |
| Effective speed | `1.0` |
| Chunking | progressive natural chunks |
| Chunk target | natural paragraph/dialogue/sentence boundaries |
| Prebuffer | 2 chunks |
| Cache ceiling | 5,120 MB, access-refreshed LRU |
| Browser fallback | disabled |
| Follow/highlight | sentence |

The saved provider/profile and speed are user settings. Preserve them before testing and do not silently overwrite them.

Final long-story result: 21/21 chunks returned; first audio 3.16 seconds; median chunk synthesis 2.874 seconds; maximum 3.522 seconds; exact cache reuse passed. Retain pronunciation aliases, actual-duration rewind/forward, generated-only seeking, and canonical saved cursor behavior.

`Premium Female Narrator` maps to installed Qwen3-TTS 0.6B CustomVoice with built-in Serena. Authorized custom profiles use Qwen3-TTS 0.6B Base. The current measured backend first-audio times are 23.974 seconds cold, 16.736 seconds warm, and 0.064 seconds for an exact cache hit. Internally Qwen uses CPU bfloat16, SDPA, 4 intra-op/4 inter-op threads, a continuous ordered batch-four producer, per-chunk cache, and no prewarm. Qwen shuts down after generation and before writing; StoryDriver leaves the selected writing provider/model alone. Any Qwen generation failure falls back to Kokoro automatically and the player labels that fallback truthfully.

For an authorized custom narrator, add a clean 10-20 second single-speaker reference in Settings -> Narration -> Custom voices. WAV, FLAC, and MP3 are accepted and normalized locally; the transcript must match the spoken recording. The pinned Qwen3-TTS 0.6B Base model creates the reusable prompt. The latest acceptance run completed 720.08 seconds at RTF 0.9269, 215 chunks, 214 transitions, a 25 ms maximum gap, and zero underruns. Keep expression cues explicit and sparse. The built-in 0.6B voice reports normal delivery when its model cannot honor instruct-style expression.

Do not expose these engine parameters as routine user modes, run multiple Qwen workers, enable prewarm, or unload an external writing model for narration. Human listening remains the final decision on whether Serena is more pleasant than Kokoro for a particular story.

Do not install or select Chatterbox. Turbo, Original English, and Multilingual V3 all failed the local continuous gate; best Turbo RTF was `1.1414`. The rejected runtime/models were removed, while compact results and a blind listening pack remain for evidence. Use Settings -> Narration -> Compare voices for the supported Kokoro Aoede/Qwen Serena decision; identities remain hidden until ratings are complete, and selecting a winner is always explicit.

## UI And Images

- Keep the desktop sidebar at its default expanded state unless the 68 px rail is preferred; the choice persists. Use local title search rather than adding a remote or full-content search service.
- Default display: Comfortable reading width, serif prose at 18 px/1.8 line height, 1.25 paragraph spacing, Comfortable 16 px composer, 100% UI scale, mobile Follow global, and Subtle motion.
- Keep advanced model settings behind the Advanced tab and avoid adding normal writing modes. Per-task overrides remain an expert routing tool, not a second quality path.
- Workspace backgrounds are optional and off by default. Recommended starting values are 10% opacity, 35% dim, 85% saturation, Cover/Center/Fixed, 1.2-second fade, rotation Off, and pause while writing.
- Keep backgrounds under `backend/data/assets/backgrounds`; do not point the UI at external URLs or user folders outside StoryDriver.
- Keep image generation out of active startup, routes, settings, navigation, and generation. Dormant compatibility records are data-preservation only.
- Keep mobile form inputs at 16px or larger and verify 430x932 after UI changes.
- Keep Device diagnostics free of story/director text; only operational status and cursor coordinates may be copied.

## Services

```text
Installed app:  D:\StoryDriverApp\StoryDriver.exe
Production UI/API: http://localhost:8001
Built-in llama.cpp: owned loopback port, on demand
LM Studio compatibility: http://localhost:1234/v1
Kokoro: http://localhost:8880
Qwen: http://localhost:8891, on demand
Development Vite only: http://localhost:5173
```

Use `StoryDriver.exe`, the tray menu, and `StoryDriverCLI.exe status/doctor/backup/export/restore/cleanup`. Root batch launchers are removed. Port 8000 is not StoryDriver.

## Change Rule

Before changing defaults, snapshot model settings, task profiles/notes, user prompts, TTS settings, image settings, and UI preset. Benchmark with disposable stories, compare quality and latency, restore the snapshot if the change does not clearly improve the definitive path.
