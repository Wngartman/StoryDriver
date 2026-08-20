from pathlib import Path


root = Path(__file__).resolve().parents[1]
source = (root / "frontend/src/components/ModelSettingsModal.jsx").read_text(encoding="utf-8")
provider = (root / "backend/app/generation/model_provider.py").read_text(encoding="utf-8")
pipeline = (root / "backend/app/generation/pipeline.py").read_text(encoding="utf-8")
for needle in (
    'settingsView === "basic"',
    'settingsView === "advanced"',
    'title="Per-task model overrides"',
    'name="Temperature"',
    'name="Top P"',
    'name="Streaming"',
    'name="Seed"',
):
    if needle not in source:
        raise AssertionError(f"model settings UI contract missing {needle}")
if "Task Model ID" in source or "Legacy v2" in source or "Legacy Notes" in source:
    raise AssertionError("duplicate/legacy model controls remain visible")
for needle in ('body["top_k"]', 'body["min_p"]', 'body["repeat_penalty"]', 'body["seed"]'):
    if needle not in provider:
        raise AssertionError(f"provider runtime contract missing {needle}")
for needle in ('"presence_penalty"', '"frequency_penalty"', '"reasoning_mode"', '"context_length"'):
    if needle not in pipeline:
        raise AssertionError(f"pipeline runtime contract missing {needle}")
print("model settings UI/runtime source contract: PASS")
