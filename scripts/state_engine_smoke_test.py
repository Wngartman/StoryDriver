from __future__ import annotations

import json
import sqlite3
import sys
import time
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
SESSION_TITLE = "StoryDriver State Smoke Test"
SCENE_TEXT = (
    "Mara left the thorn camp wearing a red cloak over the torn green one she had abandoned by the fire. "
    "She pressed a bandage to the fresh cut on her left forearm while Iven carried the brass key she had given him. "
    "At the ruined chapel, Mara promised Iven she would keep the prince's true name secret until dawn. "
    "By the end of the scene, they had moved from the forest road into the chapel nave."
)


def request_json(path: str, method: str = "GET") -> dict:
    request = urllib.request.Request(f"{BACKEND_URL}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def ensure_character(db: sqlite3.Connection, name: str, role: str) -> str:
    row = db.execute("SELECT id FROM characters WHERE name = ?", (name,)).fetchone()
    if row:
        db.execute("UPDATE characters SET auto_created = 1 WHERE id = ?", (row["id"],))
        return row["id"]
    character_id = str(uuid4())
    db.execute(
        """
        INSERT INTO characters (id, name, role, personality, appearance, current_state, auto_created)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (
            character_id,
            name,
            role,
            "State smoke-test character.",
            "Grounded cinematic fantasy appearance.",
            "Available for automatic state smoke testing.",
        ),
    )
    return character_id


def attach_character(db: sqlite3.Connection, session_id: str, character_id: str) -> None:
    existing = db.execute(
        "SELECT id FROM session_characters WHERE session_id = ? AND character_id = ?",
        (session_id, character_id),
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE session_characters SET is_active = 1 WHERE session_id = ? AND character_id = ?",
            (session_id, character_id),
        )
        return
    db.execute(
        "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
        (str(uuid4()), session_id, character_id),
    )


def ensure_test_scene() -> tuple[str, str, str]:
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

        mara_id = ensure_character(db, "Mara State Smoke", "Scout")
        iven_id = ensure_character(db, "Iven State Smoke", "Key bearer")
        attach_character(db, session_id, mara_id)
        attach_character(db, session_id, iven_id)

        scene_id = str(uuid4())
        version_id = str(uuid4())
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
            VALUES (?, ?, ?, ?, 'continue')
            """,
            (scene_id, session_id, "Automatic Story State smoke test scene.", SCENE_TEXT),
        )
        db.execute(
            """
            INSERT INTO scene_versions (
                id, scene_id, session_id, director_note, generated_text, mode, version_index
            )
            VALUES (?, ?, ?, ?, ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, "Automatic Story State smoke test scene.", SCENE_TEXT),
        )
        db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (session_id,),
        )
    return session_id, scene_id, version_id


def main() -> int:
    try:
        health = request_json("/health")
    except Exception as error:
        print(f"Backend is not reachable at {BACKEND_URL}: {error}")
        return 1
    if not health.get("ok"):
        print(f"Backend health is not OK: {health}")
        return 1

    session_id, scene_id, version_id = ensure_test_scene()
    register_test_session_cleanup(BACKEND_URL, session_id)
    print(f"Created/reused smoke session: {session_id}")
    print(f"Smoke scene/version: {scene_id} / {version_id}")
    started = time.monotonic()
    try:
        run = request_json(
            f"/sessions/{session_id}/scenes/{scene_id}/story-state/extract?version_id={version_id}",
            method="POST",
        )
    except Exception as error:
        print(f"Story-state extraction failed: {error}")
        return 1
    elapsed = time.monotonic() - started
    print(f"Extraction status: {run.get('status')} in {elapsed:.1f}s")
    if run.get("error"):
        print(f"Extractor error: {run['error']}")
        return 1
    overview = request_json(f"/sessions/{session_id}/story-state")
    print(f"Character live states: {len(overview.get('character_live_state', []))}")
    print(f"Objects: {len(overview.get('objects', []))}")
    print(f"Plot threads: {len(overview.get('plot_threads', []))}")
    print(f"Scene/world states: {len(overview.get('scene_state', [])) + len(overview.get('world_state', []))}")
    if not overview.get("character_live_state"):
        print("No character state was extracted; inspect the latest run warnings in Story State.")
        return 1
    if not overview.get("objects"):
        print("No object ownership state was extracted; inspect the latest run warnings in Story State.")
        return 1
    if not overview.get("plot_threads"):
        print("No promise/secret plot thread was extracted; inspect the latest run warnings in Story State.")
        return 1
    if not (overview.get("scene_state") or overview.get("world_state")):
        print("No location/scene state was extracted; inspect the latest run warnings in Story State.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
