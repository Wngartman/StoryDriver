from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import db_session, init_db  # noqa: E402
from app.generation.pipeline import run_scene_planning_pass  # noqa: E402
from app.generation.router import resolve_task_model_settings  # noqa: E402


CASES = (
    (
        "introduction_scope",
        "First chapter in a present-day apartment. Introduce adult friends Elena, Priya, and Mara through conversation and small practical actions. Keep the entire scene in the kitchen for twenty minutes. The future police investigation is backstory only; do not begin it, leave the apartment, or skip ahead.",
    ),
    (
        "blocking_objects",
        "Continue a tactical basement scene without a time skip. June holds Mara's brass key beside the locked boiler panel; Mara kneels near the tool chest; Ellis waits at the stairs with a bandaged palm; Rowan blocks the laundry door on an injured ankle. Plan clear movement and object ownership but do not leave the basement.",
    ),
    (
        "relationship_agency",
        "Write a slow relationship argument among adult partners Noor, Elena, and Celia in the living room. Noor wants immediate honesty, Elena wants privacy, and Celia wants a practical compromise. Let each initiate an action, keep viewpoint transitions explicit, and end before anyone leaves or resolves the entire conflict.",
    ),
)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def run(output: Path) -> int:
    init_db()
    session_id = str(uuid4())
    with db_session() as db:
        db.execute(
            "INSERT INTO sessions (id, title) VALUES (?, ?)",
            (session_id, "Disposable Planner Latency Profile"),
        )
    rows: list[dict[str, object]] = []
    try:
        inherited_settings, inherited_resolved = resolve_task_model_settings("prose_generation")
        inherited_model = str(inherited_settings.model or "")
        for name, note in CASES:
            result = await run_scene_planning_pass(
                session_id=session_id,
                mode="continue",
                director_note=note,
                writing_length={"mode": "scene", "label": "Scene", "min_words": 800, "max_words": 1400},
                recent_scenes=[],
                session_summary=None,
                target_scene=None,
                inherited_settings=inherited_settings,
                inherited_resolved=inherited_resolved,
                inherited_model=inherited_model,
            )
            metadata = result["metadata"]
            plan = result["plan"]
            contract = plan.get("scene_contract") or {}
            blocking = plan.get("blocking_map") or {}
            rows.append(
                {
                    "case": name,
                    "duration_seconds": metadata.get("duration_seconds"),
                    "prompt_chars": metadata.get("prompt_chars"),
                    "raw_response_chars": metadata.get("raw_response_chars"),
                    "json_repair_used": metadata.get("json_repair_used"),
                    "deterministic_fallback_used": metadata.get("deterministic_fallback_used"),
                    "model": metadata.get("model"),
                    "required_contract_fields_present": all(
                        key in contract
                        for key in (
                            "start_time",
                            "allowed_duration",
                            "start_location",
                            "end_boundary",
                            "required_beats",
                            "must_not_occur_yet",
                            "time_skip_allowed",
                            "scene_question",
                            "emotional_progression",
                            "ending_handoff",
                        )
                    ),
                    "blocking_fields_present": len(blocking) == 9,
                    "agency_rows": len(plan.get("character_agency_matrix") or []),
                    "required_fact_count": len(plan.get("required_director_facts") or []),
                }
            )
        durations = [float(row["duration_seconds"] or 0) for row in rows]
        result = {
            "status": "complete",
            "created_at": utc_now(),
            "cases": rows,
            "mean_seconds": round(statistics.mean(durations), 3),
            "median_seconds": round(statistics.median(durations), 3),
            "max_seconds": round(max(durations), 3),
            "fallback_count": sum(bool(row["deterministic_fallback_used"]) for row in rows),
            "json_repair_count": sum(bool(row["json_repair_used"]) for row in rows),
        }
        atomic_json(output, result)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        with db_session() as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile StoryDriver's planner without generating prose.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "backend" / "data" / "logs" / "planner_latency_profile_latest.json",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
