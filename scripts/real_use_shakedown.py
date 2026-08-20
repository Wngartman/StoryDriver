from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
REPORT_PATH = DATA_DIR / "logs" / "REAL_USE_SHAKEDOWN_REPORT.md"
BACKEND_URL = os.environ.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001")
SESSION_TITLE = "StoryDriver Real Use Shakedown"

sys.path.insert(0, str(BACKEND_DIR))


def ensure_windows_runtime_dlls() -> None:
    candidates: list[Path] = []
    for key in ("STORYDRIVER_PYTHONHOME", "PYTHONHOME"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value))
    vendor_root = Path.home() / ".lmstudio" / "extensions" / "backends" / "vendor"
    if vendor_root.exists():
        candidates.extend(sorted((path.parent for path in vendor_root.glob("**/python.exe")), reverse=True))
    seen: set[str] = set()
    for home in candidates:
        if not home.exists():
            continue
        for candidate in (home, home / "DLLs", home / "Library" / "bin"):
            key = str(candidate).lower()
            if key in seen or not candidate.exists():
                continue
            seen.add(key)
            try:
                os.add_dll_directory(str(candidate))
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = f"{candidate}{os.pathsep}{os.environ.get('PATH', '')}"


ensure_windows_runtime_dlls()

from app.database import db_session, init_db  # noqa: E402
from app.services.image_prompt_service import fallback_prompt, load_visual_context  # noqa: E402
from app.generation.prompt_builder import build_scene_prompt  # noqa: E402
from app.memory.summaries import latest_summary, maybe_update_session_summary  # noqa: E402
from app.settings.store import load_model_settings  # noqa: E402
from app.memory.engine import (  # noqa: E402
    active_prompt_state_count,
    archived_state_count,
    create_run,
    format_story_state_for_prompt,
    format_visual_story_state_for_prompt,
    merge_state_payload,
    update_run,
    utc_now,
)


