from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation.pipeline import (  # noqa: E402
    AGENCY_MATRIX_KEYS,
    BLOCKING_MAP_KEYS,
    PLAN_KEYS,
    SCENE_CONTRACT_KEYS,
    deterministic_quality_review,
    deterministic_scene_plan,
)


PROFILE = ROOT / "backend" / "data" / "logs" / "planner_latency_profile_structured.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    notes = (
        "First chapter introductions only in a modern apartment; do not begin the future investigation or leave.",
        "Medieval no-magic planning in the hall; keep the battle in the future and track the map and key.",
        "Three-adult relationship argument with different goals, close viewpoint transitions, and no time skip.",
    )
    for note in notes:
        plan = deterministic_scene_plan(
            session_id="planner-quality-static",
            mode="continue",
            director_note=note,
            writing_length={"mode": "scene", "label": "Scene", "min_words": 800, "max_words": 1400},
            recent_scenes=[],
            session_summary=None,
            target_scene=None,
            world_notes={"rules": "No magic where specified; hard scene boundaries apply."},
            active_characters=[],
        )
        require(set(PLAN_KEYS).issubset(plan), "validated plan lost required top-level fields")
        require(set(SCENE_CONTRACT_KEYS).issubset(plan["scene_contract"]), "scene contract fields missing")
        require(set(BLOCKING_MAP_KEYS).issubset(plan["blocking_map"]), "blocking fields missing")
        require(plan["character_agency_matrix"], "agency matrix is empty")
        require(
            all(set(AGENCY_MATRIX_KEYS).issubset(row) for row in plan["character_agency_matrix"]),
            "agency row fields missing",
        )
        require(plan["scene_contract"]["time_skip_allowed"] is False, "time skip default regressed")
    closed_cast_note = (
        "Write a relationship-driven intimate scene between Elena, age thirty-four, and Noor, age thirty-six, "
        "established adult partners at home."
    )
    closed_cast_plan = deterministic_scene_plan(
        session_id="planner-quality-static",
        mode="continue",
        director_note=closed_cast_note,
        writing_length={"mode": "scene", "label": "Scene", "min_words": 0, "max_words": 1400},
        recent_scenes=[],
        session_summary=None,
        target_scene=None,
        world_notes=None,
        active_characters=[],
    )
    require(closed_cast_plan["scene_contract"]["cast_policy"] == "closed", "explicit two-person cast was not closed")
    closed_cast_review = deterministic_quality_review(
        scene_plan=closed_cast_plan,
        draft_text=(
            "Elena sat beside Noor and asked what she wanted. Noor answered without looking away. "
            "Ari Vale stood in the corner and watched them. Ari remained there while Elena reached for Noor's hand."
        ),
        writing_length={"min_words": 0},
    )
    require(closed_cast_review["severity"] == "major", "unexpected closed-cast character was not rejected")
    require(any("Ari" in issue for issue in closed_cast_review["issues"]), "unexpected character was not identified")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    require(float(profile["mean_seconds"]) < 15.0, "planner mean exceeded 15 seconds")
    require(profile["fallback_count"] == 0, "live planner profile unexpectedly used fallback")
    require(profile["json_repair_count"] == 0, "live planner profile unexpectedly used JSON repair")
    print(f"[OK] planner quality regression passed; mean={profile['mean_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
