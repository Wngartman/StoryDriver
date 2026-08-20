from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation.pipeline import (  # noqa: E402
    PLAN_KEYS,
    build_targeted_repair_prompt,
    compact_pipeline_stats,
    deterministic_quality_review,
    deterministic_scene_plan,
    generate_validated_quality_review,
    generate_validated_scene_plan,
    should_run_targeted_repair,
)
from app.settings.store import TASK_MODEL_DEFINITIONS  # noqa: E402
from app.database import db_session, init_db  # noqa: E402
from app.routes.sessions import save_generated_scene  # noqa: E402


class FakeStructuredClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    async def generate_scene_routed(self, **kwargs):
        del kwargs
        self.calls += 1
        if self.calls <= len(self.responses):
            return {"text": self.responses[self.calls - 1]}
        return {"text": self.responses[-1] if self.responses else ""}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def plan_for(note: str, mode: str = "continue") -> dict:
    return deterministic_scene_plan(
        session_id="static-session",
        mode=mode,
        director_note=note,
        writing_length={"mode": "scene", "label": "Scene", "min_words": 800, "max_words": 1400},
        recent_scenes=[],
        session_summary=None,
        target_scene=None,
        world_notes=None,
        active_characters=[],
    )


async def test_malformed_planner_repair() -> None:
    fallback = plan_for("Modern scene: Alex enters the apartment and finds the hidden key.")
    fixed_json = """
    {
      "scene_mode": "continue",
      "scene_purpose": "Alex searches the apartment.",
      "requested_scope": "One focused scene.",
      "allowed_time_span": "Minutes.",
      "must_not_advance_beyond": "Do not leave the apartment.",
      "required_director_facts": ["Alex finds the hidden key"],
      "characters_present": ["Alex"],
      "viewpoint_strategy": "Close third.",
      "character_motives": [],
      "location_and_blocking": {"location": "apartment", "important_zones": [], "character_positions": [], "entrances_exits": [], "visible_objects": ["hidden key"]},
      "continuity_facts": [],
      "sensory_anchors": ["metal key"],
      "story_beats": ["Search", "Discovery"],
      "character_introduction_requirements": [],
      "forbidden_leaps_or_skips": ["No aftermath jump"],
      "ending_handoff": "Alex holds the key.",
      "continuity_risks": []
    }
    """
    client = FakeStructuredClient(["not json", fixed_json])
    plan, _raw, repaired, fallback_used = await generate_validated_scene_plan(
        client=client,
        model="fake",
        system_prompt="json",
        user_prompt="plan",
        parameters={"max_tokens": 1000},
        timeout=5,
        inference_backend="openai_compatible",
        reasoning_mode="off",
        context_length=None,
        fallback_to_openai_compatible=True,
        fallback_plan=fallback,
        mode="continue",
    )
    assert_true(not repaired, "Malformed planner JSON should no longer spend a repair attempt.")
    assert_true(fallback_used, "Malformed planner JSON should use deterministic fallback immediately.")
    assert_true(client.calls == 1, f"Planner fallback should call model once, called {client.calls}.")
    assert_true(set(PLAN_KEYS).issubset(plan.keys()), "Fallback plan missing required schema keys.")


async def test_planner_fallback_after_one_repair() -> None:
    fallback = plan_for("Medieval fantasy scene: the sisters plan a rescue at the stable.")
    client = FakeStructuredClient(["nope", "still not json"])
    plan, _raw, repaired, fallback_used = await generate_validated_scene_plan(
        client=client,
        model="fake",
        system_prompt="json",
        user_prompt="plan",
        parameters={"max_tokens": 1000},
        timeout=5,
        inference_backend="openai_compatible",
        reasoning_mode="off",
        context_length=None,
        fallback_to_openai_compatible=True,
        fallback_plan=fallback,
        mode="continue",
    )
    assert_true(not repaired, "Planner fallback should skip JSON repair for speed.")
    assert_true(fallback_used, "Planner should use deterministic fallback after malformed JSON.")
    assert_true(client.calls == 1, f"Planner fallback should not loop, called {client.calls}.")
    assert_true(set(PLAN_KEYS).issubset(plan.keys()), "Fallback plan missing required schema keys.")


async def test_malformed_review_repair() -> None:
    fixed_json = '{"pass": false, "severity": "major", "issues": ["time skip"], "repair_instructions": ["Remove the skip."]}'
    client = FakeStructuredClient(["```json\nbad\n```", fixed_json])
    review, _raw, repaired, fallback_used = await generate_validated_quality_review(
        client=client,
        model="fake",
        system_prompt="json",
        user_prompt="review",
        parameters={"max_tokens": 800},
        timeout=5,
        inference_backend="openai_compatible",
        reasoning_mode="off",
        context_length=None,
        fallback_to_openai_compatible=True,
    )
    assert_true(repaired, "Malformed review JSON did not trigger one repair attempt.")
    assert_true(not fallback_used, "Valid repaired review should not use fallback.")
    assert_true(client.calls == 2, f"Review repair should call model exactly twice, called {client.calls}.")
    assert_true(should_run_targeted_repair(review), "Major review should gate one targeted repair.")


def test_required_task_profiles() -> None:
    expected = {
        "story_foundation_generation",
        "scene_planning",
        "prose_generation",
        "rewrite_revision",
        "scene_quality_review",
        "story_state_extraction",
        "summary_generation",
        "title_generation",
        "utility",
    }
    missing = expected - set(TASK_MODEL_DEFINITIONS)
    assert_true(not missing, f"Missing task model definitions: {sorted(missing)}")


