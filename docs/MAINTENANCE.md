# Maintenance

Verified: 2026-08-20

## Routine Check

Use Settings -> Diagnostics or run `StoryDriverCLI.exe status` and `StoryDriverCLI.exe doctor`. Expected installed steady state is the WPF shell/WebView2, packaged backend 8001, and Kokoro 8880 after its asynchronous startup. Built-in llama.cpp and Qwen 8891 start only when requested. LM Studio or another local provider appears only when selected/started externally. Image/ComfyUI startup is removed. Port 8000 is an unrelated process and must not be stopped by StoryDriver tooling.

Backend health endpoints:

```text
http://localhost:8001/health
http://localhost:8001/diagnostics
http://localhost:8001/models
http://localhost:8001/tts/status
```

## Build And Static Checks

Use the project-local Python and Node runtimes. Do not install globally.

```bat
D:\StoryDriver\backend\.venv\Scripts\python.exe -m compileall -q D:\StoryDriver\backend\app
D:\StoryDriver\backend\.venv\Scripts\python.exe D:\StoryDriver\scripts\deliberate_pipeline_static_test.py
D:\StoryDriver\backend\.venv\Scripts\python.exe D:\StoryDriver\scripts\memory_character_place_v3_static_test.py
D:\StoryDriver\backend\.venv\Scripts\python.exe D:\StoryDriver\scripts\scene_contract_blocking_static_test.py
D:\StoryDriver\backend\.venv\Scripts\python.exe D:\StoryDriver\scripts\story_foundation_static_test.py
D:\StoryDriver\backend\.venv\Scripts\python.exe D:\StoryDriver\scripts\prose_v3_static_test.py
dotnet build D:\StoryDriver\apps\desktop\StoryDriver.Desktop.csproj -c Release
```

From `frontend`, run the project-local Vite build (`npm run build` when npm is available, or the bundled Node executable with `node_modules\vite\bin\vite.js build`).

For native release work, use the isolated `tools\packaging\.venv` and D:-only NSIS/runtime caches. Build with `scripts\build_native_release.py`, then test the extracted portable package and a disposable installer path before touching the final installation. Verify `release\SHA256SUMS.txt` after the last binary rebuild. Root batch launchers are obsolete; scripts under `scripts` are developer/test helpers only.

## Database Integrity

Stop StoryDriver before copying or compacting `backend\data\app.db`. Create one timestamped backup under `backend\data\backups`, record its SHA-256, and write one rollback note.

After migrations or destructive work, verify:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

Expected results are `ok` and zero foreign-key rows. Do not edit the live SQLite file with services running.

## Cleanup

Dry-run first:

```bat
D:\StoryDriver\backend\.venv\Scripts\python.exe D:\StoryDriver\scripts\storydriver_cleanup.py --dry-run --all-safe --logs --backups --temp --orphans
```

Review the proposed actions before adding `--apply`. The cleanup tool is limited to approved StoryDriver roots and protects the database, source tree, runtime folders, docs, TTS folder, and installed executables. Keep one current architecture document, one settings document, one consolidated task report, and one meaningful rollback checkpoint.

Do not retain raw prompt/prose logs, stale benchmark environments, generated screenshots, old broken venvs, duplicate wrappers, or redundant database backups.

The 2026-07-14 custom-voice cleanup dry-run found 64 orphaned `qwen_*.wav` test files totaling 23.6 MB. They were confirmed disposable and removed; 65 referenced generated files were preserved. Continue to review every orphan report before applying cleanup because real narration caches take precedence over automatic deletion.

## Permanent Creative Reset

Only after explicit authorization:

Quit StoryDriver from the tray first, make a verified backup, then run the explicit developer maintenance command `D:\StoryDriver\scripts\reset_storydriver_creative_data.bat --confirm-permanent-reset`.

The required confirmation flag is part of the safety boundary. The reset clears all creative tables and generated story media, verifies integrity and foreign keys, and compacts the database. Settings and required revamp records are preserved.

## Runtime Logs

Normal runtime logs must not contain story prose or complete prompts. Raw diagnostic story logging is opt-in through `STORYDRIVER_DEBUG_LOGS=true` and should be disabled again immediately after a scoped investigation.

`STORYDRIVER_ALLOW_REMOTE_ENDPOINTS=true` also weakens the default privacy boundary and must never be enabled casually.

## TTS Maintenance

Qwen3-TTS 0.6B is the current saved provider and Kokoro is the fast fallback. Retain the consolidated report, startup profile, pause root-cause report, rollback note, compact machine-readable evidence, and selected listening samples. Qwen's isolated environment, CustomVoice/Base models, service, and benchmark evidence stay under `tts_engines\qwen3_tts`; do not copy them into the backend or Kokoro environments. Do not prewarm Qwen.

Selecting Premium starts Qwen automatically. During generation, verify `/health`, `/status`, and `/metrics` on `127.0.0.1:8891` if diagnosis is needed. Normal writing must cancel and shut down Qwen first. After all chunks are generated, confirm no listener on 8891 and no lingering process whose command line contains the Qwen service. A cache-only replay may briefly start the lightweight service without loading the model.

