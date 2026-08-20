from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routes.sessions import generation_adherence_warnings  # noqa: E402
from app.generation.pipeline import (  # noqa: E402
    PLAN_KEYS,
    build_quality_review_prompt,
    build_scene_planning_prompt,
    deterministic_scene_plan,
)
from app.generation.prompt_builder import build_scene_prompt  # noqa: E402


SCENE_LENGTH = {
    "mode": "scene",
    "label": "Scene",
    "min_words": 800,
    "max_words": 1400,
    "description": "medium scene with developed pacing",
}
CHAPTER_LENGTH = {
    "mode": "chapter",
    "label": "Chapter",
    "min_words": 1800,
    "max_words": 2600,
    "description": "long readable chapter",
}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def plan_for(note: str, length: dict | None = None, active_characters: list[dict] | None = None) -> dict:
    return deterministic_scene_plan(
        session_id="scene-contract-static-test",
        mode="continue",
        director_note=note,
        writing_length=length or SCENE_LENGTH,
        recent_scenes=[],
        session_summary=None,
        target_scene=None,
        world_notes=None,
        active_characters=active_characters or [],
    )


def plan_text(plan: dict) -> str:
    return str(plan).lower()


def test_schema_contract_is_complete() -> None:
    plan = plan_for("First chapter: introduce Mara, Iven, and Sera in the apartment before the later escape.")
    assert_true(set(PLAN_KEYS).issubset(plan.keys()), "Plan is missing required v2 schema keys.")
    for key in ("scene_contract", "blocking_map", "character_agency_matrix", "viewpoint_contract"):
        assert_true(isinstance(plan[key], (dict, list)), f"{key} was not structured.")
    contract = plan["scene_contract"]
    for key in (
        "start_time",
        "allowed_duration",
        "start_location",
        "end_boundary",
        "required_beats",
        "must_not_occur_yet",
        "introduction_obligations",
        "time_skip_allowed",
        "scene_question",
        "emotional_progression",
    ):
        assert_true(key in contract, f"Scene contract missing {key}.")


def test_first_chapter_intro_does_not_skip_to_action() -> None:
    plan = plan_for(
        "First chapter: introduce Mara, Iven, and Sera in their safehouse. The future plot involves a rescue, but this opening is only introductions."
    )
    contract = plan["scene_contract"]
    assert_true(contract["time_skip_allowed"] is False, "Intro scene should not allow a time skip.")
    must_not = " ".join(contract["must_not_occur_yet"]).lower()
    assert_true("skip introductions" in must_not or "action" in must_not, "Intro plan did not forbid skipping introductions.")
    assert_true("rescue" in " ".join(contract["backstory_to_dramatize"]).lower() or "future" in plan_text(plan), "Future premise was not treated as context.")


def test_modern_apartment_object_handoff_blocking() -> None:
    plan = plan_for(
        "Modern apartment scene: Lena, Maya, and Noor argue in the living room. Maya gives Lena the keycard but no one leaves."
    )
    blocking = plan["blocking_map"]
    assert_true(any("apartment" in zone.lower() or "living" in zone.lower() for zone in blocking["location_zones"]), "Apartment zones missing.")
    objects = " ".join(blocking["carried_placed_objects"]).lower()
    assert_true("keycard" in objects or "ownership" in objects, "Object handoff was not represented in blocking.")
    assert_true(len(plan["character_agency_matrix"]) >= 3, "Agency matrix lost a named apartment character.")


def test_medieval_planning_scene_must_not_reach_battle() -> None:
    plan = plan_for(
        "Medieval no-magic planning scene: three adult sisters study a map before the battle. They must not reach the battle yet."
    )
    must_not = " ".join(plan["scene_contract"]["must_not_occur_yet"]).lower()
    assert_true("battle" in must_not, "Planning contract did not forbid reaching battle.")
    assert_true(plan["scene_contract"]["time_skip_allowed"] is False, "Planning scene should not allow a time skip.")


def test_scifi_ship_technical_but_human_dialogue() -> None:
    plan = plan_for("Sci-fi ship scene: engineer Vale and pilot Rin argue beside the airlock about a failing coolant loop.")
    anchors = " ".join(plan["world_grounding_anchors"]).lower()
    assert_true("technical" in anchors or "technology" in anchors or "coolant" in plan_text(plan), "Sci-fi technical grounding missing.")
    blocking = " ".join(plan["blocking_map"]["doors_exits_windows"]).lower()
    assert_true("hatch" in blocking or "exit" in blocking or "door" in blocking, "Ship exits/hatches not represented.")


def test_tactical_movement_across_multiple_zones() -> None:
    plan = plan_for("Tactical movement scene: cross from the loading bay through the corridor to the engine room under fire.")
    zones = " ".join(plan["blocking_map"]["location_zones"]).lower()
    assert_true("start zone" in zones or "transition" in zones or "destination" in zones, "Tactical zones missing.")
    assert_true(plan["blocking_map"]["entries_exits"], "Tactical entries/exits were not planned.")


