from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
TEMP_ROOT = ROOT / "backend" / "data" / "temp" / "relationship_location_accuracy"
TEMP_DB = TEMP_ROOT / "contract.db"
os.environ["STORYDRIVER_DATA_DIR"] = str(TEMP_ROOT)
os.environ["STORYDRIVER_DB_PATH"] = str(TEMP_DB)
sys.path.insert(0, str(ROOT / "backend"))

from app.database import db_session, init_db
from app.memory.context import build_next_prompt_memory_pack
from app.memory.engine import create_run, merge_state_payload


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def fixture() -> tuple[str, str, str, str]:
    init_db()
    session_id, scene_id, version_one, version_two = (str(uuid4()) for _ in range(4))
    with db_session() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, "Continuity Contract Fixture"))
        for name in ("Mara Vale", "June Park"):
            character_id = str(uuid4())
            db.execute("INSERT INTO characters (id, name, auto_created) VALUES (?, ?, 1)", (character_id, name))
            db.execute(
                "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
                (str(uuid4()), session_id, character_id),
            )
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, 'continue')",
            (scene_id, session_id, "Track the handoff.", "Mara and June work beside the north window."),
        )
        for version_id, index in ((version_one, 1), (version_two, 2)):
            db.execute(
                """
                INSERT INTO scene_versions (
                    id, scene_id, session_id, director_note, generated_text, mode, version_index
                ) VALUES (?, ?, ?, ?, ?, 'continue', ?)
                """,
                (version_id, scene_id, session_id, "Track the handoff.", "Concrete continuity fixture.", index),
            )
    return session_id, scene_id, version_one, version_two


def merge(session_id: str, scene_id: str, version_id: str, payload: dict) -> list[str]:
    run_id = create_run(session_id, scene_id, version_id)
    return merge_state_payload(
        run_id=run_id,
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id,
        payload=payload,
    )


def scalar(sql: str, args: tuple = ()) -> int:
    with closing(sqlite3.connect(TEMP_DB)) as db:
        return int(db.execute(sql, args).fetchone()[0])