def test_plan_contracts() -> None:
    notes = [
        "First chapter with several main characters in a grounded medieval rescue setup.",
        "Modern present-day scene: a narrow apartment conversation after extensive backstory.",
        "Sci-fi tactical room-blocking scene aboard a damaged colony ship.",
        "Emotional relationship scene where two adults discuss a broken promise.",
        "Adult-only consensual intimacy between clearly adult fictional characters, focused on consent and psychology.",
    ]
    for note in notes:
        plan = plan_for(note)
        assert_true(set(PLAN_KEYS).issubset(plan.keys()), f"Plan missing keys for note: {note}")
        assert_true(plan["scene_mode"] == "continue", "Plan mode was not normalized.")
        assert_true(plan["required_director_facts"], f"Plan did not preserve director facts for note: {note}")
        assert_true(isinstance(plan["location_and_blocking"], dict), "Location/blocking was not structured.")


def test_compact_metadata_no_raw_persistence() -> None:
    stats = compact_pipeline_stats(
        planning_metadata={"duration_seconds": 1.25, "raw_response_chars": 400, "story_beat_count": 3},
        review_metadata={"duration_seconds": 0.5, "severity": "minor", "issues": ["short"]},
        repair_metadata=None,
        repair_ran=False,
    )
    assert_true(stats["mandatory"], "Pipeline metadata did not mark the pipeline mandatory.")
    assert_true(stats["raw_chain_of_thought_saved"] is False, "Pipeline should never mark raw chain-of-thought as saved.")
    assert_true(stats["raw_plans_reviews_persisted"] is False, "Pipeline should not persist raw plans/reviews.")
    assert_true(stats["latency_seconds"]["added_total"] == 1.75, "Pipeline added latency was not summarized.")


def test_material_over_length_triggers_one_targeted_repair() -> None:
    writing_length = {"mode": "custom", "label": "Custom", "min_words": 950, "max_words": 1150}
    draft = " ".join(["word"] * 1457)
    review = deterministic_quality_review(scene_plan=plan_for("Keep the meeting focused."), draft_text=draft, writing_length=writing_length)
    assert_true(review["severity"] == "major", "A material over-length draft should trigger the one targeted repair.")
    assert_true(any("1457 words" in issue for issue in review["issues"]), "The review should report the measured word count.")
    prompt = build_targeted_repair_prompt(
        context_text="Apartment meeting.",
        scene_plan=plan_for("Keep the meeting focused."),
        draft_text=draft,
        quality_review=review,
        writing_length=writing_length,
    )
    assert_true("Current draft length: 1457 words" in prompt, "Repair prompt should expose the measured draft length.")
    assert_true("must fit that final range" in prompt, "Repair prompt should make the final range binding.")


def test_route_single_save_shape() -> None:
    source = (ROOT / "backend" / "app" / "routes" / "sessions.py").read_text(encoding="utf-8")
    run_generation_block = source.split("async def run_generation", 1)[1].split("def preview_generation_payload", 1)[0]
    stream_block = source.split("async def generate_scene_stream", 1)[1]
    assert_true(run_generation_block.count("save_generated_scene(") == 1, "Non-stream generation route should save exactly once.")
    assert_true(stream_block.count("save_generated_scene(") == 1, "Streaming generation route should save exactly once.")


def test_versioned_save_contract() -> None:
    init_db()
    session_id = str(uuid4())
    with db_session() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, "Deliberate Pipeline Static Version Test"))
    try:
        scene = save_generated_scene(
            session_id=session_id,
            director_note="Create the first scene.",
            generated_text="First saved scene.",
            generation_stats={"test": "continue"},
            mode="continue",
            target_scene_id=None,
        )
        scene_id = scene.id
        assert_true(scene.version_count == 1, "Continue should create one initial version.")
        for mode in ("regenerate", "rewrite", "revise"):
            scene = save_generated_scene(
                session_id=session_id,
                director_note=f"{mode} the same scene.",
                generated_text=f"{mode} version text.",
                generation_stats={"test": mode},
                mode=mode,
                target_scene_id=scene_id,
            )
            assert_true(scene.id == scene_id, f"{mode} should stay on the same scene id.")
        with db_session() as db:
            scene_count = db.execute("SELECT COUNT(*) AS count FROM scenes WHERE session_id = ?", (session_id,)).fetchone()["count"]
            version_count = db.execute("SELECT COUNT(*) AS count FROM scene_versions WHERE session_id = ?", (session_id,)).fetchone()["count"]
        assert_true(scene_count == 1, f"Versioned modes should not create duplicate scenes; got {scene_count}.")
        assert_true(version_count == 4, f"Expected 4 total versions after regenerate/rewrite/revise; got {version_count}.")
    finally:
        with db_session() as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


async def main() -> int:
    test_required_task_profiles()
    test_plan_contracts()
    await test_malformed_planner_repair()
    await test_planner_fallback_after_one_repair()
    await test_malformed_review_repair()
    test_compact_metadata_no_raw_persistence()
    test_material_over_length_triggers_one_targeted_repair()
    test_route_single_save_shape()
    test_versioned_save_contract()
    print("[OK] deliberate pipeline static tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