SCENES: list[dict[str, Any]] = [
    {
        "index": 1,
        "text": (
            "Mara, Elias, and Rowan planned beside a wet campfire before dawn. Mara wore her old green cloak, "
            "Elias kept his dark coat buttoned tight, and Rowan checked the nicked edge of his old sword."
        ),
        "payload": {
            "character_updates": [
                {
                    "character_name": "Mara Real Use",
                    "state_type": "appearance",
                    "key": "current_outfit",
                    "value": "wearing her old green cloak over travel leathers",
                    "action": "replace",
                    "confidence": 0.96,
                },
                {
                    "character_name": "Rowan Real Use",
                    "state_type": "inventory",
                    "key": "old_sword",
                    "value": "carrying a nicked old sword",
                    "confidence": 0.94,
                },
            ],
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "stormy frontier camp beside a low wet fire",
                    "confidence": 0.95,
                }
            ],
        },
    },
    {
        "index": 2,
        "text": (
            "At the road shrine, Mara changed out of the green cloak and tied on a red cloak with a torn hem so the bandits "
            "would mistake her for one of their runners."
        ),
        "payload": {
            "character_updates": [
                {
                    "character_name": "Mara Real Use",
                    "state_type": "appearance",
                    "key": "current_outfit",
                    "value": "wearing a red cloak with a torn hem over travel leathers",
                    "action": "replace",
                    "confidence": 0.99,
                }
            ],
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "road shrine between camp and the ruined chapel",
                    "confidence": 0.92,
                }
            ],
        },
    },
    {
        "index": 3,
        "kind": "superseded",
        "rewrite_group": "bandit_encounter",
        "text": "In the abandoned draft, a bandit knife cut Mara across the right knee before Rowan drove the attacker back.",
        "payload": {
            "character_updates": [
                {
                    "character_name": "Mara Real Use",
                    "state_type": "injury",
                    "key": "injury_right_knee",
                    "value": "right knee cut by a bandit knife",
                    "confidence": 0.93,
                }
            ]
        },
    },
    {
        "index": 3,
        "kind": "latest",
        "rewrite_group": "bandit_encounter",
        "text": (
            "In the rewritten encounter, an arrow grazed Mara's left shoulder. Rowan caught her before she fell, and Elias dragged "
            "them behind a broken milestone while rain turned the road to black mud."
        ),
        "payload": {
            "character_updates": [
                {
                    "character_name": "Mara Real Use",
                    "state_type": "injury",
                    "key": "injury_left_shoulder",
                    "value": "left shoulder grazed by a bandit arrow",
                    "confidence": 0.98,
                }
            ],
            "relationship_updates": [
                {
                    "character_a_name": "Mara Real Use",
                    "character_b_name": "Rowan Real Use",
                    "relationship_key": "trust",
                    "content": "Mara trusts Rowan more after he caught her during the bandit ambush.",
                    "confidence": 0.9,
                }
            ],
        },
    },
    {
        "index": 4,
        "text": (
            "Elias found a bronze compass under the chapel stair and hid it in his satchel before Mara noticed the vow-mark etched "
            "into its lid."
        ),
        "payload": {
            "object_updates": [
                {
                    "object_key": "bronze_compass_real_use",
                    "name": "bronze compass",
                    "state_type": "secret_object",
                    "value": "hidden in Elias's satchel",
                    "owner_character_name": "Elias Real Use",
                    "confidence": 0.97,
                }
            ],
            "plot_thread_updates": [
                {
                    "thread_key": "elias_hides_bronze_compass_real_use",
                    "title": "Elias hides the bronze compass",
                    "status": "active",
                    "content": "Elias is hiding the bronze compass from Mara and Rowan.",
                    "confidence": 0.96,
                }
            ],
        },
    },
    {
        "index": 5,
        "text": (
            "They entered the ruined chapel nave. Rain fell through the broken roof onto star-metal tiles, and Elias whispered that "
            "the chapel had been built to bind promises."
        ),
        "payload": {
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "ruined chapel nave with rain falling through the broken roof",
                    "confidence": 0.97,
                }
            ],
            "world_updates": [
                {
                    "state_type": "world",
                    "key": "star_metal_tiles",
                    "value": "star-metal tiles in the chapel are tied to vows and promises",
                    "confidence": 0.92,
                }
            ],
        },
    },
    {
        "index": 6,
        "text": (
            "Mara caught Elias lying about the satchel and stopped walking beside him. Rowan noticed the silence and kept himself "
            "between them as they crossed the nave."
        ),
        "payload": {
            "relationship_updates": [
                {
                    "character_a_name": "Mara Real Use",
                    "character_b_name": "Elias Real Use",
                    "relationship_key": "trust",
                    "content": "Mara distrusts Elias after catching him lying about his satchel.",
                    "confidence": 0.94,
                },
                {
                    "character_a_name": "Rowan Real Use",
                    "character_b_name": "Elias Real Use",
                    "relationship_key": "tension",
                    "content": "Rowan watches Elias closely because Mara no longer trusts him.",
                    "confidence": 0.86,
                },
            ]
        },
    },
    {
        "index": 7,
        "kind": "superseded",
        "rewrite_group": "secret_reveal",
        "text": "In the abandoned draft, Elias confessed immediately that the bronze compass was in his satchel.",
        "payload": {
            "plot_thread_updates": [
                {
                    "thread_key": "elias_hides_bronze_compass_real_use",
                    "title": "Elias hides the bronze compass",
                    "status": "resolved",
                    "content": "Elias confessed that he had hidden the bronze compass.",
                    "confidence": 0.94,
                }
            ]
        },
    },
    {
        "index": 7,
        "kind": "latest",
        "rewrite_group": "secret_reveal",
        "text": (
            "In the rewritten scene, Elias almost confessed but swallowed the truth. He promised Mara he would explain the compass "
            "after they survived the chapel, while still keeping it hidden."
        ),
        "payload": {
            "plot_thread_updates": [
                {
                    "thread_key": "elias_hides_bronze_compass_real_use",
                    "title": "Elias hides the bronze compass",
                    "status": "active",
                    "content": "Elias is still hiding the bronze compass and promised to explain it later.",
                    "confidence": 0.95,
                },
                {
                    "thread_key": "elias_promises_explanation_real_use",
                    "title": "Elias promises an explanation",
                    "status": "active",
                    "content": "Elias promised Mara he will explain the compass after they survive the chapel.",
                    "confidence": 0.93,
                },
            ]
        },
    },
    {
        "index": 8,
        "text": (
            "Mara passed the iron chapel key to Rowan so he could force the choir gate while she held her bandaged shoulder under "
            "the rain-dark red cloak."
        ),
        "payload": {
            "object_updates": [
                {
                    "object_key": "iron_chapel_key_real_use",
                    "name": "iron chapel key",
                    "state_type": "held_item",
                    "value": "held by Rowan after Mara handed it over",
                    "owner_character_name": "Rowan Real Use",
                    "confidence": 0.96,
                }
            ],
            "character_updates": [
                {
                    "character_name": "Mara Real Use",
                    "state_type": "injury",
                    "key": "injury_left_shoulder",
                    "value": "left shoulder bandaged after the bandit arrow graze",
                    "confidence": 0.97,
                }
            ],
        },
    },
    {
        "index": 9,
        "text": (
            "After the gate opened, Rowan returned the iron chapel key to Mara. She tucked it into the inner pocket beneath her red "
            "cloak before they took the ridge path back to camp."
        ),
        "payload": {
            "object_updates": [
                {
                    "object_key": "iron_chapel_key_real_use",
                    "name": "iron chapel key",
                    "state_type": "held_item",
                    "value": "back with Mara in the inner pocket beneath her red cloak",
                    "owner_character_name": "Mara Real Use",
                    "confidence": 0.97,
                }
            ],
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "ridge path back toward camp",
                    "confidence": 0.93,
                }
            ],
        },
    },
    {
        "index": 10,
        "text": (
            "Back at camp, Rowan cleaned Mara's left shoulder bandage while Elias sat apart with the bronze compass still hidden in "
            "his satchel. The red cloak steamed by the fire, muddy and torn."
        ),
        "payload": {
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "camp where Mara's shoulder wound is being treated",
                    "confidence": 0.98,
                }
            ],
            "character_updates": [
                {
                    "character_name": "Mara Real Use",
                    "state_type": "appearance",
                    "key": "current_outfit",
                    "value": "wearing a muddy torn red cloak over travel leathers",
                    "action": "replace",
                    "confidence": 0.98,
                },
                {
                    "character_name": "Mara Real Use",
                    "state_type": "injury",
                    "key": "injury_left_shoulder",
                    "value": "left shoulder bandaged and being cleaned at camp",
                    "confidence": 0.98,
                },
            ],
            "object_updates": [
                {
                    "object_key": "bronze_compass_real_use",
                    "name": "bronze compass",
                    "state_type": "secret_object",
                    "value": "still hidden in Elias's satchel at camp",
                    "owner_character_name": "Elias Real Use",
                    "confidence": 0.96,
                }
            ],
        },
    },
]


