from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation.pipeline import PLAN_KEYS, deterministic_scene_plan  # noqa: E402
from app.generation.prompt_builder import build_scene_prompt  # noqa: E402
from app.settings.store import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TASK_NOTES,
    STORYDRIVER_PROSE_V2_SYSTEM_PROMPT,
    STORYDRIVER_PROSE_V3_PRESET_ID,
    STORYDRIVER_PROSE_V3_PRESET_NAME,
    STORYDRIVER_PROSE_V3_SYSTEM_PROMPT,
    STORYDRIVER_PROSE_V3_TASK_NOTES,
    seed_storydriver_prose_v3_preset_for_connection,
    upgrade_legacy_model_settings_to_prose_v3_for_connection,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def memory_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE model_presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            system_prompt TEXT NOT NULL DEFAULT '',
            settings_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )
    return db


def test_prompt_contract() -> None:
    prompt = STORYDRIVER_PROSE_V3_SYSTEM_PROMPT
    lower = prompt.lower()
    assert_true(DEFAULT_SYSTEM_PROMPT == STORYDRIVER_PROSE_V3_SYSTEM_PROMPT, "Prose v3 is not the backend default system prompt.")
    required_terms = [
        "concrete moment",
        "do not rush introductions",
        "clear adult marker",
        "interiority",
        "preserve agency",
        "relationships active",
        "spatial continuity",
        "modern scenes should sound natural and contemporary",
        "not fake-archaic",
        "generic technobabble",
        "distinct by character",
        "clearly adult consenting fictional characters",
        "never sexualize minors",
        "regenerate/rewrite/revise",
        "do not create a new later scene",
    ]
    for term in required_terms:
        assert_true(term in lower, f"Prose v3 prompt missing contract term: {term}")


def test_task_notes_contract() -> None:
    notes = DEFAULT_TASK_NOTES["prose_generation"]
    rewrite_notes = DEFAULT_TASK_NOTES["rewrite_revision"]
    assert_true(notes == STORYDRIVER_PROSE_V3_TASK_NOTES, "Prose generation task notes do not use v3.")
    assert_true(len(notes) < 1800, "Prose v3 task notes should stay concise.")
    assert_true(len(notes) < len(STORYDRIVER_PROSE_V3_SYSTEM_PROMPT) / 2, "Task notes duplicate too much of the full system prompt.")
    for term in (
        "concrete moment",
        "continue creates a new scene",
        "regenerate, rewrite, and revise stay on the same target scene/version",
        "interiority",
        "distinct dialogue",
        "exact room/object/body blocking",
        "adult consensual intimacy",
    ):
        assert_true(term in notes.lower(), f"Prose v3 task notes missing: {term}")
    assert_true("without appending a later story beat" in rewrite_notes.lower(), "Rewrite notes do not preserve version scope.")


def test_preset_seed_is_non_destructive() -> None:
    with memory_db() as db:
        inserted = seed_storydriver_prose_v3_preset_for_connection(db)
        assert_true(inserted, "First seed should insert Prose v3 preset.")
        row = db.execute("SELECT * FROM model_presets WHERE id = ?", (STORYDRIVER_PROSE_V3_PRESET_ID,)).fetchone()
        assert_true(row is not None, "Seeded Prose v3 preset row is missing.")
        assert_true(row["name"] == STORYDRIVER_PROSE_V3_PRESET_NAME, "Seeded Prose v3 preset name is wrong.")
        assert_true(row["system_prompt"] == STORYDRIVER_PROSE_V3_SYSTEM_PROMPT, "Seeded Prose v3 system prompt is wrong.")
        inserted_again = seed_storydriver_prose_v3_preset_for_connection(db)
        assert_true(not inserted_again, "Second seed should not insert again.")

    with memory_db() as db:
        db.execute(
            "INSERT INTO model_presets (id, name, system_prompt, settings_json) VALUES (?, ?, ?, ?)",
            (STORYDRIVER_PROSE_V3_PRESET_ID, STORYDRIVER_PROSE_V3_PRESET_NAME, "user edited preset", "{}"),
        )
        inserted = seed_storydriver_prose_v3_preset_for_connection(db)
        row = db.execute("SELECT system_prompt FROM model_presets WHERE id = ?", (STORYDRIVER_PROSE_V3_PRESET_ID,)).fetchone()
        assert_true(not inserted, "Existing Prose v3 preset should not be overwritten.")
        assert_true(row["system_prompt"] == "user edited preset", "Existing edited Prose v3 preset was overwritten.")


