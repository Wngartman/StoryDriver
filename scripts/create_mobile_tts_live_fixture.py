from __future__ import annotations

import datetime
import json
import sqlite3
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "backend" / "data" / "app.db"
SESSION_ID = "mobile-tts-live-verification-20260616"


def utc_now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def main() -> int:
    scene_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = utc_now()
    paragraph = (
        "Mara held the lantern low as rain clicked against the old station windows. "
        "She waited until the echo of the last train faded, then crossed the platform "
        "with the careful patience of someone who had learned to listen before moving. "
        "The brass key was still warm in her palm. Jonas watched from beside the timetable "
        "board, coat collar dark with water, trying not to look at the locked office door. "
        "The room beyond it smelled of dust, wet wool, and the faint mineral tang of old "
        "batteries. He said the ledger had to be there, and that they would take it before "
        "the night clerk came back. Mara nodded, but the promise felt thinner now that the "
        "storm had swallowed the road behind them."
    )
    paragraphs = [
        f"{paragraph} This was checkpoint {index + 1}, and the silence after it gave the narration another clean place to breathe."
        for index in range(42)
    ]
    text = "\n\n".join(paragraphs)
    generation_stats = {
        "test_fixture": "mobile_tts_live_verification",
        "visible_prose_tokens_per_second": 100,
        "first_token_latency_seconds": 1.2,
    }
    with sqlite3.connect(DB_PATH) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            """
            INSERT OR REPLACE INTO sessions (id, title, created_at, updated_at, title_source, auto_title_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (SESSION_ID, "StoryDriver Mobile TTS Live Test", now, now, "user_set", "user_set"),
        )
        db.execute("DELETE FROM scene_versions WHERE session_id = ?", (SESSION_ID,))
        db.execute("DELETE FROM scenes WHERE session_id = ?", (SESSION_ID,))
        db.execute(
            """
            INSERT INTO scenes (
                id, session_id, director_note, generated_text, generation_stats_json, mode, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scene_id,
                SESSION_ID,
                "Mobile TTS live verification fixture: long scene for chunk boundary playback.",
                text,
                json.dumps(generation_stats),
                "continue",
                now,
                now,
            ),
        )
        db.execute(
            """
            INSERT INTO scene_versions (
                id, scene_id, session_id, director_note, generated_text, generation_stats_json, mode, version_index, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                scene_id,
                SESSION_ID,
                "Mobile TTS live verification fixture: long scene for chunk boundary playback.",
                text,
                json.dumps(generation_stats),
                "continue",
                1,
                now,
            ),
        )
    print(
        json.dumps(
            {
                "session_id": SESSION_ID,
                "scene_id": scene_id,
                "version_id": version_id,
                "chars": len(text),
                "paragraphs": len(paragraphs),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
