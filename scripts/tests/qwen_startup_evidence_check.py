from __future__ import annotations

import argparse
import json
from pathlib import Path


EVIDENCE = Path(r"D:\StoryDriver\backend\data\logs\qwen_real_startup_profile_evidence.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=("first", "cache", "warm"))
    case = parser.parse_args().case
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    if case == "first":
        row = data["cold"]
        assert row["effective_provider"] == "high_quality_local"
        assert row["first_audio_backend_seconds"] <= 60
    elif case == "cache":
        row = data["exact_cache"]
        assert row["cached"] is True
        assert row["first_audio_backend_seconds"] <= 8
    else:
        row = data["warm_new"]
        assert row["first_audio_backend_seconds"] <= 60
    print(f"PASS: qwen_startup_evidence_{case}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
