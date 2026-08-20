import json
from pathlib import Path
import sys


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))

from app.memory.engine import normalize_object_identity  # noqa: E402

for raw, expected in (
    ("brass key was a cold", "brass key"),
    ("key gripped firmly in her hand", "key"),
    ("the black rent ledger", "black rent ledger"),
):
    if normalize_object_identity(raw) != expected:
        raise AssertionError(f"object identity cleanup failed for {raw!r}")
metrics_path = root / "backend/data/logs/LONG_STORY_CONTINUITY_GAUNTLET_LATEST.json"
if not metrics_path.exists():
    raise AssertionError("run product_quality_use_test before inventory_memory_accuracy_test")
metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
canonical = metrics.get("canonical") or {}
for key in (
    "duplicate_character_locations",
    "duplicate_object_holders",
    "generic_relationships",
    "invalid_positions",
    "malformed_objects",
):
    if canonical.get(key):
        raise AssertionError(f"continuity check {key} failed: {canonical[key]}")
if canonical.get("key_alias_count") != 1:
    raise AssertionError("brass key was split into duplicate aliases")
if not canonical.get("brass_key_owner_preserved") or not canonical.get("brass_key_holder_preserved"):
    raise AssertionError("owner/holder transfer did not end in the expected canonical state")
memory = metrics.get("memory") or {}
if not memory.get("relevant_old_memory_present") or not memory.get("bounded"):
    raise AssertionError(f"bounded old-memory retrieval failed: {memory}")
if float(memory.get("relevant_ms") or 9999) >= 100:
    raise AssertionError(f"memory retrieval exceeded 100 ms: {memory}")
if not all(item.get("status") in {"completed", "skipped"} for item in metrics.get("state_runs") or []):
    raise AssertionError("one or more Story State runs failed")
if not metrics.get("version_scene_count_stable") or not all(item.get("same_scene") for item in metrics.get("version_generations") or []):
    raise AssertionError("rewrite/revise/regenerate changed the scene count")
print("inventory and memory accuracy contract: PASS")
