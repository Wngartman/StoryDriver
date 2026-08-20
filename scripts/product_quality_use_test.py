from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "backend/.venv/Scripts/python.exe"
GAUNTLET = ROOT / "scripts/tests/long_story_continuity_gauntlet.py"
SOURCE_METRICS = ROOT / "backend/data/logs/LONG_STORY_CONTINUITY_GAUNTLET_LATEST.json"
SUMMARY_METRICS = ROOT / "backend/data/logs/product_polish/product_quality_use_metrics.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run StoryDriver's disposable qualitative product-use gauntlet.")
    parser.add_argument("--reuse-latest", action="store_true")
    args = parser.parse_args()
    if not args.reuse_latest:
        env = dict(os.environ)
        temp = str(ROOT / "backend/data/temp")
        env.update({"TMP": temp, "TEMP": temp})
        completed = subprocess.run(
            [str(PYTHON), str(GAUNTLET), "--real-scenes", "10"],
            cwd=ROOT,
            env=env,
            check=False,
        )
        if completed.returncode:
            return completed.returncode
    if not SOURCE_METRICS.exists():
        raise AssertionError("quality gauntlet metrics are missing")
    metrics = json.loads(SOURCE_METRICS.read_text(encoding="utf-8"))
    required_cases = {
        "contemporary_adventure_romance",
        "medieval",
        "science_fiction",
        "quiet_relationship",
        "mystery",
        "horror",
        "adult_fiction",
        "long_chapter",
    }
    missing = sorted(required_cases - set(metrics.get("targeted") or {}))
    if missing:
        raise AssertionError(f"quality gauntlet missed cases: {missing}")
    if not metrics.get("passed"):
        raise AssertionError(f"quality gauntlet failed: {metrics.get('errors')}")
    if metrics.get("scene_count") != 10 or not metrics.get("version_scene_count_stable"):
        raise AssertionError("connected-story or same-scene version contract failed")
    generations = metrics.get("main_generations") or []
    total_times = [float(item.get("total_seconds") or 0) for item in generations]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": True,
        "connected_scenes": metrics.get("scene_count"),
        "same_scene_versions": len(metrics.get("version_generations") or []),
        "targeted_cases": sorted((metrics.get("targeted") or {}).keys()),
        "settings_restored": metrics.get("settings_restored"),
        "stories_deleted": all(item.get("status") == "completed" for item in metrics.get("deletions") or []),
        "state_runs": metrics.get("state_runs"),
        "latency_seconds": {
            "minimum": min(total_times) if total_times else None,
            "maximum": max(total_times) if total_times else None,
            "average": round(sum(total_times) / len(total_times), 3) if total_times else None,
        },
        "tts": metrics.get("tts"),
        "storage": metrics.get("storage"),
        "errors": [],
    }
    SUMMARY_METRICS.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_METRICS.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("product quality use contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