def test_relationship_argument_distinct_goals() -> None:
    plan = plan_for(
        "Relationship argument: Mara wants to call the police, Iven wants to hide the letter, and Sera wants everyone to leave.",
        active_characters=[
            {"name": "Mara", "role": "doctor", "relationships": "protective of Sera", "current_state": "angry and afraid"},
            {"name": "Iven", "role": "brother", "relationships": "hiding something from Mara", "current_state": "defensive"},
            {"name": "Sera", "role": "roommate", "relationships": "caught between them", "current_state": "ready to run"},
        ],
    )
    matrix = plan["character_agency_matrix"]
    assert_true(len(matrix) >= 3, "Relationship scene agency matrix lost a present character.")
    objections = {row["character"]: row["likely_objection"] for row in matrix}
    assert_true(len(set(objections.values())) >= 2, "Agency matrix did not create distinct objections.")
    assert_true(all(row["relationship_pressure"] for row in matrix), "Relationship pressure missing from agency rows.")


def test_long_chapter_deepens_without_time_skip() -> None:
    plan = plan_for("Long first chapter: introduce the crew during one continuous dinner conversation before tomorrow's launch.", CHAPTER_LENGTH)
    contract = plan["scene_contract"]
    assert_true(contract["time_skip_allowed"] is False, "Long chapter should not automatically allow a time skip.")
    must_not = " ".join(contract["must_not_occur_yet"]).lower()
    assert_true("long requested length" in must_not or "fast-forward" in must_not, "Long chapter did not forbid fast-forwarding.")


def test_future_backstory_remains_future() -> None:
    plan = plan_for(
        "Opening scene: introduce Ana and Jules in the workshop. Years later Ana will betray Jules during the siege, but that future backstory must stay future."
    )
    contract = plan["scene_contract"]
    assert_true(contract["backstory_to_dramatize"], "Future/backstory material was not separated from current beats.")
    assert_true(contract["time_skip_allowed"] is False, "Future backstory should not authorize a time skip.")
    assert_true("betray" in plan_text(plan) and "future" in plan_text(plan), "Future betrayal was not preserved as future context.")


def test_prompt_and_review_include_contract_language() -> None:
    plan = plan_for("Modern apartment scene: Maya gives Lena the keycard and they argue without leaving.")
    prose_prompt = build_scene_prompt(
        session_id="scene-contract-static-test",
        director_note="Modern apartment scene: Maya gives Lena the keycard and they argue without leaving.",
        mode="continue",
        recent_scenes=[],
        session_summary=None,
        target_scene=None,
        task_notes="Write narrated fiction prose only.",
        writing_length=SCENE_LENGTH,
        writing_process_plan=plan,
        world_notes=None,
        active_characters=[],
        mark_state_used=False,
    )
    lower_prompt = prose_prompt.lower()
    for phrase in ("scene contract", "blocking map", "character agency matrix", "viewpoint contract"):
        assert_true(phrase in lower_prompt, f"Prose prompt missing {phrase}.")

    planning_prompt = build_scene_planning_prompt(context_text="Context", mode="continue", task_notes="Task notes")
    planning_lower = planning_prompt.lower()
    for phrase in ("time_skip_allowed", "must_not_occur_yet", "sightlines", "anti_fixation_checks"):
        assert_true(phrase in planning_lower, f"Planning prompt missing {phrase}.")

    review_prompt = build_quality_review_prompt(
        context_text="Context",
        scene_plan=plan,
        draft_text="Draft text",
        task_notes="Review notes",
        writing_length=SCENE_LENGTH,
    )
    review_lower = review_prompt.lower()
    for phrase in ("scene_contract", "blocking", "head-hopping", "artificial cliffhanger", "repetitive fixation"):
        assert_true(phrase in review_lower, f"Quality review prompt missing {phrase}.")


def test_deterministic_adherence_warnings() -> None:
    warnings = generation_adherence_warnings(
        "Planning scene before the battle. Do not reach the battle yet.",
        "They argued for a few minutes. Days later, after the battle, Mara counted the wounded.",
        CHAPTER_LENGTH,
    )
    joined = " ".join(warnings).lower()
    assert_true("time skip" in joined or "aftermath" in joined, "Unauthorized time skip warning missing.")
    assert_true("setup" in joined or "planning" in joined, "Setup/planning jump warning missing.")


def main() -> int:
    test_schema_contract_is_complete()
    test_first_chapter_intro_does_not_skip_to_action()
    test_modern_apartment_object_handoff_blocking()
    test_medieval_planning_scene_must_not_reach_battle()
    test_scifi_ship_technical_but_human_dialogue()
    test_tactical_movement_across_multiple_zones()
    test_relationship_argument_distinct_goals()
    test_long_chapter_deepens_without_time_skip()
    test_future_backstory_remains_future()
    test_prompt_and_review_include_contract_language()
    test_deterministic_adherence_warnings()
    print("[OK] scene contract/blocking static tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
