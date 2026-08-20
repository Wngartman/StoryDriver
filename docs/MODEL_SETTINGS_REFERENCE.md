# Model Settings Reference

## Effective Instruction Order

Normal generation uses user-authored system instructions, editable task notes, Story Foundation, bounded Memory v3 and Story State, the structured Scene Contract/blocking/agency plan, the director note, prose generation, compact review, and at most one targeted major-failure repair. The user prompt is creative authority; structured context supplies facts and constraints, not a hidden style persona.

## Writing View

- Preset and selected LM Studio model.
- User system prompt and editable task notes.
- Beat, Scene, Chapter, or Custom writing length.
- Temperature, Top P, maximum output tokens, streaming, seed/random seed, and repetition penalty when supported.
- A concise definitive-pipeline status. Planning, review, Story State extraction, and the one-repair limit cannot be accidentally disabled.

## Advanced View

Advanced contains supported Top K, Min P, frequency/presence penalties, context budgets, task selection, task model override, task notes, and effective route. Per-task overrides cover foundation, planning, prose, same-scene versions, review, state extraction, summaries, titles, and diagnostics. `Use global model for every task` and per-row Reset are the only routing resets.

Unsupported or obsolete controls are not shown. Image settings, duplicate Model ID/Task Model ID fields, legacy direct/fast quality toggles, duplicate reset shortcuts, and label-only reasoning controls are absent. Refresh Models discovers the actual local LM Studio inventory.

## Persistence And Runtime

Settings save to the local backend and apply to the next relevant request. The contract suite changes each retained category, reloads it, checks the runtime payload or endpoint behavior, and restores the exact snapshot. External model/TTS URLs are rejected by default. Presets do not silently include UI, theme, or TTS settings.

## Help

The central explanation registry describes effect, safe range, support, and likely quality/latency/determinism tradeoffs. Help opens after a 750 ms hover, immediately on keyboard focus, and by touch/click. Escape, outside interaction, or scroll closes it.

## Recommended Values

The selected model is `gemma4-26b-a4b-uncensored-hauhaucs-balanced`; keep the mandatory deliberate path and current balanced task profiles. Change one sampling or context variable at a time and use disposable stories before adopting a new default. See `CURRENT_RECOMMENDED_SETTINGS.md` for current values.
