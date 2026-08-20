from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
import time
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import init_db  # noqa: E402


DB_PATH = ROOT / "backend" / "data" / "app.db"
BACKEND_URL = "http://localhost:8001"
SESSION_TITLE = "StoryDriver State V2 Continuity Static Test"


def request_json(path: str, method: str = "GET") -> dict:
    request = urllib.request.Request(f"{BACKEND_URL}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


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
            "Synthetic continuity-test character.",
            "Adult, clearly described for continuity testing.",
            "Available for Story State v2 static test.",
        ),
    )
    db.execute(
        "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
        (str(uuid4()), session_id, character_id),
    )
    return character_id


def seed_state() -> tuple[str, tuple[str, str]]:
    init_db()
    session_id = str(uuid4())
    scene_id = str(uuid4())
    version_id = str(uuid4())
    run_id = str(uuid4())
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, SESSION_TITLE))
        mara_id = insert_character(db, session_id, "Mara State V2", "Witness")
        iven_id = insert_character(db, session_id, "Iven State V2", "Holder")
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, 'continue')",
            (
                scene_id,
                session_id,
                "Story State v2 continuity static test.",
                "Mara stood beside the apartment door while Iven held the silver key near the kitchen table.",
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
                "Story State v2 continuity static test.",
                "Mara stood beside the apartment door while Iven held the silver key near the kitchen table.",
            ),
        )
        db.execute(
            "INSERT INTO story_state_runs (id, session_id, scene_id, version_id, status, raw_response) VALUES (?, ?, ?, ?, 'completed', '{}')",
            (run_id, session_id, scene_id, version_id),
        )
        character_rows = [
            (mara_id, "Mara State V2", "location", "current_location", "modern apartment living room", 0.94),
            (mara_id, "Mara State V2", "blocking", "room_position", "beside the apartment door", 0.93),
            (mara_id, "Mara State V2", "blocking", "room_position", "by the kitchen table", 0.91),
            (mara_id, "Mara State V2", "appearance", "current_outfit", "wearing a black coat over a gray shirt", 0.9),
            (mara_id, "Mara State V2", "injury", "injury_left_hand", "fresh cut bandaged with white gauze", 0.96),
            (mara_id, "Mara State V2", "knowledge", "known_secrets", "knows Iven lied about losing the silver key", 0.92),
            (iven_id, "Iven State V2", "location", "current_location", "modern apartment living room", 0.94),
            (iven_id, "Iven State V2", "blocking", "room_position", "near the kitchen table holding the silver key", 0.95),
            (iven_id, "Iven State V2", "emotion", "emotional_state", "guarded and guilty", 0.89),
        ]
        for character_id, name, state_type, key, value, confidence in character_rows:
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
                id, session_id, object_key, name, state_type, value,
                owner_character_id, owner_character_name, confidence, is_tentative,
                source_scene_id, source_version_id
            )
            VALUES (?, ?, 'silver_key', 'silver key', 'ownership', 'transferred from Mara State V2 to Iven State V2', ?, ?, 0.95, 0, ?, ?)
            """,
            (str(uuid4()), session_id, iven_id, "Iven State V2", scene_id, version_id),
        )
        for value in ("on the kitchen table", "inside Mara's coat pocket"):
            db.execute(
                """
                INSERT INTO object_state (
                    id, session_id, object_key, name, state_type, value,
                    confidence, is_tentative, source_scene_id, source_version_id
                )
                VALUES (?, ?, 'silver_key', 'silver key', 'location', ?, 0.9, 0, ?, ?)
                """,
                (str(uuid4()), session_id, value, scene_id, version_id),
            )
        db.execute(
            """
            INSERT INTO relationship_state (
                id, session_id, character_a_id, character_a_name, character_b_id, character_b_name,
                relationship_type, relationship_key, content, trust_weight, conflict_weight,
                betrayal_weight, emotional_importance, confidence, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, 'ally', 'betrayal_key_lie',
                    'Mara knows Iven lied about the silver key, creating a fresh trust wound.',
                    0.28, 0.84, 0.86, 0.9, 0.94, ?, ?)
            """,
            (str(uuid4()), session_id, mara_id, "Mara State V2", iven_id, "Iven State V2", scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO emotional_memories (
                id, session_id, memory_type, memory_text, emotional_weight, themes_json,
                related_characters_json, trigger_conditions, cooldown_scenes, confidence,
                run_id, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'betrayal', ?, 0.91, ?, ?, 'Recall when trust, keys, lies, or apartment confrontation are relevant.', 2, 0.93, ?, ?, ?)
            """,
            (
                str(uuid4()),
                session_id,
                "Mara remembers that Iven lied about the silver key when trust mattered.",
                json.dumps(["betrayal", "trust", "object ownership"]),
                json.dumps(["Mara State V2", "Iven State V2"]),
                run_id,
                scene_id,
                version_id,
            ),
        )
        db.execute(
            """
            INSERT INTO scene_live_state (
                id, session_id, scene_id, version_id, state_type, key, value,
                confidence, is_tentative, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, 'blocking', 'present_characters', 'Mara State V2 and Iven State V2 are present; both can hear each other.', 0.93, 0, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id, scene_id, version_id),
        )
    return session_id, (mara_id, iven_id)


def delete_fixture(session_id: str, character_ids: tuple[str, str]) -> None:
    result = request_json(f"/sessions/{session_id}?permanent=true", method="DELETE")
    job_id = result.get("job_id") or result.get("id")
    if not job_id:
        raise RuntimeError("Fixture deletion returned no job ID.")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = request_json(f"/sessions/delete-jobs/{job_id}")
        if job.get("status") == "completed":
            break
        if job.get("status") in {"failed", "cancelled"}:
            raise RuntimeError(f"Fixture deletion failed: {job}")
        time.sleep(0.25)
    else:
        raise RuntimeError("Fixture deletion timed out.")
    with sqlite3.connect(DB_PATH) as db:
        placeholders = ",".join("?" for _ in character_ids)
        linked = db.execute(
            f"SELECT COUNT(*) FROM session_characters WHERE character_id IN ({placeholders})",
            character_ids,
        ).fetchone()[0]
        if linked:
            raise RuntimeError("Refusing to remove synthetic characters that remain linked to a story.")
        db.execute(f"DELETE FROM characters WHERE id IN ({placeholders})", character_ids)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    health = request_json("/health")
    require(bool(health.get("ok")), "Backend health is not OK.")
    session_id, character_ids = seed_state()
    try:
        overview = request_json(f"/sessions/{session_id}/story-state")
        prompt_context = (overview.get("prompt_context") or "").lower()
        conflicts = overview.get("conflicts") or []
        live = overview.get("character_live_state") or []
        objects = overview.get("objects") or []
        relationships = overview.get("relationships") or []
        memories = overview.get("emotional_memories") or []

        require(any(item.get("key") == "room_position" for item in live), "Room position state missing.")
        require(any((item.get("confidence") or 0) >= 0.9 for item in live), "Explicit character facts are not high confidence.")
        require(any(item.get("owner_character_name") == "Iven State V2" for item in objects), "Object owner did not persist.")
        require(any((item.get("confidence") or 0) >= 0.9 for item in objects), "Explicit object fact is not high confidence.")
        require(any((item.get("betrayal_weight") or 0) >= 0.8 for item in relationships), "Betrayal relationship weight missing.")
        require(any((item.get("confidence") or 0) >= 0.9 for item in memories), "Emotional memory confidence is too low.")
        require(any("multiple active" in (item.get("message") or "").lower() for item in conflicts), "Expected conflict not detected.")
        require("position:" in prompt_context or "room position" in prompt_context, "Prompt context omitted position.")
        require("holder iven state v2" in prompt_context or "owner: iven state v2" in prompt_context, "Prompt context omitted object holder.")
        require("betrayal" in prompt_context or "trust wound" in prompt_context, "Prompt context omitted relationship/emotional stakes.")

        print(f"Story State v2 continuity static test session: {session_id}")
        print(f"Live state rows: {len(live)}")
        print(f"Object rows: {len(objects)}")
        print(f"Relationship rows: {len(relationships)}")
        print(f"Conflict rows: {len(conflicts)}")
        print("Spatial continuity, object ownership, relationship stakes, confidence, conflicts, and prompt recall passed.")
        return 0
    finally:
        delete_fixture(session_id, character_ids)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[FAIL] {error}")
        raise SystemExit(1)
