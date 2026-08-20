# TTS Provider Benchmark Report

Date: 2026-06-23
Workspace: `D:\StoryDriver`

## Result

No candidate should replace Kokoro right now.

Kokoro remains the recommended production/fallback provider. Chatterbox Turbo is the closest quality candidate to revisit, but this benchmark found practical blockers on this Windows AMD workstation:

- Official Chatterbox load path hits a Windows watermarker issue in `resemble-perth`; benchmarking required a dummy watermarker monkeypatch that is not acceptable for integration.
- Installed Torch is CPU-only (`torch 2.6.0+cpu`, `cuda_available=False`), so the AMD RX 7900 XTX was not used.
- Chatterbox Turbo short-form CPU RTF was about 1.39 and each 65-word chunk took roughly 16-33 seconds before first audio.
- Chatterbox Turbo direct long-form generation failed by returning 0.16 seconds of audio for 1670 words.
- Chatterbox English CPU RTF was about 2.70, too slow for long-form progressive narration.

No StoryDriver active TTS provider was changed. Kokoro remains running and healthy.

## 2026-07-13 Official-Source Refresh And Integration Gate

The official repositories were reviewed again during the complete revamp. The practical result did not change:

- Chatterbox now presents Multilingual V3 (500M) and Turbo (350M), but the official Turbo example remains CUDA-oriented and the documented tested environment is Linux/Python 3.11. The previous Windows CPU failures remain disqualifying on this machine.
- CosyVoice advertises streaming and voice cloning, but its practical deployment path remains centered on Linux, NVIDIA, TensorRT, and vLLM tooling.
- F5-TTS still documents AMD ROCm for Linux rather than native Windows. Its pretrained model license is non-commercial even though the code is MIT.
- Fish Speech S2 Pro is a 4B quality-ceiling model with CUDA-oriented deployment and a research license, not a maintainable Windows AMD StoryDriver provider.

No unsupported CUDA/NVIDIA package was installed, no StoryDriver/Kokoro environment was modified, and no premium provider was integrated. Kokoro remains the effective primary provider; `high_quality_local` is explicitly unavailable with Kokoro as its fallback.

The final StoryDriver live acceptance synthesized 21/21 progressive Kokoro chunks with `af_aoede`: first audio 2.032 seconds, median chunk 2.031 seconds, maximum chunk 2.062 seconds, exact URL cache reuse, and 44.641 seconds total synthesis time for the test pack.

## Sources Checked

Official repositories were used as the primary sources. Temporary local clones used by the original benchmark were removed after the selection decision was consolidated.

| Engine | Official source |
|---|---|
| Chatterbox | https://github.com/resemble-ai/chatterbox |
| CosyVoice | https://github.com/FunAudioLLM/CosyVoice |
| F5-TTS | https://github.com/SWivid/F5-TTS |
| Fish Speech / Fish Audio S2 Pro | https://github.com/fishaudio/fish-speech and https://speech.fish.audio/install/ |

## Safety Structure

Created directories:

- `D:\StoryDriver\tts_engines`
- `D:\StoryDriver\tts_engines\benchmarks`
- `D:\StoryDriver\tts_engines\samples`
- `D:\StoryDriver\tts_engines\reports`
- `D:\StoryDriver\tts_engines\cache`
- `D:\StoryDriver\tts_engines\envs`

Historical benchmark checkpoint (removed after consolidation):

- `D:\StoryDriver\tts_engines\benchmarks\checkpoint_20260623_174037`

The benchmark used isolated D-only paths and did not modify StoryDriver's backend venv, Kokoro environment, LM Studio configuration, or the active TTS provider.

## Compatibility Matrix

| Candidate | Feasibility gate | Installed? | Python | Windows | AMD/CPU | CUDA/NVIDIA assumption | Model size | Streaming/chunk support | Voice cloning | Server/API | License | Decision |
|---|---|---:|---|---|---|---|---|---|---|---|---|---|
| Chatterbox English | Partially feasible | yes | `>=3.10`; docs say tested on Python 3.11 Debian | Package installed on Windows Python 3.12 | CPU-only worked, slow | README examples prefer CUDA but CPU path exists | README describes original English as 500M | Offline generation only in tested path; app-level chunking possible | Optional audio prompt | Library, Gradio/FastAPI deps present | MIT code | Not production-ready on this machine |
| Chatterbox Turbo | Partially feasible | yes | `>=3.10`; same package | Package installed on Windows Python 3.12 | CPU-only worked, too slow for first audio | README positions Turbo for CUDA low latency | README describes Turbo as 350M | 24 chunks completed, but no true first-audio streaming | Optional audio prompt; none supplied | Library | MIT code | Closest candidate, but fails replacement bar |
| CosyVoice 3 / 2 | Fails practical gate | no | Docs recommend conda Python 3.10 | Windows path is not clean for this setup | CPU/ONNX pieces exist, but practical deployment is CUDA-oriented | requirements use CUDA indexes; runtime examples use NVIDIA Docker/TensorRT | CosyVoice3/2 0.5B, older 300M variants | Official docs advertise streaming/bi-streaming | Zero-shot voice cloning supported | WebUI, gRPC, FastAPI examples | Apache-2.0 code | Do not install under current constraints |
| F5-TTS | Fails practical gate | no | README recommends Python 3.11, badge Python 3.10 | Possible but not clean with current Python 3.12-only local tooling | CPU possible but expected slow; ROCm guidance is Linux-only | CUDA examples; ROCm wheels Linux-only; dependency set includes `bitsandbytes` | Base model, exact active model depends on download | Chunk inference and socket streaming mentioned | Requires `ref_audio` and `ref_text`; no authorized female ref supplied | CLI/Gradio/socket examples | MIT code; pretrained models CC-BY-NC | Do not install yet |
| Fish Audio S2 Pro | Fails practical gate | no | `>=3.10`, docs use Python 3.12 | Docs say compile unsupported on Windows/macOS | CPU extra exists, but 4B model is impractical for this target | SGLang/vLLM path emphasizes CUDA and NVIDIA H200 | 4B | Production streaming via SGLang/vLLM | Supports advanced voice/prosody features | API/WebUI/SGLang/vLLM paths | Fish Audio Research License; commercial use needs separate license | Optional quality ceiling only, not a StoryDriver replacement |

