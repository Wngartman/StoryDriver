from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import DB_PATH, init_db
from app.memory.engine import format_story_state_for_prompt
from tests.live_fixture_cleanup import register_test_session_cleanup


SESSION_TITLE = "StoryDriver Relationship Salience Smoke Test"


def ensure_character(db: sqlite3.Connection, name: str, role: str) -> str:
    row = db.execute("SELECT id FROM characters WHERE name = ?", (name,)).fetchone()
    if row:
        db.execute("UPDATE characters SET auto_created = 1 WHERE id = ?", (row["id"],))
        return row["id"]
    character_id = str(uuid4())
    db.execute(
        """
        INSERT INTO characters (id, name, role, personality, appearance, current_state, image_prompt, auto_created)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            character_id,
            name,
            role,
            "Relationship salience smoke-test character.",
            "grounded medieval fantasy appearance",
            "Available for relationship salience testing.",
            "grounded medieval fantasy appearance",
        ),
    )
    return character_id


def attach_character(db: sqlite3.Connection, session_id: str, character_id: str) -> None:
    row = db.execute(
        "SELECT id FROM session_characters WHERE session_id = ? AND character_id = ?",
        (session_id, character_id),
    ).fetchone()
    if row:
        db.execute("UPDATE session_characters SET is_active = 1 WHERE id = ?", (row["id"],))
        return
    db.execute(
        "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
        (str(uuid4()), session_id, character_id),
    )


def clear_test_state(db: sqlite3.Connection, session_id: str) -> None:
    for table in ("character_live_state", "relationship_state", "emotional_memories", "world_live_state", "scene_live_state", "object_state"):
        db.execute(f"UPDATE {table} SET archived = 1 WHERE session_id = ?", (session_id,))
    db.execute("UPDATE plot_threads SET status = 'archived', disabled = 0 WHERE session_id = ?", (session_id,))


def seed_relationship(
    db: sqlite3.Connection,
    session_id: str,
    a_id: str,
    a_name: str,
    b_id: str,
    b_name: str,
    relationship_type: str,
    content: str,
    *,
    family: float = 0,
    closeness: float = 0,
    trust: float = 0,
    conflict: float = 0,
    grief: float = 0,
    betrayal: float = 0,
    protective: float = 0,
    romantic: float = 0,
    importance: float = 0,
) -> None:
    db.execute(
        """
        INSERT INTO relationship_state (
            id, session_id, character_a_id, character_a_name, character_b_id, character_b_name,
            relationship_type, relationship_key, content, closeness_weight, trust_weight,
            conflict_weight, protective_weight, grief_weight, romantic_weight, family_weight,
            betrayal_weight, respect_weight, fear_weight, emotional_importance, confidence,
            is_tentative, archived, manually_pinned
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'relationship', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0.96, 0, 0, 0)
        """,
        (
            str(uuid4()),
            session_id,
            a_id,
            a_name,
            b_id,
            b_name,
            relationship_type,
            content,
            closeness,
            trust,
            conflict,
            protective,
            grief,
            romantic,
            family,
            betrayal,
            importance,
        ),
    )


def seed_memory(
    db: sqlite3.Connection,
    session_id: str,
    memory_type: str,
    text: str,
    weight: float,
    themes: str,
    related: str,
    trigger: str,
    *,
    pinned: bool = False,
) -> None:
    db.execute(
        """
        INSERT INTO emotional_memories (
            id, session_id, memory_type, memory_text, emotional_weight, themes_json,
            related_characters_json, trigger_conditions, cooldown_scenes, confidence,
            is_tentative, archived, manually_pinned
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 3, 0.96, 0, 0, ?)
        """,
        (str(uuid4()), session_id, memory_type, text, weight, themes, related, trigger, 1 if pinned else 0),
    )


def ensure_test_context() -> str:
    init_db()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        session = db.execute("SELECT id FROM sessions WHERE title = ?", (SESSION_TITLE,)).fetchone()
        if session:
            session_id = session["id"]
        else:
            session_id = str(uuid4())
            db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, SESSION_TITLE))
        clear_test_state(db, session_id)

        elara_id = ensure_character(db, "Elara Salience Smoke", "Oldest sister")
        lyra_id = ensure_character(db, "Lyra Salience Smoke", "Youngest sister")
        mara_id = ensure_character(db, "Mara Salience Smoke", "Scout")
        elias_id = ensure_character(db, "Elias Salience Smoke", "Scholar ally")
        rowan_id = ensure_character(db, "Rowan Salience Smoke", "Widower")
        mira_id = ensure_character(db, "Mira Salience Smoke", "Lost spouse")
        for character_id in (elara_id, lyra_id, mara_id, elias_id, rowan_id, mira_id):
            attach_character(db, session_id, character_id)

        seed_relationship(
            db,
            session_id,
            elara_id,
            "Elara Salience Smoke",
            lyra_id,
            "Lyra Salience Smoke",
            "sibling",
            "Elara and Lyra are sisters with a very high sibling bond.",
            family=0.96,
            closeness=0.92,
            protective=0.82,
            grief=0.9,
            importance=0.95,
        )
        seed_relationship(
            db,
            session_id,
            mara_id,
            "Mara Salience Smoke",
            elias_id,
            "Elias Salience Smoke",
            "betrayer",
            "Elias betrayed Mara by hiding the bronze compass; trust remains damaged.",
            trust=0.12,
            conflict=0.7,
            betrayal=0.9,
            importance=0.86,
        )
        seed_relationship(
            db,
            session_id,
            rowan_id,
            "Rowan Salience Smoke",
            mira_id,
            "Mira Salience Smoke",
            "spouse",
            "Rowan's spouse Mira died before the current journey.",
            closeness=0.88,
            grief=0.93,
            romantic=0.92,
            importance=0.94,
        )

        seed_memory(
            db,
            session_id,
            "grief",
            "Lyra's death remains a major grief for Elara, especially around family, home, and child rescue.",
            0.96,
            '["family","home","child","rescue","grief"]',
            '["Elara Salience Smoke","Lyra Salience Smoke"]',
            "Recall when Elara faces family loss, children in danger, home, or rescue.",
        )
        seed_memory(
            db,
            session_id,
            "grief",
            "The sisters' parents died when they were young, shaping their protectiveness toward abandoned children.",
            0.9,
            '["parent","family","children","rescue"]',
            '["Elara Salience Smoke","Lyra Salience Smoke"]',
            "Recall for family, childhood, parent loss, or child rescue scenes.",
        )
        seed_memory(
            db,
            session_id,
            "betrayal",
            "Elias hid the bronze compass from Mara; trust remains damaged when secrets or the compass matter.",
            0.9,
            '["secret","trust","compass","betrayal"]',
            '["Mara Salience Smoke","Elias Salience Smoke"]',
            "Recall when secrets, betrayal, or the bronze compass are relevant.",
            pinned=True,
        )
        seed_memory(
            db,
            session_id,
            "trauma",
            "A nameless bandit died in a ditch; this low-weight death should not dominate unrelated scenes.",
            0.15,
            '["bandit"]',
            '["Nameless Bandit"]',
            "Only recall if this exact bandit matters.",
        )
    return session_id


def require(text: str, needle: str, label: str) -> None:
    if needle.lower() not in text.lower():
        raise AssertionError(f"{label} missing expected text: {needle}")


def forbid(text: str, needle: str, label: str) -> None:
    if needle.lower() in text.lower():
        raise AssertionError(f"{label} included unwanted text: {needle}")


def main() -> int:
    session_id = ensure_test_context()
    register_test_session_cleanup("http://localhost:8001", session_id)
    rescue_context, rescue_count = format_story_state_for_prompt(
        session_id,
        relevance_text="Elara Salience Smoke hears that a child has been captured and must decide whether to attempt a rescue near home.",
    )
    travel_context, _travel_count = format_story_state_for_prompt(
        session_id,
        relevance_text="The party follows a muddy road through quiet rain, counting supplies and avoiding broken cart ruts.",
    )
    require(rescue_context, "sisters with a very high sibling bond", "rescue prose context")
    require(rescue_context, "Lyra's death remains a major grief", "rescue prose context")
    require(rescue_context, "parents died", "rescue prose context")
    require(rescue_context, "trust remains damaged", "manual pinned betrayal context")
    forbid(rescue_context, "nameless bandit died", "rescue prose context")
    forbid(travel_context, "Lyra's death remains a major grief", "unrelated travel context")
    forbid(travel_context, "nameless bandit died", "unrelated travel context")
    print(f"Relationship salience smoke session: {session_id}")
    print(f"Rescue prompt items included: {rescue_count}")
    print("High-weight family grief recalled when relevant; low-weight bandit death stayed out.")
    print("Pinned betrayal memory appears in relevant prose context.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Relationship salience smoke failed: {error}")
        raise SystemExit(1)
