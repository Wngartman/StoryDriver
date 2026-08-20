from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "backend" / "data" / "app.db"
WORKFLOW_ID = "lonecat_zit_nsfw_8_0_1_api"


def main() -> int:
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT value_json FROM app_settings WHERE key = 'image_settings'").fetchone()
        settings = json.loads(row["value_json"] if row and row["value_json"] else "{}")
        settings.update(
            {
                "comfyui_base_url": "http://localhost:8188",
                "selected_workflow_id": WORKFLOW_ID,
                "image_resource_mode": "image_priority",
                "auto_reload_lm_after_image": True,
                "generate_image_while_narrating": True,
                "auto_image_on_narrate": False,
            }
        )
        settings.setdefault("free_comfyui_memory_after_generation", False)
        db.execute(
            """
            INSERT INTO app_settings (key, value_json)
            VALUES ('image_settings', ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            (json.dumps(settings),),
        )
        db.execute(
            """
            UPDATE session_image_settings
            SET selected_workflow_id = ?
            WHERE selected_workflow_id IS NULL
               OR selected_workflow_id = ''
               OR selected_workflow_id = 'image_z_image_turbo'
               OR selected_workflow_id != ?
            """,
            (WORKFLOW_ID, WORKFLOW_ID),
        )
    print(f"Selected StoryDriver image workflow: {WORKFLOW_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