## Benchmark Method

Synthetic corpus only. No private story text was used.

Corpus categories:

- Neutral narration
- Emotional quiet scene
- Dialogue with speaker attributions
- Action scene
- Modern prose
- Fantasy prose
- Difficult names, numbers, and punctuation
- 1670-word long-form excerpt, about 9-12 minutes depending on speech rate

No user-authorized female reference clip was supplied, so no real-person voice cloning was performed. Chatterbox Turbo used its built-in/default conditioning.

## Measured Metrics

### Kokoro Baseline

Voice: `af_heart`
API: `http://127.0.0.1:8880/v1/audio/speech`

| Metric | Result |
|---|---:|
| Short sample first audio | 1.047-1.313 sec |
| Short average RTF | 0.0990 |
| Long excerpt words | 1670 |
| Long excerpt audio duration | 551.146 sec |
| Long excerpt synthesis time | 51.509 sec |
| Long excerpt RTF | 0.0935 |
| 24 chunk test | 24/24 completed |
| Chunk total audio | 473.104 sec |
| Chunk average RTF | 0.0973 |
| Cached chunk resume | yes |
| Process private memory after chunk run | about 4.16 GB |
| Mobile playback compatibility | Existing StoryDriver path works |

### Chatterbox English CPU

Environment: `D:\StoryDriver\tts_engines\envs\chatterbox`
Package: `chatterbox-tts==0.1.7`
Torch: `2.6.0+cpu`

Official load without bypass failed:

- `D:\StoryDriver\tts_engines\benchmarks\chatterbox_english_load_traceback.txt`
- Root: `perth.PerthImplicitWatermarker()` was `None` on this Windows install.

Benchmark load used a dummy watermarker monkeypatch for measurement only.

| Sample | Words | Audio sec | Elapsed sec | First audio sec | RTF |
|---|---:|---:|---:|---:|---:|
| Neutral | 35 | 8.840 | 23.759 | 23.757 | 2.6877 |
| Emotional | 37 | 8.680 | 23.456 | 23.437 | 2.7023 |
| Dialogue | 33 | 9.320 | 24.686 | 24.670 | 2.6487 |
| Action | 28 | 7.000 | 19.411 | 19.388 | 2.7730 |
| Names | 30 | 11.760 | 31.734 | 31.621 | 2.6985 |

Summary:

- Model load: 4.969 sec after models were present.
- Average short RTF: 2.7020.
- Direct long excerpt was skipped because a 10-minute equivalent would take about 27 minutes on CPU with no true progressive first-audio path.
- 20+ chunk stability was skipped.
- Process private memory reached about 6.55 GB.
- Output WAVs are IEEE float WAV; integration would need PCM16/Opus conversion for reliable mobile playback.

### Chatterbox Turbo CPU

Environment: `D:\StoryDriver\tts_engines\envs\chatterbox`
Package: `chatterbox-tts==0.1.7`
Torch: `2.6.0+cpu`

Benchmark load used the same dummy watermarker monkeypatch for measurement only.

| Sample | Words | Audio sec | Elapsed sec | First audio sec | RTF |
|---|---:|---:|---:|---:|---:|
| Neutral | 35 | 11.480 | 16.112 | 16.106 | 1.4035 |
| Emotional | 37 | 11.120 | 15.497 | 15.495 | 1.3936 |
| Dialogue | 33 | 13.080 | 18.145 | 18.142 | 1.3872 |
| Action | 28 | 9.000 | 12.495 | 12.489 | 1.3884 |
| Names | 30 | 13.880 | 19.261 | 19.255 | 1.3877 |

Summary:

