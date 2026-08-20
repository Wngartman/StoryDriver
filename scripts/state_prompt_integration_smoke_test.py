import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import init_db
from tests.live_fixture_cleanup import register_test_session_cleanup

DB_PATH = ROOT / "backend" / "data" / "app.db"
BACKEND_URL = "http://localhost:8001"
SESSION_TITLE = "StoryDriver Prompt State Integration Smoke Test"


def request_json(path: str) -> dict:
    request = urllib.request.Request(f"{BACKEND_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def ensure_character(db: sqlite3.Connection, name: str, role: str, appearance: str) -> str:
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
            "Continuity smoke-test character.",
            appearance,
            "Available for prompt integration smoke testing.",
            appearance,
        ),
    )
    return character_id


def attach_character(db: sqlite3.Connection, session_id: str, character_id: str) -> None:
    row = db.execute(
        "SELECT id FROM session_characters WHERE session_id = ? AND character_id = ?",
        (session_id, character_id),
    ).fetchone()
    if row:
        db.execute(
            "UPDATE session_characters SET is_active = 1 WHERE session_id = ? AND character_id = ?",
            (session_id, character_id),
        )
        return
    db.execute(
        "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
        (str(uuid4()), session_id, character_id),
    )


def archive_prior_test_state(db: sqlite3.Connection, session_id: str) -> None:
    for table in ("character_live_state", "relationship_state", "emotional_memories", "world_live_state", "scene_live_state", "object_state"):
        db.execute(f"UPDATE {table} SET archived = 1 WHERE session_id = ?", (session_id,))
    db.execute("UPDATE plot_threads SET status = 'archived' WHERE session_id = ?", (session_id,))


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

        mara_id = ensure_character(db, "Mara Prompt Smoke", "Scout", "short black hair, scar over left eyebrow")
        elias_id = ensure_character(db, "Elias Prompt Smoke", "Scholar", "tall scholar with tired eyes")
        attach_character(db, session_id, mara_id)
        attach_character(db, session_id, elias_id)

        scene_id = str(uuid4())
        version_id = str(uuid4())
        scene_text = (
            "At camp, Mara treats the wound in her left shoulder while wearing the red cloak she chose after leaving the green cloak behind. "
            "Elias keeps the bronze compass hidden in his satchel and says nothing about it."
        )
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
            VALUES (?, ?, ?, ?, 'continue')
            """,
            (scene_id, session_id, "State prompt integration smoke scene.", scene_text),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, "State prompt integration smoke scene.", scene_text),
        )
        archive_prior_test_state(db, session_id)
        old_scene_id = scene_id
        db.execute(
            """
            INSERT INTO character_live_state (
                id, session_id, character_id, character_name, state_type, key, value,
                confidence, is_tentative, archived, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, 'appearance', 'current_outfit', 'wearing a green cloak', 0.95, 0, 1, ?, ?)
            """,
            (str(uuid4()), session_id, mara_id, "Mara Prompt Smoke", old_scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO character_live_state (
                id, session_id, character_id, character_name, state_type, key, value,
                confidence, is_tentative, archived, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, 'appearance', 'current_outfit', 'wearing a red cloak', 0.97, 0, 0, ?, ?)
            """,
            (str(uuid4()), session_id, mara_id, "Mara Prompt Smoke", scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO character_live_state (
                id, session_id, character_id, character_name, state_type, key, value,
                confidence, is_tentative, archived, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, 'injury', 'injury_left_shoulder', 'left shoulder bandaged after a fresh wound', 0.96, 0, 0, ?, ?)
            """,
            (str(uuid4()), session_id, mara_id, "Mara Prompt Smoke", scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO object_state (
                id, session_id, object_key, name, state_type, value, owner_character_id,
                owner_character_name, confidence, is_tentative, archived, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'bronze_compass', 'bronze compass', 'secret_object', 'hidden in Elias satchel', ?, ?, 0.94, 0, 0, ?, ?)
            """,
            (str(uuid4()), session_id, elias_id, "Elias Prompt Smoke", scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO plot_threads (
                id, session_id, thread_key, title, status, content, confidence,
                is_tentative, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'elias_hides_compass', 'Elias hides the bronze compass', 'active',
                    'Elias is keeping the bronze compass secret from Mara.', 0.91, 0, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO relationship_state (
                id, session_id, character_a_id, character_a_name, character_b_id, character_b_name,
                relationship_type, relationship_key, content, closeness_weight, trust_weight,
                conflict_weight, protective_weight, grief_weight, romantic_weight, family_weight,
                betrayal_weight, respect_weight, fear_weight, emotional_importance, confidence,
                is_tentative, archived, manually_pinned, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, 'close_friend', 'trust_after_compass_secret',
                    'Mara and Elias are close allies, but the hidden compass is beginning to damage trust.',
                    0.72, 0.42, 0.35, 0.3, 0.0, 0.0, 0.0, 0.18, 0.48, 0.0,
                    0.76, 0.93, 0, 0, 0, ?, ?)
            """,
            (str(uuid4()), session_id, mara_id, "Mara Prompt Smoke", elias_id, "Elias Prompt Smoke", scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO emotional_memories (
                id, session_id, memory_type, memory_text, emotional_weight, themes_json,
                related_characters_json, trigger_conditions, cooldown_scenes, confidence,
                is_tentative, archived, manually_pinned, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'betrayal',
                    'Elias hid the bronze compass from Mara; trust remains damaged when secrets or the compass matter.',
                    0.92, '["secret","trust","compass"]', '["Mara Prompt Smoke","Elias Prompt Smoke"]',
                    'Recall when Mara, Elias, secrets, betrayal, or the bronze compass are relevant.', 3,
                    0.96, 0, 0, 1, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO emotional_memories (
                id, session_id, memory_type, memory_text, emotional_weight, themes_json,
                related_characters_json, trigger_conditions, cooldown_scenes, confidence,
                is_tentative, archived, manually_pinned, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'trauma',
                    'A nameless bandit died far from the camp and should not dominate Mara and Elias scenes.',
                    0.18, '["bandit"]', '["Nameless Bandit"]',
                    'Only recall if the nameless bandit is directly relevant.', 8,
                    0.86, 0, 0, 0, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id),
        )
        db.execute(
            """
            INSERT INTO scene_live_state (
                id, session_id, scene_id, version_id, state_type, key, value,
                confidence, is_tentative, archived, source_scene_id, source_version_id
            )
            VALUES (?, ?, ?, ?, 'scene', 'current_location', 'camp where wounds are being treated', 0.95, 0, 0, ?, ?)
            """,
            (str(uuid4()), session_id, scene_id, version_id, scene_id, version_id),
        )
    return session_id


def require_contains(label: str, text: str, needle: str) -> None:
    if needle.lower() not in text.lower():
        raise AssertionError(f"{label} did not include expected text: {needle}")


def main() -> int:
    try:
        health = request_json("/health")
    except Exception as error:
        print(f"Backend is not reachable at {BACKEND_URL}: {error}")
        return 1
    if not health.get("ok"):
        print(f"Backend health is not OK: {health}")
        return 1

    session_id = ensure_test_context()
    register_test_session_cleanup(BACKEND_URL, session_id)
    overview = request_json(f"/sessions/{session_id}/story-state")
    prose_context = overview.get("prompt_context") or ""

    checks = [
        ("prose context", prose_context, "red cloak"),
        ("prose context", prose_context, "left shoulder"),
        ("prose context", prose_context, "bronze compass"),
        ("prose context", prose_context, "camp"),
        ("prose context", prose_context, "trust remains damaged"),
    ]
    for label, text, needle in checks:
        require_contains(label, text, needle)
    if "green cloak" in prose_context.lower():
        raise AssertionError("Archived green cloak leaked into prompt context.")
    if "nameless bandit died" in prose_context.lower():
        raise AssertionError("Low-weight unrelated emotional memory leaked into prose prompt context.")

    print(f"Prompt state integration smoke session: {session_id}")
    print(f"Prompt items included: {overview.get('prompt_item_count')}")
    print("Prose prompt state includes red cloak, shoulder injury, hidden compass, camp location, and relevant trust memory.")
    print("Archived green cloak and low-weight unrelated emotional memory did not leak into prompt context.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"State prompt integration smoke failed: {error}")
        raise SystemExit(1)
