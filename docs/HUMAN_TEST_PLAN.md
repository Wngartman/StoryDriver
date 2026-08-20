# Human Test Plan

Automated revamp acceptance is complete. These checks require human perception or a physical device and must not be represented as automated passes.

## Kokoro Listening

Listen with normal headphones or speakers to the retained files in `D:\StoryDriver\tts_engines\samples`:

- `kokoro_neutral.wav`
- `kokoro_dialogue.wav`
- `kokoro_emotional.wav`
- `kokoro_action.wav`
- `kokoro_names.wav`
- `kokoro_long_excerpt.wav`

Score realism, female voice quality, long-session pleasantness, emotion, dialogue, pronunciation, pauses/prosody, artifacts, and overall preference using `tts_engines\reports\LISTENING_SCORECARD.md`.

Pay special attention to the long excerpt, quote/attribution joins, names, sentence-end pauses, and whether the active `af_aoede` profile remains pleasant for 10 minutes.

## Physical iPhone LAN

On an iPhone connected to the same private network:

1. Open `http://PC_PRIVATE_IP:5173`.
2. Confirm the story drawer and settings drawer open and close.
3. Confirm the compact composer remains visible above the safe area and text inputs do not zoom.
4. Generate a disposable scene and verify visible streaming.
5. Start narration and exercise play, pause, resume, skip, and speed.
6. Refresh mid-chunk and confirm the saved cursor can resume.
7. Background Safari, return, and recover playback with one user gesture if iOS suspends audio.
8. Interrupt/restart Kokoro and confirm the reading position is retained.
9. Verify no horizontal overflow at portrait width.

Delete the disposable story and confirm its generated audio is removed.

## Optional Preference Check

Confirm whether the current appearance preset should remain the default. Images stay paused; rebuilding image generation requires a separate explicit project and acceptance pass.