def test_legacy_upgrade_preserves_custom_prompts() -> None:
    with memory_db() as db:
        db.execute(
            "INSERT INTO app_settings (key, value_json) VALUES (?, ?)",
            ("model_settings", json.dumps({"active_preset_id": None, "system_prompt": STORYDRIVER_PROSE_V2_SYSTEM_PROMPT})),
        )
        upgraded = upgrade_legacy_model_settings_to_prose_v3_for_connection(db)
        row = db.execute("SELECT value_json FROM app_settings WHERE key = 'model_settings'").fetchone()
        assert_true(upgraded, "Untouched legacy v2 model settings should upgrade to v3.")
        assert_true(json.loads(row["value_json"])["system_prompt"] == STORYDRIVER_PROSE_V3_SYSTEM_PROMPT, "Legacy prompt did not upgrade.")

    with memory_db() as db:
        db.execute(
            "INSERT INTO app_settings (key, value_json) VALUES (?, ?)",
            ("model_settings", json.dumps({"active_preset_id": None, "system_prompt": "custom user prompt"})),
        )
        upgraded = upgrade_legacy_model_settings_to_prose_v3_for_connection(db)
        row = db.execute("SELECT value_json FROM app_settings WHERE key = 'model_settings'").fetchone()
        assert_true(not upgraded, "Custom model settings should not be upgraded silently.")
        assert_true(json.loads(row["value_json"])["system_prompt"] == "custom user prompt", "Custom prompt was overwritten.")

    with memory_db() as db:
        db.execute(
            "INSERT INTO app_settings (key, value_json) VALUES (?, ?)",
            ("model_settings", json.dumps({"active_preset_id": "custom-preset", "system_prompt": STORYDRIVER_PROSE_V2_SYSTEM_PROMPT})),
        )
        upgraded = upgrade_legacy_model_settings_to_prose_v3_for_connection(db)
        assert_true(not upgraded, "Active preset model settings should not be silently changed.")


def plan_for(note: str, mode: str = "continue") -> dict:
    return deterministic_scene_plan(
        session_id="prose-v3-static-test",
        mode=mode,
        director_note=note,
        writing_length={
            "mode": "scene",
            "label": "Scene",
            "min_words": 800,
            "max_words": 1400,
            "description": "medium scene with developed pacing",
        },
        recent_scenes=[],
        session_summary=None,
        target_scene=None,
        world_notes=None,
        active_characters=[],
    )


def test_requested_scenarios_are_plannable() -> None:
    notes = [
        "Modern present-day opening: Maya arrives at a crowded hospital and must identify her estranged sister without archaic diction.",
        "Medieval no-magic first chapter: three adult sisters argue in the stable about whether to rescue a kidnapped child.",
        "Sci-fi opening aboard a failing colony ship where an engineer and pilot disagree beside the airlock.",
        "Introduce a clearly adult character with hair, face, build, clothing, posture, voice, and visible nervous habit.",
        "Keep the scene narrow: two adults in one kitchen argue over a broken promise without jumping to tomorrow.",
        "Adult-only consensual intimacy between clearly adult fictional characters, with consent, desire, and relationship context.",
    ]
    for note in notes:
        plan = plan_for(note)
        assert_true(set(PLAN_KEYS).issubset(plan.keys()), f"Plan missing schema keys for note: {note}")
        assert_true(plan["required_director_facts"], f"Plan lost required director facts for note: {note}")
        assert_true(isinstance(plan["location_and_blocking"], dict), "Plan location/blocking is not structured.")


def test_prompt_preview_sections() -> None:
    plan = plan_for("Modern opening in a kitchen where two adult siblings disagree over a locked drawer.")
    prompt = build_scene_prompt(
        session_id="prose-v3-static-test",
        director_note="Modern opening in a kitchen where two adult siblings disagree over a locked drawer.",
        mode="continue",
        recent_scenes=[],
        session_summary="The siblings inherited the house last week.",
        target_scene=None,
        task_notes=STORYDRIVER_PROSE_V3_TASK_NOTES,
        writing_length={
            "mode": "scene",
            "label": "Scene",
            "min_words": 800,
            "max_words": 1400,
            "description": "medium scene with developed pacing",
        },
        writing_process_plan=plan,
        world_notes={"setting": "Present-day Denver townhouse", "tone": "tense and intimate"},
        active_characters=[
            {
                "name": "Maya",
                "role": "older sister",
                "personality": "controlled, protective, blunt",
                "appearance": "adult woman with close-cropped black hair and a gray work jacket",
                "relationships": "estranged from Lena",
                "current_state": "standing near the kitchen island",
            }
        ],
        prompt_mode="standard",
        mark_state_used=False,
    )
    for section in (
        "TASK NOTES:",
        "GENRE / TIME-PERIOD DICTION GUIDE:",
        "STRUCTURED SCENE PLAN:",
        "ACTIVE CHARACTERS:",
        "SESSION SUMMARY:",
        "DIRECTOR NOTE (strongest immediate instruction):",
        "Mode: continue",
    ):
        assert_true(section in prompt, f"Prompt preview user prompt missing section: {section}")


def main() -> int:
    test_prompt_contract()
    test_task_notes_contract()
    test_preset_seed_is_non_destructive()
    test_legacy_upgrade_preserves_custom_prompts()
    test_requested_scenarios_are_plannable()
    test_prompt_preview_sections()
    print("[OK] StoryDriver Prose v3 static contracts passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