- Model load: 46.424 sec on first Turbo run.
- Average short RTF: 1.3921.
- Direct long excerpt failed: 1670 words returned only 0.160 seconds of audio in 2.176 seconds.
- Chunk test completed 24/24 chunks.
- Chunk total audio: 461.160 sec.
- Chunk average RTF: 1.3906.
- Cached chunk resume: yes, because chunk WAVs were saved.
- Chunk first-audio latency was still full chunk latency, roughly 16-33 seconds for 40-65 word chunks.
- Process private memory reached about 7.19 GB after the chunk run.
- Output WAVs are IEEE float WAV; integration would need PCM16/Opus conversion for reliable mobile playback.

## Samples Generated

| Provider | Files |
|---|---|
| Kokoro | `D:\StoryDriver\tts_engines\samples\kokoro_neutral.wav`, `kokoro_dialogue.wav`, `kokoro_emotional.wav`, `kokoro_action.wav`, `kokoro_names.wav`, `kokoro_long_excerpt.wav`, plus `kokoro_chunks\` |
| Chatterbox English | `D:\StoryDriver\tts_engines\samples\chatterbox_english_neutral.wav`, `chatterbox_english_dialogue.wav`, `chatterbox_english_emotional.wav`, `chatterbox_english_action.wav`, `chatterbox_english_names.wav` |
| Chatterbox Turbo | `D:\StoryDriver\tts_engines\samples\chatterbox_turbo_neutral.wav`, `chatterbox_turbo_dialogue.wav`, `chatterbox_turbo_emotional.wav`, `chatterbox_turbo_action.wav`, `chatterbox_turbo_names.wav`, `chatterbox_turbo_long_excerpt.wav`, plus `chatterbox_turbo_chunks\` |

Listening scorecard:

- `D:\StoryDriver\tts_engines\reports\LISTENING_SCORECARD.md`

The direct Chatterbox Turbo long excerpt is intentionally retained as failure evidence, not as a valid listening sample.

## Selection Thresholds

| Threshold | Kokoro | Chatterbox English | Chatterbox Turbo | CosyVoice | F5-TTS | Fish S2 Pro |
|---|---|---|---|---|---|---|
| Noticeably more realistic | baseline | manual listening needed | manual listening needed | not tested | not tested | not tested |
| Stable | yes | not enough | chunked only; direct long failed | not installed | not installed | not installed |
| First audio hideable by progressive chunking | yes | no | weak on CPU | unknown | unknown | unknown |
| Long-form playback works | yes | no | only chunked, not direct | not tested | not tested | not tested |
| No unacceptable LM Studio conflict | yes | questionable CPU/RAM | questionable CPU/RAM | unknown | unknown | likely no |
| Maintainable local Windows deployment | yes | no due watermarker issue | no due watermarker issue | no under current constraints | no under current constraints | no under current constraints |
| Licensing acceptable | yes in current setup | MIT code | MIT code | Apache-2.0 code | pretrained models CC-BY-NC | research/non-commercial unless separately licensed |

## Recommendation

Do not integrate or replace Kokoro.

Current recommended provider:

- Production/fallback: Kokoro.
- Research candidate to revisit: Chatterbox Turbo, only if the official Windows watermarker issue is resolved or a reviewed local patch is acceptable, and only after manual listening confirms a substantial realism improvement.

Exact integration recommendation if revisiting Chatterbox later:

1. Require a user-authorized female reference clip before voice cloning tests.
2. Keep Chatterbox in a separate service and isolated D-only venv.
3. Use CPU only unless a supported Windows AMD acceleration path exists.
4. Test much smaller chunks, likely 8-20 words, then measure whether first-audio latency becomes acceptable without audible discontinuities.
5. Transcode generated WAV to PCM16 WAV or Opus before browser/mobile playback.
6. Add a hard watchdog for truncation, empty outputs, repeated outputs, and per-chunk timeout.
7. Keep Kokoro as fallback until Chatterbox passes a long-form, 20+ chunk, manual listening, and mobile playback test.

## Disk Usage And Retained Files

The original benchmark footprint was 9.297 GB. The revamp removed the failed Chatterbox venv, model/package caches, source clones, raw traces/metrics, and failed-candidate audio after proving they were not used by StoryDriver.

Post-cleanup footprint on 2026-07-13:

| Path | Files | Bytes |
|---|---:|---:|
| `D:\StoryDriver\tts_engines` | 12 | about 29.27 MB |
| `envs` | 1 `.gitkeep` | 1 |
| `cache` | 1 `.gitkeep` | 1 |
| `benchmarks` | 1 `.gitkeep` | 1 |
| `samples` | 6 WAV files + `.gitkeep` | 29,249,327 |
| `reports` | 2 | about 17 KB before this update |

Retained evidence:

- `TTS_PROVIDER_BENCHMARK_REPORT.md`
- `LISTENING_SCORECARD.md`
- six clearly named synthetic Kokoro listening WAV files

No isolated candidate environment remains installed. Future candidates must recreate a D-only isolated environment and cache, and must not modify Kokoro or the StoryDriver backend venv.

## Final Service Check

`D:\StoryDriver\scripts\check_services.bat` after benchmark:

- StoryDriver Backend: OK
- StoryDriver Frontend: OK
- Kokoro: OK
- LM Studio OpenAI: OK
- LM Studio REST: OK
- ComfyUI: paused/offline as expected
