import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from tests.live_fixture_cleanup import register_test_session_cleanup


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "backend" / "data" / "app.db"
BACKEND_URL = "http://localhost:8001"
SESSION_TITLE = "StoryDriver State Review Smoke Test"


def request_json(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{BACKEND_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def ensure_character(db: sqlite3.Connection, name: str) -> str:
    row = db.execute("SELECT id FROM characters WHERE name = ?", (name,)).fetchone()
    if row:
        db.execute("UPDATE characters SET auto_created = 1 WHERE id = ?", (row["id"],))
        return row["id"]
    character_id = str(uuid4())
    db.execute(
        """
        INSERT INTO characters (id, name, role, personality, appearance, current_state, image_prompt, auto_created)
        VALUES (?, ?, 'State review smoke character', 'Practical and continuity-sensitive.',
                'dark hair, weathered face, practical travel clothes',
                'Available for Story State review smoke testing.',
                'grounded cinematic fantasy character', 1)
        """,
        (character_id, name),
    )
    return character_id


def ensure_context() -> dict:
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        session = db.execute("SELECT id FROM sessions WHERE title = ?", (SESSION_TITLE,)).fetchone()
        if session:
            session_id = session["id"]
        else:
            session_id = str(uuid4())
            db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, SESSION_TITLE))

        mara_id = ensure_character(db, "Mara Review Smoke")
        row = db.execute(
            "SELECT id FROM session_characters WHERE session_id = ? AND character_id = ?",
            (session_id, mara_id),
        ).fetchone()
        if row:
            db.execute("UPDATE session_characters SET is_active = 1 WHERE id = ?", (row["id"],))
        else:
            db.execute(
                "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
                (str(uuid4()), session_id, mara_id),
            )

        scene_id = str(uuid4())
        version_id = str(uuid4())
        scene_text = "Mara tests the state review tools at camp while carrying a small brass token."
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, 'continue')",
            (scene_id, session_id, "State review smoke.", scene_text),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, "State review smoke.", scene_text),
        )
        for table in ("character_live_state", "relationship_state", "world_live_state", "scene_live_state", "object_state"):
            db.execute(f"UPDATE {table} SET archived = 1 WHERE session_id = ?", (session_id,))
        db.execute("UPDATE plot_threads SET status = 'archived' WHERE session_id = ?", (session_id,))

        edit_item_id = str(uuid4())
        disabled_item_id = str(uuid4())
        conflict_a_id = str(uuid4())
        conflict_b_id = str(uuid4())
        archive_item_id = str(uuid4())
        object_id = str(uuid4())
        undo_run_id = str(uuid4())
        undo_item_id = str(uuid4())
        db.execute(
            """
            INSERT INTO story_state_runs (id, session_id, scene_id, version_id, status, completed_at)
            VALUES (?, ?, ?, ?, 'completed', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (undo_run_id, session_id, scene_id, version_id),
        )
        character_rows = [
            (edit_item_id, "appearance", "visual_marker_smoke", "wearing a plain brown cloak", 0.8, 0, 0, "ok"),
            (disabled_item_id, "goal", "guard_goal_smoke", "guard the south gate", 0.83, 0, 0, "ok"),
            (conflict_a_id, "appearance", "current_outfit", "wearing a black cloak", 0.76, 0, 0, "ok"),
            (conflict_b_id, "appearance", "current_outfit", "wearing a white cloak", 0.74, 0, 0, "ok"),
            (archive_item_id, "appearance", "stale_outfit_smoke", "wearing an old green cloak", 0.82, 0, 0, "ok"),
            (undo_item_id, "inventory", "temporary_torch_smoke", "carrying a temporary torch", 0.78, 0, 0, "ok"),
        ]
        for item_id, state_type, key, value, confidence, disabled, manual_override, review_status in character_rows:
            db.execute(
                """
                INSERT INTO character_live_state (
                    id, session_id, character_id, character_name, state_type, key, value,
                    confidence, is_tentative, archived, disabled, manual_override, review_status,
                    source_scene_id, source_version_id
                )
                VALUES (?, ?, ?, 'Mara Review Smoke', ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    session_id,
                    mara_id,
                    state_type,
                    key,
                    value,
                    confidence,
                    disabled,
                    manual_override,
                    review_status,
                    scene_id,
                    version_id,
                ),
            )
        db.execute(
            """
            INSERT INTO object_state (
                id, session_id, object_key, name, state_type, value, owner_character_id,
                owner_character_name, confidence, is_tentative, archived, disabled,
                manual_override, review_status, source_scene_id, source_version_id
            )
            VALUES (?, ?, 'brass_token_smoke', 'brass token', 'inventory', 'kept in Mara pocket',
                    ?, 'Mara Review Smoke', 0.84, 0, 0, 0, 0, 'ok', ?, ?)
            """,
            (object_id, session_id, mara_id, scene_id, version_id),
        )
    return {
        "session_id": session_id,
        "edit_item_id": edit_item_id,
        "disabled_item_id": disabled_item_id,
        "archive_item_id": archive_item_id,
        "undo_run_id": undo_run_id,
        "undo_item_id": undo_item_id,
        "object_id": object_id,
    }


