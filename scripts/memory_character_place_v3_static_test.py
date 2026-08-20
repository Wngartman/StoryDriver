from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import init_db  # noqa: E402
from app.memory.context import format_next_prompt_memory_pack  # noqa: E402
from app.memory.engine import (  # noqa: E402
    fallback_character_live_updates,
    fallback_character_names,
    fallback_object_updates,
    fallback_scene_blocking_updates,
    supplement_partial_state_payload,
)


DB_PATH = ROOT / "backend" / "data" / "app.db"
SESSION_TITLE = "StoryDriver Memory Character Place V3 Static Test"


def insert_character(db: sqlite3.Connection, session_id: str, name: str, role: str) -> str:
    character_id = str(uuid4())
    db.execute(
        """
        INSERT INTO characters (id, name, role, personality, appearance, current_state)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            character_id,
            name,
            role,
            "Synthetic v3 test character with a clear objection and private pressure.",
            "Adult, visually distinct, grounded in the current room.",
            "Active in the disposable memory v3 test.",
        ),
    )
    db.execute(
        "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
        (str(uuid4()), session_id, character_id),
    )
    return character_id


def seed_session() -> str:
    init_db()
    session_id = str(uuid4())
    scene_id = str(uuid4())
    version_id = str(uuid4())
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, SESSION_TITLE))
        mara_id = insert_character(db, session_id, "Mara Memory V3", "Apartment tenant")
        iven_id = insert_character(db, session_id, "Iven Memory V3", "Neighbor with the key")
        joss_id = insert_character(db, session_id, "Joss Memory V3", "Friend caught between them")
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
            VALUES (?, ?, ?, ?, 'continue')
            """,
            (
                scene_id,
                session_id,
                "Disposable v3 continuity seed.",
                "Mara waited beside the apartment door while Iven held the brass key near the kitchen table.",
            ),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, 'continue', 1)
            """,
            (
                version_id,
                scene_id,
                session_id,
                "Disposable v3 continuity seed.",
                "Mara waited beside the apartment door while Iven held the brass key near the kitchen table.",
            ),
        )
        foundation = {
            "overview": {
                "premise": "Three adults in a modern apartment negotiate trust around a key and a sealed letter.",
                "genre": "present-day character drama",
                "time_period": "modern evening",
                "tone": "tense, intimate, practical",
                "story_rules": ["Keep the scene in the apartment until someone explicitly leaves."],
            },
            "world": {
                "rules": ["Phones, locks, neighbors, and hallway noise matter."],
                "locations": ["apartment entry", "kitchen table", "hallway door"],
                "factions": [],
                "conflicts": ["a hidden letter changes trust"],
                "technology_magic": "modern tools only; no magic",
                "source": "test",
            },
            "characters": [
                {"name": "Mara Memory V3", "role": "tenant", "personality": "controlled but angry", "private_goal": "keep the letter from being opened too soon"},
                {"name": "Iven Memory V3", "role": "neighbor", "personality": "guilty and practical", "private_goal": "return the brass key without admitting why he had it"},
                {"name": "Joss Memory V3", "role": "friend", "personality": "protective mediator", "private_goal": "keep both of them from escalating"},
            ],
            "relationships": [
                {
                    "characters": ["Mara Memory V3", "Iven Memory V3"],
                    "type": "strained ally",
                    "dynamic": "they need each other but distrust the key handoff",
                    "tension": "Mara thinks Iven knows more than he admits",
                }
            ],
            "narrative_contract": {
                "viewpoint_default": "rotating close third with explicit transitions",
                "prose_priorities": ["relationship pressure", "blocking", "object continuity"],
                "time_policy": "continuous apartment scene time",
            },
            "continuity_seed": {
                "initial_objects": ["brass key", "sealed letter"],
                "active_locations": ["entry door", "kitchen table"],
                "not_yet_events": ["do not leave for the hallway confrontation yet"],
                "relationship_pressures": ["Mara and Iven disagree about whether to tell Joss"],
                "secrets_or_hidden_information": ["Iven knows why the key was missing"],
            },
            "opening_scope": {
                "start_location": "apartment entry",
                "start_time": "same evening",
                "initial_pressure": "the brass key has just changed hands",
                "first_scene_jobs": ["keep all three people present", "track the key"],
                "boundaries": ["do not skip to morning"],
                "introduction_obligations": ["ground each person in the room"],
                "must_not_occur_yet": ["do not open the sealed letter yet"],
                "time_skip_policy": "no time skip",
            },
        }
        db.execute(
            """
            INSERT INTO story_foundations (id, session_id, schema_version, status, source_director_note, foundation_json)
            VALUES (?, ?, 'story_foundation_v3', 'ready', ?, ?)
            """,
            (str(uuid4()), session_id, "Disposable v3 continuity seed.", json.dumps(foundation)),
        )
        character_state = [
            (mara_id, "Mara Memory V3", "blocking", "room_position", "beside the apartment door, facing Iven", 0.94),
            (mara_id, "Mara Memory V3", "appearance", "current_outfit", "wearing a black coat over a gray shirt", 0.9),
            (mara_id, "Mara Memory V3", "knowledge", "known_secrets", "knows Iven returned the brass key late", 0.92),
            (iven_id, "Iven Memory V3", "blocking", "room_position", "near the kitchen table", 0.94),
            (iven_id, "Iven Memory V3", "blocking", "carried_objects", "holding the brass key", 0.94),
            (iven_id, "Iven Memory V3", "goal", "short_term_goal", "return the key without explaining the delay", 0.88),
            (joss_id, "Joss Memory V3", "blocking", "room_position", "between Mara and Iven, close enough to hear both", 0.88),
        ]
        for character_id, name, state_type, key, value, confidence in character_state:
            db.execute(
                """
                INSERT INTO character_live_state (
                    id, session_id, character_id, character_name, state_type, key, value,
                    confidence, is_tentative, source_scene_id, source_version_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (str(uuid4()), session_id, character_id, name, state_type, key, value, confidence, scene_id, version_id),
            )
        db.execute(
            """
            INSERT INTO object_state (
                id, session_id, object_key, name, state_type, value, previous_value,
                owner_character_id, owner_character_name, confidence, is_tentative,
                source_scene_id, source_version_id
            )
            VALUES (?, ?, 'brass_key', 'brass key', 'ownership', 'carried by Iven Memory V3',
                    'hidden behind the loose wall tile beside the old dryer', ?, 'Iven Memory V3', 0.94, 0, ?, ?)
            """,
            (str(uuid4()), session_id, iven_id, scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO object_state (
                id, session_id, object_key, name, state_type, value,
                owner_character_id, owner_character_name, confidence, is_tentative,
                source_scene_id, source_version_id
            )
            VALUES (?, ?, 'sealed_letter', 'sealed letter', 'location', 'under the kitchen table edge', NULL, '', 0.86, 0, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO relationship_state (
                id, session_id, character_a_id, character_a_name, character_b_id, character_b_name,
                relationship_type, relationship_key, content, trust_weight, conflict_weight,
                emotional_importance, confidence, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, 'Mara Memory V3', ?, 'Iven Memory V3', 'ally', 'key_trust_tension',
                    'Mara and Iven need each other but the key delay has damaged trust.', 0.34, 0.74, 0.78, 0.9, ?, ?)
            """,
            (str(uuid4()), session_id, mara_id, iven_id, scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO emotional_memories (
                id, session_id, memory_type, memory_text, emotional_weight, themes_json,
                related_characters_json, trigger_conditions, confidence, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'betrayal', 'Mara remembers Iven lying about the missing brass key.',
                    0.92, '["trust","key"]', '["Mara Memory V3","Iven Memory V3"]',
                    'Recall when keys, trust, or the apartment door matter.', 0.9, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO scene_live_state (id, session_id, scene_id, version_id, state_type, key, value, confidence, is_tentative, source_scene_id, source_version_id)
            VALUES (?, ?, ?, ?, 'blocking', 'active_zones', 'entry door, kitchen table, hallway threshold', 0.86, 0, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id, scene_id, version_id),
        )
        db.commit()
    return session_id


def cleanup_session(session_id: str) -> None:
    with sqlite3.connect(DB_PATH) as db:
        db.execute("PRAGMA foreign_keys = ON")
        character_ids = [
            row[0]
            for row in db.execute(
                "SELECT character_id FROM session_characters WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        ]
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        if character_ids:
            placeholders = ",".join("?" for _ in character_ids)
            db.execute(
                f"DELETE FROM characters WHERE id IN ({placeholders}) "
                "AND NOT EXISTS (SELECT 1 FROM session_characters WHERE session_characters.character_id = characters.id)",
                character_ids,
            )
        db.commit()


def assert_contains(text: str, needle: str) -> None:
    if needle.lower() not in text.lower():
        raise AssertionError(f"Expected text to contain {needle!r}\n{text}")


def test_memory_pack() -> None:
    session_id = seed_session()
    try:
        context, item_count, pack = format_next_prompt_memory_pack(
            session_id,
            relevance_text="Continue with Mara Memory V3, Iven Memory V3, Joss Memory V3, the brass key, and the apartment door.",
            context_kind="writer",
            limit=5200,
            cache=True,
        )
        if item_count < 12:
            raise AssertionError(f"Expected at least 12 selected memory items, got {item_count}")
        assert_contains(context, "NEXT PROMPT MEMORY PACK v3")
        assert_contains(context, "Mara Memory V3")
        assert_contains(context, "Iven Memory V3")
        assert_contains(context, "Joss Memory V3")
        assert_contains(context, "brass key")
        assert_contains(context, "carried by Iven Memory V3")
        assert_contains(context, "kitchen table")
        assert_contains(context, "damaged trust")
        if not pack.get("sections"):
            raise AssertionError("Memory pack returned no sections.")
        with sqlite3.connect(DB_PATH) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM next_prompt_memory_cache_v3 WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        if count < 1:
            raise AssertionError("Expected memory pack cache row to be written.")
        historical_context, _historical_count, _historical_pack = format_next_prompt_memory_pack(
            session_id,
            relevance_text="Recall where the brass key was hidden beside the old dryer and loose wall tile.",
            context_kind="writer",
            limit=4600,
        )
        assert_contains(historical_context, "hidden behind the loose wall tile beside the old dryer")
    finally:
        cleanup_session(session_id)


def test_local_fallbacks() -> None:
    scene_text = (
        "Mara Test stood beside the west door wearing a red cloak. "
        "Mara Test handed the brass key to Iven Test. "
        "Iven Test set the oil lamp on the altar steps. "
        "Iven Test knew the prince's true name and kept the secret from Joss Test. "
        "Joss Test overheard the whisper from behind the vestry curtain."
    )
    context = {
        "scene_text": scene_text,
        "director_note": "Disposable fallback test in a chapel with a key handoff.",
        "characters": [{"name": "Mara Test"}, {"name": "Iven Test"}, {"name": "Joss Test"}],
    }
    names = fallback_character_names(context)
    character_updates = fallback_character_live_updates(scene_text, names)
    object_updates = fallback_object_updates(scene_text, names)
    scene_updates = fallback_scene_blocking_updates(scene_text, names, "ruined chapel")
    supplemented = supplement_partial_state_payload(context, {"character_updates": [], "scene_updates": [], "object_updates": [], "warnings": []})

    character_keys = {(item["character_name"], item["key"]) for item in character_updates}
    if ("Mara Test", "current_outfit") not in character_keys:
        raise AssertionError(f"Missing fallback outfit update: {character_updates}")
    if ("Mara Test", "room_position") not in character_keys:
        raise AssertionError(f"Missing fallback position update: {character_updates}")
    if ("Iven Test", "known_secrets") not in character_keys:
        raise AssertionError(f"Missing fallback knowledge update: {character_updates}")

    brass_key = next((item for item in object_updates if item.get("object_key") == "brass_key"), None)
    if not brass_key or brass_key.get("owner_character_name") != "Mara Test" or brass_key.get("holder_character_name") != "Iven Test":
        raise AssertionError(f"Missing brass key owner/holder transfer: {object_updates}")
    lamp_locations = [item for item in object_updates if item.get("object_key") == "oil_lamp" and item.get("state_type") == "location"]
    if not lamp_locations:
        raise AssertionError(f"Missing oil lamp placement: {object_updates}")

    scene_keys = {item["key"] for item in scene_updates}
    for key in ("present_characters", "current_location", "active_zones", "hearing_range"):
        if key not in scene_keys:
            raise AssertionError(f"Missing scene blocking key {key}: {scene_updates}")
    if len(supplemented.get("character_updates", [])) < 3 or len(supplemented.get("object_updates", [])) < 2:
        raise AssertionError(f"Supplement did not add enough fallback facts: {supplemented}")


def main() -> None:
    test_memory_pack()
    test_local_fallbacks()
    print("Memory / Character / Place v3 static test passed.")


if __name__ == "__main__":
    main()