def main() -> int:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    if TEMP_DB.exists():
        TEMP_DB.unlink()
    session_id, scene_id, version_one, version_two = fixture()

    first_payload = {
        "character_updates": [
            {"character_name": "Mara Vale", "state_type": "location", "key": "location", "value": "west workshop", "confidence": 0.96},
            {"character_name": "Mara Vale", "state_type": "position", "key": "position", "value": "straps of her pack until they hurt", "confidence": 0.96},
            {"character_name": "June Park", "state_type": "position", "key": "position", "value": "between them and the biting Denver air", "confidence": 0.96},
            {"character_name": "Mara Vale", "state_type": "position", "key": "position", "value": "Mara stands beside the north window", "confidence": 0.96},
            {"character_name": "June Park", "state_type": "location", "key": "location", "value": "west workshop", "confidence": 0.96},
        ],
        "relationship_updates": [
            {"character_a_name": "Mara Vale", "character_b_name": "June Park", "relationship_type": "ally", "content": "active bond", "confidence": 0.98},
            {
                "character_a_name": "Mara Vale",
                "character_b_name": "June Park",
                "relationship_type": "close_friend",
                "relationship_key": "broken_promise",
                "content": "June wants Mara to admit she hid the eviction notice; Mara wants June to trust her plan despite that lie.",
                "trust_weight": 0.42,
                "conflict_weight": 0.78,
                "emotional_importance": 0.86,
                "confidence": 0.98,
            },
        ],
        "scene_updates": [
            {"state_type": "location", "key": "current_location", "value": "west workshop", "confidence": 0.97},
            {"state_type": "presence", "key": "present_characters", "value": "introduced or present", "confidence": 0.97},
        ],
        "object_updates": [
            {
                "object_key": "brass_utility_key",
                "name": "brass utility key",
                "state_type": "ownership",
                "value": "June holds the key after Mara lends it to her.",
                "owner_character_name": "Mara Vale",
                "holder_character_name": "June Park",
                "current_location": "on June's open palm beside the workbench",
                "placement_state": "held",
                "visibility": "visible",
                "importance": 0.9,
                "confidence": 0.98,
            }
        ],
    }
    warnings = merge(session_id, scene_id, version_one, first_payload)
    require(any("generic relationship filler" in item for item in warnings), "generic relationship filler was not rejected")
    require(any("position without concrete physical" in item for item in warnings), "invalid physical position was not rejected")
    require(any("generic presence filler" in item for item in warnings), "generic presence record was not rejected")

    second_payload = {
        "character_updates": [
            {"character_name": "Mara Vale", "state_type": "location", "key": "current_location", "value": "stone courtyard", "confidence": 0.98},
            {"character_name": "Mara Vale", "state_type": "position", "key": "room_position", "value": "Mara waits beside the courtyard's north gate", "confidence": 0.98},
            {"character_name": "June Park", "state_type": "location", "key": "current_location", "value": "stone courtyard", "confidence": 0.98},
            {"character_name": "June Park", "state_type": "position", "key": "room_position", "value": "June stands across from Mara near the dry fountain", "confidence": 0.98},
        ],
        "relationship_updates": [
            {
                "character_a_name": "June Park",
                "character_b_name": "Mara Vale",
                "relationship_type": "close_friend",
                "relationship_key": "partial_repair",
                "content": "Mara finally shows June the eviction notice; June remains angry but agrees to help until dawn.",
                "trust_weight": 0.55,
                "conflict_weight": 0.62,
                "emotional_importance": 0.9,
                "confidence": 0.99,
            }
        ],
        "scene_updates": [
            {"state_type": "location", "key": "current_location", "value": "stone courtyard", "confidence": 0.98},
            {"state_type": "presence", "key": "present_characters", "value": "Mara Vale and June Park are present in the courtyard", "confidence": 0.96},
        ],
        "object_updates": [
            {
                "object_key": "brass_utility_key",
                "name": "brass utility key",
                "state_type": "ownership",
                "value": "June returns the key to Mara.",
                "owner_character_name": "Mara Vale",
                "holder_character_name": "Mara Vale",
                "current_location": "inside Mara's coat pocket",
                "placement_state": "stored",
                "visibility": "concealed",
                "importance": 0.9,
                "confidence": 0.99,
            }
        ],
    }
    merge(session_id, scene_id, version_two, second_payload)
    merge(
        session_id,
        scene_id,
        version_two,
        {
            "object_updates": [
                {
                    "object_key": "key",
                    "name": "key",
                    "state_type": "possession",
                    "value": "carried by Mara Vale",
                    "owner_character_name": "",
                    "holder_character_name": "Mara Vale",
                    "placement_state": "held",
                    "confidence": 0.98,
                }
            ]
        },
    )
    merge(
        session_id,
        scene_id,
        version_two,
        {
            "object_updates": [
                {
                    "object_key": "key_tight",
                    "name": "key tight",
                    "state_type": "possession",
                    "value": "Mara held the key tight.",
                    "holder_character_name": "Mara Vale",
                    "placement_state": "held",
                    "confidence": 0.98,
                }
            ]
        },
    )
    relationship_events_before = scalar("SELECT count(*) FROM relationship_state_events WHERE session_id = ?", (session_id,))
    merge(session_id, scene_id, version_two, second_payload)
    relationship_events_after = scalar("SELECT count(*) FROM relationship_state_events WHERE session_id = ?", (session_id,))

    require(scalar("SELECT count(*) FROM relationship_state WHERE session_id=? AND archived=0 AND disabled=0", (session_id,)) == 1, "relationship pair is not canonical")
    require(relationship_events_before == relationship_events_after == 2, "unchanged relationship update created a duplicate event")
    require(scalar("SELECT count(*) FROM character_live_state WHERE session_id=? AND key='current_location' AND archived=0 AND disabled=0", (session_id,)) == 2, "character current locations are not canonical")
    require(scalar("SELECT count(*) FROM character_live_state WHERE session_id=? AND key='room_position' AND archived=0 AND disabled=0", (session_id,)) == 2, "valid room positions were lost")
    require(scalar("SELECT count(*) FROM character_live_state WHERE session_id=? AND lower(value) LIKE '%straps of her pack%' AND archived=0", (session_id,)) == 0, "invalid position survived validation")
    require(scalar("SELECT count(*) FROM character_live_state WHERE session_id=? AND lower(value) LIKE '%biting denver air%' AND archived=0", (session_id,)) == 0, "abstract position survived validation")
    require(scalar("SELECT count(*) FROM scene_live_state WHERE session_id=? AND key='current_location' AND archived=0 AND disabled=0", (session_id,)) == 1, "scene has multiple active locations")
    require(scalar("SELECT count(*) FROM object_state WHERE session_id=? AND object_key='brass_utility_key' AND archived=0 AND disabled=0", (session_id,)) == 1, "object has duplicate current rows")
    require(scalar("SELECT count(*) FROM object_state WHERE session_id=? AND object_key='key' AND archived=0 AND disabled=0", (session_id,)) == 0, "short object alias created a duplicate row")
    require(scalar("SELECT count(*) FROM object_state WHERE session_id=? AND object_key='key_tight' AND archived=0 AND disabled=0", (session_id,)) == 0, "trailing object modifier created a duplicate row")

    with closing(sqlite3.connect(TEMP_DB)) as db:
        db.row_factory = sqlite3.Row
        item = db.execute("SELECT * FROM object_state WHERE session_id=? AND object_key='brass_utility_key' AND archived=0", (session_id,)).fetchone()
        require(item["owner_character_name"] == "Mara Vale", "object owner changed during holder transfer")
        require(item["holder_character_name"] == "Mara Vale", "object holder transfer was not recorded")
        require(item["current_location"] == "inside Mara's coat pocket", "object location was not recorded")

    started = time.perf_counter()
    pack = build_next_prompt_memory_pack(
        session_id,
        relevance_text="Mara asks June about the brass utility key and the eviction notice in the courtyard.",
        context_kind="writer",
    )
    retrieval_ms = (time.perf_counter() - started) * 1000
    rendered = pack["rendered_context"]
    require(retrieval_ms < 100, f"memory retrieval exceeded 100 ms: {retrieval_ms:.2f} ms")
    require("eviction notice" in rendered.lower(), "concrete relationship history was not retrieved")
    require("brass utility key" in rendered.lower(), "relevant object was not retrieved")
    require("straps of her pack" not in rendered.lower(), "rejected position leaked into prompt memory")
    require(pack["prompt_chars"] <= 8000, "writer memory packet exceeded its bound")

    print(f"PASS: canonical relationship, location, blocking, and object contracts ({retrieval_ms:.2f} ms retrieval; {pack['prompt_chars']} prompt chars).")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    finally:
        shutil.rmtree(TEMP_ROOT)
    raise SystemExit(exit_code)