def overview(session_id: str) -> dict:
    return request_json(f"/sessions/{session_id}/story-state")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    health = request_json("/health")
    require(health.get("ok"), f"Backend health not OK: {health}")
    ids = ensure_context()
    session_id = ids["session_id"]
    register_test_session_cleanup(BACKEND_URL, session_id)

    request_json(
        f"/story-state/items/character/{ids['edit_item_id']}",
        method="PATCH",
        payload={"value": "wearing a silver cloak", "confidence": 0.88, "manual_override": True},
    )
    state = overview(session_id)
    prompt_context = state.get("prompt_context") or ""
    require("silver cloak" in prompt_context.lower(), "Manual edit did not enter prose prompt context.")
    edited = next(item for item in state["character_live_state"] if item["id"] == ids["edit_item_id"])
    require(edited.get("manual_override"), "Manual edit did not mark manual_override.")

    request_json(f"/story-state/items/character/{ids['disabled_item_id']}/disable", method="POST")
    state = overview(session_id)
    require("guard the south gate" not in (state.get("prompt_context") or "").lower(), "Disabled state leaked into prompt context.")

    request_json(f"/story-state/items/character/{ids['disabled_item_id']}/restore", method="POST")
    state = overview(session_id)
    require("guard the south gate" in (state.get("prompt_context") or "").lower(), "Restored state did not return to prompt context.")

    request_json(f"/story-state/items/character/{ids['archive_item_id']}/archive", method="POST")
    state = overview(session_id)
    require("old green cloak" not in (state.get("prompt_context") or "").lower(), "Archived stale outfit leaked into prompt context.")
    require(state.get("conflicts"), "Expected conflict warning for duplicate current outfit.")

    request_json(
        f"/story-state/items/object/{ids['object_id']}",
        method="PATCH",
        payload={"value": "hidden in Mara boot", "owner_character_name": "Mara Review Smoke", "confidence": 0.9},
    )
    state = overview(session_id)
    require("hidden in mara boot" in (state.get("prompt_context") or "").lower(), "Edited object state did not enter prose context.")

    result = request_json(f"/story-state/runs/{ids['undo_run_id']}/undo", method="POST")
    require(result.get("affected_count", 0) >= 1, "Undo did not affect any state items.")
    state = overview(session_id)
    require("temporary torch" not in (state.get("prompt_context") or "").lower(), "Undone extraction item leaked into prompt context.")

    print(f"State review smoke session: {session_id}")
    print("Edit, disable, restore, archive, conflict detection, object edit, prose context, and undo passed.")
    print(f"Conflicts detected: {len(state.get('conflicts') or [])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"State review smoke failed: {error}")
        raise SystemExit(1)
