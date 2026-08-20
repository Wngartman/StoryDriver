from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import db_session, init_db  # noqa: E402
from app.generation.prompt_builder import build_scene_prompt  # noqa: E402
from app.memory.foundation import (  # noqa: E402
    apply_foundation_to_story,
    foundation_from_manual_inputs,
    foundation_pronunciation_entries,
    get_story_foundation,
    normalize_foundation,
    render_foundation_projection,
    save_story_foundation,
)
from app.memory.engine import build_extraction_prompt  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def create_session(title: str) -> str:
    session_id = str(uuid4())
    with db_session() as db:
        db.execute(
            "INSERT INTO sessions (id, title, title_source) VALUES (?, ?, 'user_set')",
            (session_id, title),
        )
    return session_id


def cleanup(sessions: list[str], characters: list[str]) -> None:
    with db_session() as db:
        for session_id in sessions:
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        for character_id in characters:
            db.execute(
                """
                DELETE FROM characters
                WHERE id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM session_characters
                    WHERE session_characters.character_id = characters.id
                  )
                """,
                (character_id,),
            )


def attached_character_rows(session_id: str, name: str | None = None) -> list[dict]:
    query = """
        SELECT c.*
        FROM session_characters sc
        JOIN characters c ON c.id = sc.character_id
        WHERE sc.session_id = ?
    """
    params: list[str] = [session_id]
    if name:
        query += " AND lower(c.name) = lower(?)"
        params.append(name)
    with db_session() as db:
        rows = db.execute(query, params).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def main() -> int:
    init_db()
    sessions: list[str] = []
    generated_characters: list[str] = []
    try:
        note = "Modern present-day first chapter about three adult sisters rebuilding trust after a failed rescue."
        foundation = foundation_from_manual_inputs(director_note=note, manual_characters=[], world_notes={})
        characters = foundation["characters"]
        assert_true(len(characters) >= 3, "three-character setup should produce at least three characters")
        assert_true(len({item["name"] for item in characters}) == len(characters), "generated characters should have distinct names")
        assert_true(len({item["personality"] for item in characters}) >= 3, "generated characters should have distinct personalities")
        assert_true(foundation["overview"]["genre"] == "Modern", "modern note should produce modern genre")

        explicit = foundation_from_manual_inputs(
            director_note="Create a modern first chapter about Ada, a red-haired mechanic, and two other adult women.",
            manual_characters=[],
            world_notes={},
        )
        ada = next((item for item in explicit["characters"] if item["name"] == "Ada"), None)
        assert_true(ada is not None, "explicit character name should be preserved")
        assert_true("mechanic" in ada["role"], "explicit role should be preserved")
        assert_true("red-haired" in ada["appearance"], "explicit appearance trait should be preserved")

        fantasy = foundation_from_manual_inputs(
            director_note="Fantasy first chapter in a sword kingdom with no magic.",
            manual_characters=[],
            world_notes={},
        )
        scifi = foundation_from_manual_inputs(
            director_note="Sci-fi first chapter on an orbital colony with android dock crews.",
            manual_characters=[],
            world_notes={},
        )
        assert_true(fantasy["overview"]["genre"] == "Fantasy", "fantasy note should classify as fantasy")
        assert_true(scifi["overview"]["genre"] == "Science fiction", "sci-fi note should classify as science fiction")
        assert_true(fantasy["characters"][0]["name"] != scifi["characters"][0]["name"], "genre families should use different seed names")

        session_a = create_session("Foundation Static A")
        session_b = create_session("Foundation Static B")
        sessions.extend([session_a, session_b])
        mara_foundation = normalize_foundation(
            {
                "overview": {"premise": "Two separate stories both use Mara.", "genre": "Modern"},
                "characters": [{"name": "Mara", "role": "lead", "appearance": "adult with a blue coat", "personality": "guarded"}],
                "relationships": [],
                "world": {},
                "opening_scope": {},
            },
            director_note="Modern story about Mara.",
        )
        apply_foundation_to_story(session_a, mara_foundation)
        apply_foundation_to_story(session_b, mara_foundation)
        mara_a = attached_character_rows(session_a, "Mara")[0]
        mara_b = attached_character_rows(session_b, "Mara")[0]
        generated_characters.extend([mara_a["id"], mara_b["id"]])
        assert_true(mara_a["id"] != mara_b["id"], "same generated name in different stories should not reuse a global card")

        manual_session = create_session("Foundation Manual Preserve")
        sessions.append(manual_session)
        manual_character_id = str(uuid4())
        generated_characters.append(manual_character_id)
        with db_session() as db:
            db.execute(
                """
                INSERT INTO characters (id, name, appearance, role, auto_created)
                VALUES (?, 'Ada', 'manual scar over left eyebrow', '', 0)
                """,
                (manual_character_id,),
            )
            db.execute(
                "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
                (str(uuid4()), manual_session, manual_character_id),
            )
        manual_foundation = normalize_foundation(
            {
                "overview": {"premise": "Manual wins", "genre": "Modern"},
                "characters": [{"name": "Ada", "role": "mechanic", "appearance": "generated blue coat", "personality": "brisk"}],
                "relationships": [],
                "world": {"rules": ["manual data wins"]},
                "opening_scope": {},
            },
            director_note="Ada is a mechanic.",
        )
        apply_foundation_to_story(manual_session, manual_foundation)
        manual_after = attached_character_rows(manual_session, "Ada")[0]
        assert_true(manual_after["appearance"] == "manual scar over left eyebrow", "existing manual appearance must not be overwritten")
        assert_true(manual_after["role"] == "mechanic", "empty manual fields may receive suggested values")

        save_story_foundation(
            session_id=manual_session,
            foundation=manual_foundation,
            source_director_note="Ada is pronounced AY-duh.",
            source_map={"model_paths": sorted(["overview", "characters"]), "manual_paths": []},
            locked_paths=["characters"],
            status="ready",
        )
        saved = get_story_foundation(manual_session)
        assert_true(saved is not None and saved["locked_paths"] == ["characters"], "locked paths should persist")
        projection = render_foundation_projection(saved["foundation"])
        assert_true("STORY FOUNDATION / CHARACTER BIBLE" in projection, "projection should include foundation heading")
        assert_true(len(projection) < 6000, "prompt projection should stay compact")
        assert_true(len(json.dumps(saved["foundation"])) < 24000, "foundation JSON should stay compact")

        prompt = build_scene_prompt(
            session_id=manual_session,
            director_note="Open with Ada arriving late.",
            mode="continue",
            recent_scenes=[],
            mark_state_used=False,
        )
        assert_true("STORY FOUNDATION / CHARACTER BIBLE" in prompt, "prose prompt should include foundation projection")

        extraction_prompt = build_extraction_prompt(
            {
                "scene_text": "Ada wiped rain from her coat.",
                "director_note": "Open with Ada.",
                "version_id": None,
                "story_foundation": projection,
                "characters": [],
                "world_notes": {},
                "summary": "",
                "live_state": [],
                "relationships": [],
                "emotional_memories": [],
                "objects": [],
                "plot_threads": [],
            }
        )
        assert_true("story_foundation_base_identity" in extraction_prompt, "extraction prompt should include foundation context")
        assert_true("do not extract it as a live-state update" in extraction_prompt, "extraction prompt should protect base identity")

        pronunciation_foundation = normalize_foundation(
            {
                "overview": {"premise": "Name test", "genre": "Fantasy"},
                "characters": [{"name": "Aelwyn", "pronunciation": "ALE-win", "role": "lead"}],
                "relationships": [],
                "world": {},
                "opening_scope": {},
            },
            director_note="Fantasy story about Aelwyn.",
        )

        closed_cast = normalize_foundation(
            {
                "characters": [
                    {"name": "Elena", "role": "partner"},
                    {"name": "Noor", "role": "partner"},
                    {"name": "Ari Vale", "role": "silent observer"},
                ],
                "relationships": [
                    {"characters": ["Elena", "Noor"], "type": "partners"},
                    {"characters": ["Ari Vale", "Elena"], "type": "observer"},
                ],
            },
            director_note="Write a relationship scene between Elena and Noor at home.",
            manual_characters=[],
            world_notes={},
        )
        closed_names = [character["name"] for character in closed_cast["characters"]]
        assert_true(closed_names == ["Elena", "Noor"], f"closed foundation cast leaked extra characters: {closed_names}")
        assert_true(
            all("Ari" not in " ".join(relation.get("characters", [])) for relation in closed_cast["relationships"]),
            "closed foundation cast retained an unauthorized relationship",
        )
        save_story_foundation(
            session_id=session_b,
            foundation=pronunciation_foundation,
            source_director_note="",
            source_map={"model_paths": sorted(["characters"]), "manual_paths": []},
            locked_paths=[],
            status="ready",
        )
        pronunciation_entries = foundation_pronunciation_entries(session_b)
        assert_true(pronunciation_entries and pronunciation_entries[0]["story_id"] == session_b, "pronunciation hints should be story-scoped")

        print("story_foundation_static_test: ok")
        return 0
    finally:
        cleanup(sessions, generated_characters)


if __name__ == "__main__":
    sys.exit(main())
