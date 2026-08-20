# TTS Listening Scorecard

Benchmark date: 2026-06-23

Use headphones or the same speakers used for StoryDriver narration. Score 1 to 10, where 10 is best. Leave a row blank if the sample is missing or known invalid.

## Sample Paths

| Provider | Neutral | Dialogue | Emotional | Action | Names | Long excerpt |
|---|---|---|---|---|---|---|
| Kokoro baseline | `D:\StoryDriver\tts_engines\samples\kokoro_neutral.wav` | `D:\StoryDriver\tts_engines\samples\kokoro_dialogue.wav` | `D:\StoryDriver\tts_engines\samples\kokoro_emotional.wav` | `D:\StoryDriver\tts_engines\samples\kokoro_action.wav` | `D:\StoryDriver\tts_engines\samples\kokoro_names.wav` | `D:\StoryDriver\tts_engines\samples\kokoro_long_excerpt.wav` |
| Chatterbox English CPU | `D:\StoryDriver\tts_engines\samples\chatterbox_english_neutral.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_english_dialogue.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_english_emotional.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_english_action.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_english_names.wav` | not generated, too slow |
| Chatterbox Turbo CPU | `D:\StoryDriver\tts_engines\samples\chatterbox_turbo_neutral.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_turbo_dialogue.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_turbo_emotional.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_turbo_action.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_turbo_names.wav` | `D:\StoryDriver\tts_engines\samples\chatterbox_turbo_long_excerpt.wav` is invalid, direct long-form collapsed |

## Score Table

| Provider | Realism | Pleasantness over 10 minutes | Female voice quality | Emotion | Dialogue | Pronunciation | Artifacts | Speed feel | Overall preference | Notes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Kokoro baseline |  |  |  |  |  |  |  |  |  |  |
| Chatterbox English CPU |  |  |  |  |  |  |  |  |  | No direct long sample; score short-form quality only. |
| Chatterbox Turbo CPU |  |  |  |  |  |  |  |  |  | Treat direct long sample as failed; judge short/chunk samples only. |

## Pass/Fail Thresholds

| Question | Kokoro | Chatterbox English CPU | Chatterbox Turbo CPU |
|---|---|---|---|
| Noticeably more realistic than Kokoro? | baseline | manual listening needed | manual listening needed |
| Stable long-form playback? | yes | not demonstrated | direct long failed; chunked run completed |
| First audio hideable by progressive chunking? | yes | no | weak; 65-word chunks took about 16-33 seconds each |
| Maintains voice over many chunks? | manual listening needed | not tested | manual listening needed |
| Acceptable resource conflict with LM Studio? | current production baseline | high CPU/RAM cost | high CPU/RAM cost |
| Maintainable local Windows deployment? | yes | no, watermarker load blocker | no, watermarker load blocker |