def request_json(path: str, method: str = "GET", payload: dict | None = None, timeout: int = 60) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{BACKEND_URL.rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else ""
        raise RuntimeError(f"{method} {path} failed: HTTP {error.code}: {detail}") from error
    except Exception as error:
        raise RuntimeError(f"{method} {path} failed: {error}") from error


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_None._"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        values = [str(row.get(column, "")).replace("\n", " ")[:220] for column in columns]
        body.append("| " + " | ".join(values) + " |")
    return "\n".join([header, divider, *body])


def ensure_character(db: sqlite3.Connection, name: str, role: str, appearance: str, image_prompt: str) -> str:
    row = db.execute("SELECT id FROM characters WHERE name = ?", (name,)).fetchone()
    if row:
        character_id = row["id"]
    else:
        character_id = str(uuid4())
        db.execute(
            """
            INSERT INTO characters (id, name, role, personality, appearance, current_state, image_prompt)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character_id,
                name,
                role,
                "Real-use shakedown character. Stable base card should not be overwritten by live state.",
                appearance,
                "Available for real-use shakedown.",
                image_prompt,
            ),
        )
    db.execute(
        """
        INSERT INTO character_visual_profiles (
            character_id, base_visual_description, face_description, hair, default_outfit,
            distinctive_marks, color_palette, z_image_lora_trigger, visual_consistency_notes,
            used_in_image_prompts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(character_id) DO UPDATE SET
            base_visual_description = excluded.base_visual_description,
            face_description = excluded.face_description,
            hair = excluded.hair,
            default_outfit = excluded.default_outfit,
            distinctive_marks = excluded.distinctive_marks,
            color_palette = excluded.color_palette,
            z_image_lora_trigger = excluded.z_image_lora_trigger,
            visual_consistency_notes = excluded.visual_consistency_notes,
            used_in_image_prompts = 1
        """,
        (
            character_id,
            image_prompt,
            "grounded, practical face",
            "short black hair" if "Mara" in name else "dark hair",
            "green cloak and travel leathers" if "Mara" in name else "dark frontier clothing",
            "scar over left eyebrow" if "Mara" in name else "",
            "rain-dark reds, charcoal, leather, old steel",
            name.lower().replace(" ", "_") + "_lora_v1",
            "Stable base identity comes from this profile; live state supplies current outfit, wounds, dirt, and carried objects.",
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


def ensure_session() -> tuple[str, dict[str, str]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        session = db.execute("SELECT id FROM sessions WHERE title = ?", (SESSION_TITLE,)).fetchone()
        if session:
            session_id = session["id"]
        else:
            session_id = str(uuid4())
            db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, SESSION_TITLE))
        characters = {
            "mara": ensure_character(
                db,
                "Mara Real Use",
                "thief/scout",
                "short black hair, scar over left eyebrow, guarded stance",
                "Mara, short black hair, scar over left eyebrow, guarded practical thief-scout, grounded fantasy realism",
            ),
            "elias": ensure_character(
                db,
                "Elias Real Use",
                "nervous scholar",
                "thin nervous scholar with silver spectacles and a dark coat",
                "Elias, nervous scholar, silver spectacles, dark coat, ink-stained cuffs, secretive posture",
            ),
            "rowan": ensure_character(
                db,
                "Rowan Real Use",
                "tired mercenary",
                "broad tired mercenary with heavy cloak and old sword",
                "Rowan, tired mercenary, heavy cloak, weathered face, old sword, practical armor",
            ),
        }
        for character_id in characters.values():
            attach_character(db, session_id, character_id)
        world = db.execute("SELECT id FROM world_notes WHERE session_id = ?", (session_id,)).fetchone()
        if world:
            db.execute(
                """
                UPDATE world_notes
                SET setting = ?, tone = ?, rules = ?, locations = ?
                WHERE session_id = ?
                """,
                (
                    "stormy dark fantasy frontier, ruined chapel, bandit road, wet camp",
                    "grounded gritty tone, practical danger, bad weather, low light",
                    "star-metal and vow-magic exist, but people remain practical and afraid",
                    "camp; road shrine; ruined chapel nave; ridge path",
                    session_id,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO world_notes (id, session_id, setting, tone, rules, locations)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    session_id,
                    "stormy dark fantasy frontier, ruined chapel, bandit road, wet camp",
                    "grounded gritty tone, practical danger, bad weather, low light",
                    "star-metal and vow-magic exist, but people remain practical and afraid",
                    "camp; road shrine; ruined chapel nave; ridge path",
                ),
            )
        db.execute(
            """
            INSERT INTO session_image_settings (
                session_id, image_prompt_style, preferred_visual_tone, realism_notes,
                lighting_camera_notes, default_negative_prompt
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                image_prompt_style = excluded.image_prompt_style,
                preferred_visual_tone = excluded.preferred_visual_tone,
                realism_notes = excluded.realism_notes,
                lighting_camera_notes = excluded.lighting_camera_notes,
                default_negative_prompt = excluded.default_negative_prompt
            """,
            (
                session_id,
                "gritty grounded fantasy realism, rough still from a dark fantasy movie, imperfect camera quality, slight grain",
                "stormy, muddy, practical, low light, no glamour",
                "realistic faces, clothes, wounds, dirt, sweat, mud; no plastic skin or shiny clean clothes",
                "campfire, torchlight, rain, practical camera angle, documentary-like framing",
                "plastic skin, clean clothing, glamour lighting, anime, cartoon, extra fingers, modern objects, poster composition",
            ),
        )
    return session_id, characters


def insert_scene(
    session_id: str,
    text: str,
    index: int,
    *,
    scene_id: str | None = None,
    version_index: int = 1,
    kind: str = "continue",
) -> tuple[str, str]:
    with sqlite3.connect(DB_PATH) as db:
        scene_id = scene_id or str(uuid4())
        version_id = str(uuid4())
        if version_index == 1:
            db.execute(
                """
                INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
                VALUES (?, ?, ?, ?, ?)
                """,
                (scene_id, session_id, f"Real use shakedown scene {index}.", text, kind),
            )
        else:
            db.execute(
                """
                UPDATE scenes
                SET director_note = ?, generated_text = ?, mode = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND session_id = ?
                """,
                (f"Real use shakedown rewritten scene {index}.", text, kind, scene_id, session_id),
            )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, scene_id, session_id, f"Real use shakedown scene {index}.", text, kind, version_index),
        )
        db.execute("UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (session_id,))
    return scene_id, version_id


def merge_payload(session_id: str, scene_id: str, version_id: str, payload: dict[str, Any]) -> list[str]:
    run_id = create_run(session_id, scene_id, version_id)
    update_run(run_id, status="running", started_at=utc_now())
    warnings = merge_state_payload(
        run_id=run_id,
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id,
        payload={
            "character_updates": [],
            "relationship_updates": [],
            "world_updates": [],
            "scene_updates": [],
            "object_updates": [],
            "plot_thread_updates": [],
            "summary_notes": [],
            "warnings": [],
            **payload,
        },
    )
    update_run(
        run_id,
        status="completed",
        completed_at=utc_now(),
        raw_response=json.dumps(payload, ensure_ascii=False),
        warnings_json=json.dumps(warnings, ensure_ascii=False),
    )
    return warnings


def run_controlled_session(session_id: str) -> tuple[list[dict[str, str]], list[str]]:
    records: list[dict[str, str]] = []
    warnings: list[str] = []
    rewrite_scene_ids: dict[str, str] = {}
    version_indexes: dict[str, int] = {}
    for scene in SCENES:
        group = scene.get("rewrite_group")
        version_index = 1
        scene_id = None
        kind = "continue"
        if group and scene.get("kind") == "latest":
            scene_id = rewrite_scene_ids.get(group)
            version_index = version_indexes.get(group, 1) + 1
            kind = "rewrite"
        elif group:
            kind = "rewrite"
        actual_scene_id, version_id = insert_scene(
            session_id,
            scene["text"],
            scene["index"],
            scene_id=scene_id,
            version_index=version_index,
            kind=kind,
        )
        if group:
            rewrite_scene_ids[group] = actual_scene_id
            version_indexes[group] = version_index
        warnings.extend(merge_payload(session_id, actual_scene_id, version_id, scene.get("payload") or {}))
        records.append(
            {
                "index": str(scene["index"]),
                "kind": str(scene.get("kind") or "normal"),
                "scene_id": actual_scene_id,
                "version_id": version_id,
                "text": scene["text"],
            }
        )
    return records, warnings


def query_state(session_id: str) -> dict[str, Any]:
    with db_session() as db:
        rows = {
            "characters": db.execute(
                """
                SELECT character_name, state_type, key, value, archived, disabled, source_scene_id, source_version_id
                FROM character_live_state
                WHERE session_id = ?
                ORDER BY archived ASC, disabled ASC, character_name ASC, key ASC, updated_at DESC
                """,
                (session_id,),
            ).fetchall(),
            "relationships": db.execute(
                """
                SELECT character_a_name, character_b_name, relationship_key, content, archived, disabled
                FROM relationship_state
                WHERE session_id = ?
                ORDER BY archived ASC, disabled ASC, updated_at DESC
                """,
                (session_id,),
            ).fetchall(),
            "objects": db.execute(
                """
                SELECT name, object_key, state_type, value, owner_character_name, archived, disabled
                FROM object_state
                WHERE session_id = ?
                ORDER BY archived ASC, disabled ASC, object_key ASC, updated_at DESC
                """,
                (session_id,),
            ).fetchall(),
            "threads": db.execute(
                """
                SELECT title, thread_key, status, content, disabled
                FROM plot_threads
                WHERE session_id = ?
                ORDER BY disabled ASC, status ASC, updated_at DESC
                """,
                (session_id,),
            ).fetchall(),
            "scene_state": db.execute(
                """
                SELECT scene_id, version_id, key, value, archived, disabled
                FROM scene_live_state
                WHERE session_id = ?
                ORDER BY archived ASC, disabled ASC, updated_at DESC
                """,
                (session_id,),
            ).fetchall(),
            "summary": db.execute(
                """
                SELECT summary_text, from_scene_id, to_scene_id, updated_at
                FROM session_summaries
                WHERE session_id = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone(),
        }
    result: dict[str, Any] = {}
    for key, value in rows.items():
        if isinstance(value, list):
            result[key] = [{column: row[column] for column in row.keys()} for row in value]
        else:
            result[key] = {column: value[column] for column in value.keys()} if value else None
    return result


def active_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not row.get("archived") and not row.get("disabled")]


def assert_continuity(state: dict[str, Any], prose_context: str, visual_context: str, image_prompt_text: str) -> list[str]:
    failures: list[str] = []
    active_character_text = "\n".join(
        f"{row['character_name']} {row['state_type']} {row['key']} {row['value']}"
        for row in active_rows(state["characters"])
    ).lower()
    active_scene_text = "\n".join(row["value"] for row in active_rows(state["scene_state"])).lower()
    object_text = "\n".join(
        f"{row['name']} {row['value']} {row['owner_character_name']}" for row in active_rows(state["objects"])
    ).lower()
    thread_text = "\n".join(f"{row['title']} {row['status']} {row['content']}" for row in state["threads"] if not row.get("disabled")).lower()
    combined_prompt_text = f"{prose_context}\n{visual_context}\n{image_prompt_text}".lower()

    checks = [
        ("current red cloak active", "red cloak", active_character_text),
        ("left shoulder injury active", "left shoulder", active_character_text),
        ("Rowan old sword active", "old sword", active_character_text),
        ("bronze compass remains with Elias", "bronze compass", object_text),
        ("bronze compass owner Elias", "elias", object_text),
        ("iron key owner returned to Mara", "iron chapel key", object_text),
        ("camp current location", "camp", active_scene_text),
        ("Elias secret thread active", "hides the bronze compass", thread_text),
    ]
    for label, needle, haystack in checks:
        if needle not in haystack:
            failures.append(f"Missing {label}: {needle}")
    stale_checks = [
        ("green cloak leaked as current", "green cloak", active_character_text),
        ("superseded knee injury leaked", "right knee", active_character_text),
        ("old chapel nave remained current", "chapel nave", active_scene_text),
        ("superseded confession resolved secret", "confessed", thread_text),
    ]
    for label, needle, haystack in stale_checks:
        if needle in haystack:
            failures.append(label)
    for needle in ("red cloak", "left shoulder", "bronze compass", "camp"):
        if needle not in combined_prompt_text:
            failures.append(f"Prompt/image context missed continuity: {needle}")
    return failures


def latest_scene_records(session_id: str, limit: int = 6) -> list[dict[str, str]]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT
                s.id,
                s.director_note,
                COALESCE(
                    (
                        SELECT sv.generated_text
                        FROM scene_versions sv
                        WHERE sv.scene_id = s.id AND sv.session_id = s.session_id
                        ORDER BY sv.version_index DESC
                        LIMIT 1
                    ),
                    s.generated_text
                ) AS generated_text
            FROM scenes s
            WHERE s.session_id = ?
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in reversed(rows)]


def service_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {"health": None, "diagnostics": None, "image_job_status": None, "errors": []}
    for label, path, timeout in (
        ("health", "/health", 10),
        ("diagnostics", "/diagnostics", 45),
        ("image_job_status", "/images/job-status", 10),
    ):
        try:
            snapshot[label] = request_json(path, timeout=timeout)
        except Exception as error:  # noqa: BLE001
            snapshot["errors"].append(f"{label}: {error}")
    return snapshot


def synthesize_tts_samples(session_records: list[dict[str, str]], count: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if count <= 0:
        return results
    samples = [record for record in session_records if record["kind"] != "superseded"][:count]
    for index, record in enumerate(samples, start=1):
        text = record["text"][:1200]
        started = time.perf_counter()
        try:
            response = request_json(
                "/tts/synthesize",
                method="POST",
                payload={"text": text, "provider": "kokoro", "voice": "af_heart", "speed": 1.0},
                timeout=180,
            )
            results.append(
                {
                    "index": index,
                    "ok": True,
                    "scene_index": record["index"],
                    "duration_seconds": round(time.perf_counter() - started, 2),
                    "audio_url": response.get("audio_url") if isinstance(response, dict) else "",
                }
            )
        except Exception as error:  # noqa: BLE001
            results.append(
                {
                    "index": index,
                    "ok": False,
                    "scene_index": record["index"],
                    "duration_seconds": round(time.perf_counter() - started, 2),
                    "error": str(error),
                }
            )
    return results


def get_ready_workflow() -> dict[str, Any] | None:
    workflows = request_json("/image-workflows", timeout=30)
    if not isinstance(workflows, dict):
        return None
    ready = [item for item in workflows.get("workflows", []) if item.get("status") == "ready"]
    selected_id = workflows.get("selected_workflow_id")
    if selected_id:
        selected = next((item for item in ready if item.get("id") == selected_id), None)
        if selected:
            return selected
    return ready[0] if ready else None


def with_image_priority() -> dict[str, Any] | None:
    settings = request_json("/settings/image", timeout=20)
    if isinstance(settings, dict) and settings.get("image_resource_mode") != "image_priority":
        updated = dict(settings)
        updated["image_resource_mode"] = "image_priority"
        request_json("/settings/image", method="PUT", payload=updated, timeout=30)
    return settings if isinstance(settings, dict) else None


def restore_image_settings(settings: dict[str, Any] | None) -> None:
    if not settings:
        return
    try:
        request_json("/settings/image", method="PUT", payload=settings, timeout=30)
    except Exception:
        pass


def run_image_samples(session_id: str, session_records: list[dict[str, str]], count: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if count <= 0:
        return results
    job_status = request_json("/images/job-status", timeout=15)
    if isinstance(job_status, dict) and job_status.get("active"):
        return [{"ok": False, "error": "Image job already active; skipped real image shakedown to avoid duplicates."}]
    try:
        workflow = get_ready_workflow()
    except Exception as error:  # noqa: BLE001
        return [{"ok": False, "error": f"Could not list image workflows: {error}"}]
    if not workflow:
        return [{"ok": False, "error": "No ready image workflow found."}]
    previous_settings = with_image_priority()
    try:
        usable = [record for record in session_records if record["kind"] != "superseded"]
        targets = [usable[-1], usable[4], usable[-1]]
        for index, record in enumerate(targets[:count], start=1):
            started = time.perf_counter()
            try:
                image = request_json(
                    "/images/generate",
                    method="POST",
                    payload={
                        "session_id": session_id,
                        "scene_id": record["scene_id"],
                        "version_id": record["version_id"],
                        "workflow_id": workflow["id"],
                        "open_preview": False,
                    },
                    timeout=1200,
                )
                actions = image.get("resource_actions") if isinstance(image, dict) else {}
                results.append(
                    {
                        "index": index,
                        "ok": True,
                        "scene_index": record["index"],
                        "image_id": image.get("id") if isinstance(image, dict) else "",
                        "duration_seconds": round(time.perf_counter() - started, 2),
                        "resource_mode": image.get("resource_mode") if isinstance(image, dict) else "",
                        "comfyui_time": (actions or {}).get("performance_timings", {}).get("comfyui_queue_to_image_retrieved"),
                        "lm_unload_confirmed": (actions or {}).get("unload_confirmed"),
                        "lm_reload_succeeded": (actions or {}).get("reload_succeeded"),
                        "comfyui_free_succeeded": (actions or {}).get("comfyui_free_succeeded"),
                        "primary": image.get("is_primary") if isinstance(image, dict) else None,
                    }
                )
            except Exception as error:  # noqa: BLE001
                results.append(
                    {
                        "index": index,
                        "ok": False,
                        "scene_index": record["index"],
                        "duration_seconds": round(time.perf_counter() - started, 2),
                        "error": str(error),
                    }
                )
                break
    finally:
        restore_image_settings(previous_settings)
    return results


def image_history_count(session_id: str, scene_id: str, version_id: str) -> tuple[int, int]:
    with db_session() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN is_primary = 1 AND status = 'accepted' THEN 1 ELSE 0 END) AS primary_count
            FROM generated_images
            WHERE session_id = ? AND scene_id = ? AND version_id = ?
            """,
            (session_id, scene_id, version_id),
        ).fetchone()
    return int(row["total"] or 0), int(row["primary_count"] or 0)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real-use StoryDriver shakedown.")
    parser.add_argument("--tts-count", type=int, default=3, help="Number of Kokoro synthesis samples to run.")
    parser.add_argument("--image-count", type=int, default=0, help="Number of real Auto Image Priority image jobs to run.")
    parser.add_argument("--skip-summary", action="store_true", help="Skip the forced rolling summary pass for faster reruns.")
    args = parser.parse_args()

    DATA_DIR.joinpath("logs").mkdir(parents=True, exist_ok=True)
    init_db()
    before = service_snapshot()
    if not (before.get("health") or {}).get("ok"):
        REPORT_PATH.write_text(
            "# Real Use Shakedown Report\n\nBackend health was unavailable; shakedown did not run.\n",
            encoding="utf-8",
        )
        print("Backend health was unavailable; shakedown did not run.")
        return 1

    session_id, _characters = ensure_session()
    records, merge_warnings = run_controlled_session(session_id)

    summary_started = time.perf_counter()
    if args.skip_summary:
        summary_result = {"status": "skipped", "reason": "skip_summary_flag"}
    else:
        try:
            summary_result = await maybe_update_session_summary(session_id, force=True)
        except Exception as error:  # noqa: BLE001
            summary_result = {"status": "failed", "error": str(error)}
    summary_duration = round(time.perf_counter() - summary_started, 2)

    state = query_state(session_id)
    prose_context, prompt_item_count = format_story_state_for_prompt(session_id)
    visual_context = format_visual_story_state_for_prompt(session_id)
    recent_scenes = latest_scene_records(session_id)
    summary = latest_summary(session_id) or {}
    model_settings = load_model_settings(resolve_active_preset=True)
    next_prompt = build_scene_prompt(
        session_id=session_id,
        director_note="Continue at camp. Keep Mara's wound, red cloak, Elias's hidden compass, Rowan's sword, and current location straight.",
        mode="continue",
        recent_scenes=recent_scenes,
        session_summary=summary.get("summary_text"),
    )

    visual_context_payload = load_visual_context(session_id, records[-1]["text"])
    preview_prompt = fallback_prompt(
        scene_text=records[-1]["text"],
        negative_prompt="plastic skin, clean clothing, glamour lighting, anime, extra fingers, modern objects",
        visual_context=visual_context_payload,
        workflow_notes="Lonecat ZIT workflow; gritty grounded fantasy realism",
    )
    preview_prompt_text = preview_prompt.get("prompt") or ""
    continuity_failures = assert_continuity(state, prose_context, visual_context, preview_prompt_text)

    tts_results = synthesize_tts_samples(records, max(0, args.tts_count))
    image_results = run_image_samples(session_id, records, max(0, args.image_count))
    last_image_history = (0, 0)
    if image_results and image_results[-1].get("ok"):
        target = [record for record in records if record["kind"] != "superseded"][-1]
        last_image_history = image_history_count(session_id, target["scene_id"], target["version_id"])
    after = service_snapshot()

    active_character_state = active_rows(state["characters"])
    active_objects = active_rows(state["objects"])
    active_scene_state = active_rows(state["scene_state"])
    active_relationships = active_rows(state["relationships"])
    active_threads = [row for row in state["threads"] if row.get("status") == "active" and not row.get("disabled")]

    bugs_found: list[str] = []
    if continuity_failures:
        bugs_found.extend(continuity_failures)
    if any(not item.get("ok") for item in tts_results):
        bugs_found.append("At least one Kokoro synthesis sample failed.")
    if args.image_count and any(not item.get("ok") for item in image_results):
        bugs_found.append("At least one real image generation sample failed.")

    performance_notes = [
        "Image Settings non-workflow saves no longer force workflow rescans.",
        "Session visual-style autosaves no longer trigger workflow refreshes unless selected workflow changed.",
    ]
    mobile_notes = [
        "No structural mobile changes were needed in this script-driven pass.",
        "Existing 16px textarea/input styling and drawer scrolling should still be verified in-browser after build.",
    ]

    report = [
        "# Real Use Shakedown Report",
        "",
        f"- Updated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Story: {SESSION_TITLE}",
        f"- Session ID: `{session_id}`",
        f"- Controlled scene/version records created: {len(records)}",
        f"- Unique scene numbers covered: {len({record['index'] for record in records})}",
        f"- Rewritten/regenerated scene versions: {len([record for record in records if record['kind'] in {'superseded', 'latest'}])}",
        f"- Kokoro synthesis samples requested: {args.tts_count}",
        f"- Real image jobs requested: {args.image_count}",
        "",
        "## Services Status",
        "",
        f"- Backend health before: `{(before.get('health') or {}).get('ok')}`",
        f"- Backend health after: `{(after.get('health') or {}).get('ok')}`",
        f"- LM Studio OpenAI: `{((after.get('diagnostics') or {}).get('lm_studio') or {}).get('reachable')}`",
        f"- LM Studio REST: `{((after.get('diagnostics') or {}).get('lm_studio') or {}).get('rest_reachable')}`",
        f"- Kokoro: `{((after.get('diagnostics') or {}).get('kokoro') or {}).get('reachable')}`",
        f"- ComfyUI: `{((after.get('diagnostics') or {}).get('comfyui') or {}).get('reachable')}`",
        f"- Active image job after run: `{((after.get('image_job_status') or {}).get('active'))}`",
        "",
        "## Scenes Generated / Inserted",
        "",
        "The shakedown uses controlled scene text and extractor-shaped payloads so continuity assertions are deterministic. Live LM Studio scene generation and extraction are covered by the required smoke tests.",
        "",
        markdown_table(records, ["index", "kind", "scene_id", "version_id", "text"]),
        "",
        "## TTS Tests",
        "",
        markdown_table(tts_results, ["index", "ok", "scene_index", "duration_seconds", "audio_url", "error"]),
        "",
        "## Image Tests",
        "",
        markdown_table(image_results, ["index", "ok", "scene_index", "duration_seconds", "resource_mode", "comfyui_time", "lm_unload_confirmed", "comfyui_free_succeeded", "lm_reload_succeeded", "primary", "image_id", "error"]),
        "",
        f"- Last-scene image history count / primary count: {last_image_history[0]} / {last_image_history[1]}",
        "",
        "## State Continuity Tests",
        "",
        f"- Prompt state items included in next prose prompt: {prompt_item_count}",
        f"- Active prompt state items total: {active_prompt_state_count(session_id)}",
        f"- Archived/disabled state items total: {archived_state_count(session_id)}",
        f"- Next prompt size: {len(next_prompt)} characters",
        f"- Story state context size: {len(prose_context)} characters",
        f"- Visual state context size: {len(visual_context)} characters",
        f"- System prompt size: {len(model_settings.system_prompt or '')} characters",
        "",
        "### Active Character State",
        markdown_table(active_character_state, ["character_name", "state_type", "key", "value"]),
        "",
        "### Relationships",
        markdown_table(active_relationships, ["character_a_name", "character_b_name", "relationship_key", "content"]),
        "",
        "### Objects / Inventory",
        markdown_table(active_objects, ["name", "state_type", "value", "owner_character_name"]),
        "",
        "### Scene / Location",
        markdown_table(active_scene_state, ["key", "value"]),
        "",
        "### Active Plot Threads",
        markdown_table(active_threads, ["title", "status", "content"]),
        "",
        "## Visual Consistency Tests",
        "",
        f"- Preview visual beat: {preview_prompt.get('visual_beat') or 'fallback scene still'}",
        f"- Characters included: {', '.join(preview_prompt.get('characters_included') or []) or 'none'}",
        f"- Continuity used: `{json.dumps(preview_prompt.get('continuity_used') or {}, ensure_ascii=False)[:1000]}`",
        "",
        "```text",
        preview_prompt_text[:2500],
        "```",
        "",
        "## Summary Trigger",
        "",
        f"- Summary status: `{summary_result.get('status')}`",
        f"- Summary duration: {summary_duration}s",
        f"- Summary length: {len((summary or {}).get('summary_text') or '')} characters",
        f"- Summary error: {summary_result.get('error') or 'none'}",
        "",
        "## Bugs Found / Fixed",
        "",
        "\n".join(f"- {bug}" for bug in bugs_found) if bugs_found else "- No continuity, TTS synthesis, or image-job stuck-state failures were found in this run.",
        "",
        "## Performance Issues Found / Fixed",
        "",
        "\n".join(f"- {note}" for note in performance_notes),
        "",
        "## Mobile Issues",
        "",
        "\n".join(f"- {note}" for note in mobile_notes),
        "",
        "## Merge / State Warnings",
        "",
        "\n".join(f"- {warning}" for warning in merge_warnings[:60]) if merge_warnings else "- None.",
        "",
        "## Remaining Rough Edges",
        "",
        "- This script proves deterministic continuity and API synthesis/image paths; it does not replace a human read-through of ten creative LM-generated scenes.",
        "- Real UI playback progress still benefits from a browser click-through because API TTS synthesis cannot prove the browser audio element actually played.",
        "- Full three-image runs are intentionally optional because each Auto Image Priority job unloads/reloads the 31B model.",
        "",
        "## Recommended Next Step",
        "",
        "- Do one normal creative story session in the browser, then use Story State review tools to correct any extractor misses before deeper memory v2.",
    ]
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"Real-use shakedown session: {session_id}")
    print(f"Report: {REPORT_PATH}")
    if continuity_failures:
        print("Continuity failures:")
        for failure in continuity_failures:
            print(f"- {failure}")
    if any(not item.get("ok") for item in tts_results):
        print("One or more TTS samples failed; see report.")
    if args.image_count and any(not item.get("ok") for item in image_results):
        print("One or more image samples failed; see report.")
    return 1 if bugs_found else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