Qwen regression scripts include `qwen_streaming_packet_test.bat`, `qwen_cpu_optimization_matrix.bat`, `qwen_batch_parallel_test.bat`, `qwen_6_minute_continuous_test.bat`, `qwen_12_minute_continuous_test.bat`, `qwen_custom_voice_prompt_test.bat`, `qwen_custom_voice_continuous_test.bat`, `custom_voice_upload_validation_test.bat`, `quotation_prosody_routing_test.bat`, `tts_gapless_1x_test.bat`, `tts_adaptive_buffer_test.bat`, and `tts_effective_voice_truth_test.bat`. The long matrix and playback tests are acceptance tools, not routine startup checks. They use synthetic text and isolated D:-only paths.

The six/twelve-minute scripts use a local playback server and record actual scheduled transitions. A normal embedded browser may still require a Start/Resume gesture; treat that as media-policy recovery, not synthesis failure. The retained six-minute result is 361.12 seconds, 84 chunks, 12 ms maximum gap, and zero underruns. The retained custom twelve-minute result is 720.08 seconds, 215 chunks, RTF 0.9269, 25 ms maximum gap, and zero underruns.

Story isolation/title/Qwen operational regressions are covered by `cross_story_isolation_test.bat`, `foundation_story_scope_test.bat`, `async_job_story_scope_test.bat`, `frontend_story_switch_race_test.bat`, `automatic_title_live_update_test.bat`, `title_manual_override_test.bat`, `tts_provider_product_name_test.bat`, `qwen_real_startup_profile_test.bat`, `qwen_first_story_narration_test.bat`, `qwen_startup_cache_test.bat`, `tts_ten_second_pause_regression_test.bat`, `tts_style_transition_gap_test.bat`, `tts_breath_transition_gap_test.bat`, `tts_cross_story_plan_isolation_test.bat`, both `qwen_1x_*_runtime_test.bat` wrappers, and `mobile_title_narration_regression_test.bat`.

Chatterbox is rejected, not installed. `chatterbox_install_verify.bat` validates retained evidence when the venv/models are intentionally absent. Keep `tts_engines\chatterbox\benchmarks`, its compact logs, and `tts_engines\samples\chatterbox_comparison`; do not restore the 12.06 GiB experiment unless a future official CPU release materially changes the gate.

Test actual progressive narration after TTS changes: 20+ chunks, cache reuse, pause/resume, actual-time cross-chunk rewind, generated-only seek, page reload, provider failure, pronunciation alias, and mobile user-gesture recovery.

## Writing Contracts

The operational contract tests are:

```bat
D:\StoryDriver\scripts\settings_contract_test.bat
D:\StoryDriver\scripts\writing_length_contract_test.bat
D:\StoryDriver\scripts\title_diversity_test.bat
D:\StoryDriver\scripts\relationship_location_accuracy_test.bat
D:\StoryDriver\scripts\long_story_continuity_gauntlet.bat
D:\StoryDriver\scripts\tts_buffered_seek_test.bat
D:\StoryDriver\scripts\mobile_streamline_regression_test.bat
D:\StoryDriver\scripts\offline_runtime_test.bat
```

These create disposable fixtures and must leave the real-story count unchanged. The relationship/location test uses a temporary D:-only database and closes/removes it at exit.

## Product-Polish Contracts

Run the 17 focused wrappers under `scripts` whose names begin with `sidebar_workspace_contract`, `story_search`, `display_scaling`, `theme_background_library`, `background_rotation`, `model_settings_runtime_contract`, `settings_tooltip_accessibility`, `invalid_character_extraction`, `wait_character_cleanup`, `branding_asset`, `motion_reduced_motion`, `easter_egg_safety`, `product_quality_use`, `inventory_memory_accuracy`, `mobile_product_polish`, `frontend_render_profile`, and `backend_query_profile`.

`product_quality_use_test.bat --reuse-latest` validates the retained full gauntlet without regenerating private or expensive fixtures. Rerun the complete generator only when the writing/state pipeline changes. The background folder may contain only validated PNG/JPG/WebP files plus its README and metadata index. Refreshing the library is local; missing files are dropped from selection safely.

After UI work, build the frontend and verify 430x932, 390x844, 844x390, 1024x768, 1440x1000, and 1920x1080. Check both sidebar states, the mobile drawer, touch help, Escape behavior, 16 px mobile inputs, browser zoom, and zero overflow. Keep `backend/data/voices` outside Git staging.

## Release Check

Before declaring runtime work complete:

1. Compile backend and run targeted tests.
2. Build frontend.
3. Restart StoryDriver through the ownership-aware launcher.
4. Exercise the changed path end to end.
5. Verify SQLite integrity/FKs and no unexpected DB/media growth.
6. Check `/diagnostics` for local-only endpoints.
7. Run desktop and 430x932 browser checks for UI work.
8. Leave services in a known usable state.
