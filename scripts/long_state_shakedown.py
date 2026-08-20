from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
REPORT_PATH = DATA_DIR / "logs" / "LONG_STATE_SHAKEDOWN_REPORT.md"
BACKEND_URL = "http://localhost:8001"
SESSION_TITLE = "StoryDriver Long State Shakedown"

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

from app.database import db_session  # noqa: E402
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


SCENES = [
    (
        "Mara was introduced as a black-haired scout in a green cloak, guiding Elias along the forest road toward the old chapel.",
        {
            "character_updates": [
                {
                    "character_name": "Mara Shakedown",
                    "state_type": "appearance",
                    "key": "current_outfit",
                    "value": "wearing a green cloak",
                    "action": "replace",
                    "confidence": 0.95,
                }
            ],
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "forest road outside the old chapel",
                    "confidence": 0.92,
                }
            ],
            "world_updates": [
                {
                    "state_type": "world",
                    "key": "old_chapel_origin",
                    "value": "the old chapel once belonged to the Star Monastery",
                    "confidence": 0.9,
                }
            ],
        },
    ),
    (
        "Before entering the ravine, Mara changed out of the green cloak and fastened a red cloak over her travel leathers.",
        {
            "character_updates": [
                {
                    "character_name": "Mara Shakedown",
                    "state_type": "appearance",
                    "key": "current_outfit",
                    "value": "wearing a red cloak over travel leathers",
                    "action": "replace",
                    "confidence": 0.98,
                }
            ]
        },
    ),
    (
        "A thrown knife clipped Mara's right knee in an abandoned earlier version of this scene.",
        {
            "character_updates": [
                {
                    "character_name": "Mara Shakedown",
                    "state_type": "injury",
                    "key": "injury_right_knee",
                    "value": "right knee cut from a thrown knife",
                    "action": "update",
                    "confidence": 0.92,
                }
            ]
        },
        "superseded",
    ),
    (
        "The revised scene has Mara take the bandit arrow across her left shoulder, and Elias binds it with a clean strip of linen.",
        {
            "character_updates": [
                {
                    "character_name": "Mara Shakedown",
                    "state_type": "injury",
                    "key": "injury_left_shoulder",
                    "value": "left shoulder bandaged after a bandit arrow graze",
                    "action": "update",
                    "confidence": 0.97,
                }
            ]
        },
        "latest",
    ),
    (
        "Elias found a bronze compass under the chapel stair and hid it in his satchel before Mara could see the engraving.",
        {
            "object_updates": [
                {
                    "object_key": "bronze_compass",
                    "name": "bronze compass",
                    "state_type": "secret_object",
                    "value": "hidden in Elias satchel",
                    "owner_character_name": "Elias Shakedown",
                    "confidence": 0.96,
                }
            ],
            "plot_thread_updates": [
                {
                    "thread_key": "elias_hides_bronze_compass",
                    "title": "Elias hides the bronze compass",
                    "status": "active",
                    "content": "Elias is hiding the bronze compass from Mara.",
                    "confidence": 0.95,
                }
            ],
        },
    ),
    (
        "Mara caught Elias lying about the satchel and trusted him less, though she still needed his knowledge of the chapel seals.",
        {
            "relationship_updates": [
                {
                    "character_a_name": "Mara Shakedown",
                    "character_b_name": "Elias Shakedown",
                    "relationship_key": "trust",
                    "content": "Mara trusts Elias less after catching him lying about his satchel.",
                    "confidence": 0.92,
                }
            ]
        },
    ),
    (
        "They crossed into the ruined chapel nave, where rain fell through the broken roof onto old star-metal tiles.",
        {
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "ruined chapel nave with rain falling through the broken roof",
                    "confidence": 0.96,
                }
            ]
        },
    ),
    (
        "Elias revealed that star-metal compasses point toward sealed vows, not north, and Mara realized the chapel was built around promises.",
        {
            "world_updates": [
                {
                    "state_type": "world",
                    "key": "star_metal_compasses",
                    "value": "star-metal compasses point toward sealed vows instead of north",
                    "confidence": 0.94,
                }
            ],
            "character_updates": [
                {
                    "character_name": "Mara Shakedown",
                    "state_type": "inventory",
                    "key": "silver_dagger",
                    "value": "maybe carrying a silver dagger",
                    "confidence": 0.25,
                }
            ],
        },
    ),
    (
        "Mara promised Elias she would keep the prince's true name secret until dawn if he helped her leave the chapel alive.",
        {
            "plot_thread_updates": [
                {
                    "thread_key": "mara_promises_to_hide_prince_name",
                    "title": "Mara promises to hide the prince's true name",
                    "status": "active",
                    "content": "Mara promised Elias she will keep the prince's true name secret until dawn.",
                    "confidence": 0.96,
                }
            ]
        },
    ),
    (
        "Mara passed the iron chapel key to Elias so he could open the choir gate while she guarded the aisle.",
        {
            "object_updates": [
                {
                    "object_key": "iron_chapel_key",
                    "name": "iron chapel key",
                    "state_type": "held_item",
                    "value": "carried by Elias after Mara handed it over",
                    "owner_character_name": "Elias Shakedown",
                    "confidence": 0.95,
                }
            ]
        },
    ),
    (
        "Elias returned the iron chapel key to Mara after the gate opened, and she tucked it inside the pocket under her red cloak.",
        {
            "object_updates": [
                {
                    "object_key": "iron_chapel_key",
                    "name": "iron chapel key",
                    "state_type": "held_item",
                    "value": "back with Mara in the pocket under her red cloak",
                    "owner_character_name": "Mara Shakedown",
                    "confidence": 0.96,
                }
            ],
            "character_updates": [
                {
                    "character_name": "Mara Shakedown",
                    "state_type": "appearance",
                    "key": "current_outfit",
                    "value": "wearing a red cloak over travel leathers",
                    "action": "replace",
                    "confidence": 0.96,
                }
            ],
        },
    ),
    (
        "They left the chapel behind and took the ridge path toward camp while the secret compass stayed hidden in Elias's satchel.",
        {
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "ridge path away from the chapel",
                    "confidence": 0.93,
                }
            ],
            "object_updates": [
                {
                    "object_key": "bronze_compass",
                    "name": "bronze compass",
                    "state_type": "secret_object",
                    "value": "still hidden in Elias satchel",
                    "owner_character_name": "Elias Shakedown",
                    "confidence": 0.95,
                }
            ],
        },
    ),
    (
        "Back at camp, Elias cleaned Mara's bandaged left shoulder while she kept the red cloak wrapped around her and the iron key close.",
        {
            "scene_updates": [
                {
                    "state_type": "scene",
                    "key": "current_location",
                    "value": "camp where Mara's shoulder wound is being treated",
                    "confidence": 0.97,
                }
            ],
            "character_updates": [
                {
                    "character_name": "Mara Shakedown",
                    "state_type": "injury",
                    "key": "injury_left_shoulder",
                    "value": "left shoulder remains bandaged and needs care at camp",
                    "action": "update",
                    "confidence": 0.97,
                }
            ],
            "relationship_updates": [
                {
                    "character_a_name": "Mara Shakedown",
                    "character_b_name": "Elias Shakedown",
                    "relationship_key": "trust",
                    "content": "Mara distrusts Elias's secrecy but accepts his help treating her wound.",
                    "confidence": 0.9,
                }
            ],
        },
    ),
]


def request_json(path: str) -> dict:
    request = urllib.request.Request(f"{BACKEND_URL}{path}", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def ensure_character(db: sqlite3.Connection, name: str, role: str, appearance: str) -> str:
    row = db.execute("SELECT id, role, appearance, current_state FROM characters WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
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
            "Controlled long-shakedown character. Base card should not be rewritten by live state.",
            appearance,
            "Available for long state shakedown.",
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


def ensure_session_and_cards() -> tuple[str, dict[str, str]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        session = db.execute("SELECT id FROM sessions WHERE title = ?", (SESSION_TITLE,)).fetchone()
        if session:
            session_id = session["id"]
        else:
            session_id = str(uuid4())
            db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, SESSION_TITLE))
        mara_id = ensure_character(db, "Mara Shakedown", "Scout", "short black hair, scar over left eyebrow")
        elias_id = ensure_character(db, "Elias Shakedown", "Scholar", "tall scholar with tired eyes and ink-stained fingers")
        attach_character(db, session_id, mara_id)
        attach_character(db, session_id, elias_id)
        world = db.execute("SELECT id FROM world_notes WHERE session_id = ?", (session_id,)).fetchone()
        if world:
            db.execute(
                """
                UPDATE world_notes
                SET setting = ?, tone = ?, rules = ?, locations = ?
                WHERE session_id = ?
                """,
                (
                    "rainy low-fantasy borderlands around a ruined star chapel",
                    "grounded, tense, cinematic, wary",
                    "star-metal compasses respond to vows and secrets",
                    "forest road; ruined chapel; ridge path; camp",
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
                    "rainy low-fantasy borderlands around a ruined star chapel",
                    "grounded, tense, cinematic, wary",
                    "star-metal compasses respond to vows and secrets",
                    "forest road; ruined chapel; ridge path; camp",
                ),
            )
    return session_id, {"mara": mara_id, "elias": elias_id}


def insert_scene(session_id: str, text: str, index: int, *, scene_id: str | None = None, version_index: int = 1) -> tuple[str, str]:
    with sqlite3.connect(DB_PATH) as db:
        scene_id = scene_id or str(uuid4())
        version_id = str(uuid4())
        if version_index == 1:
            db.execute(
                """
                INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
                VALUES (?, ?, ?, ?, 'continue')
                """,
                (scene_id, session_id, f"Long state shakedown scene {index}.", text),
            )
        else:
            db.execute(
                "UPDATE scenes SET generated_text = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (text, scene_id),
            )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, 'continue', ?)
            """,
            (version_id, scene_id, session_id, f"Long state shakedown scene {index}.", text, version_index),
        )
        db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (session_id,),
        )
    return scene_id, version_id


def merge_payload(session_id: str, scene_id: str, version_id: str, payload: dict) -> list[str]:
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


def run_controlled_story() -> dict:
    session_id, _characters = ensure_session_and_cards()
    all_warnings: list[str] = []
    scene_records: list[dict[str, str]] = []
    first_version_scene_id = None
    for index, item in enumerate(SCENES, start=1):
        text, payload, *kind = item
        if kind and kind[0] == "superseded":
            first_version_scene_id, version_id = insert_scene(session_id, text, index)
            all_warnings.extend(merge_payload(session_id, first_version_scene_id, version_id, payload))
            scene_records.append({"scene_id": first_version_scene_id, "version_id": version_id, "text": text, "kind": "superseded"})
            continue
        if kind and kind[0] == "latest" and first_version_scene_id:
            scene_id, version_id = insert_scene(session_id, text, index, scene_id=first_version_scene_id, version_index=2)
        else:
            scene_id, version_id = insert_scene(session_id, text, index)
        all_warnings.extend(merge_payload(session_id, scene_id, version_id, payload))
        scene_records.append({"scene_id": scene_id, "version_id": version_id, "text": text, "kind": kind[0] if kind else "normal"})
    return {"session_id": session_id, "scenes": scene_records, "warnings": all_warnings}


def query_state(session_id: str) -> dict:
    with db_session() as db:
        rows = {
            "characters": db.execute(
                """
                SELECT character_name, state_type, key, value, archived, source_scene_id, source_version_id
                FROM character_live_state
                WHERE session_id = ?
                ORDER BY archived ASC, character_name ASC, key ASC
                """,
                (session_id,),
            ).fetchall(),
            "relationships": db.execute(
                """
                SELECT character_a_name, character_b_name, relationship_key, content, archived
                FROM relationship_state
                WHERE session_id = ?
                ORDER BY archived ASC, updated_at DESC
                """,
                (session_id,),
            ).fetchall(),
            "objects": db.execute(
                """
                SELECT name, object_key, state_type, value, owner_character_name, archived
                FROM object_state
                WHERE session_id = ?
                ORDER BY archived ASC, object_key ASC
                """,
                (session_id,),
            ).fetchall(),
            "threads": db.execute(
                """
                SELECT title, thread_key, status, content
                FROM plot_threads
                WHERE session_id = ?
                ORDER BY status ASC, updated_at DESC
                """,
                (session_id,),
            ).fetchall(),
            "scene_state": db.execute(
                """
                SELECT scene_id, version_id, key, value, archived
                FROM scene_live_state
                WHERE session_id = ?
                ORDER BY archived ASC, updated_at DESC
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
    return {
        key: [{column: row[column] for column in row.keys()} for row in value]
        if isinstance(value, list)
        else ({column: value[column] for column in value.keys()} if value else None)
        for key, value in rows.items()
    }


def latest_scene_prompt_dicts(session_id: str, limit: int = 6) -> list[dict[str, str]]:
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


def assert_shakedown(state: dict, prose_context: str, visual_context: str) -> list[str]:
    failures: list[str] = []
    active_character_text = "\n".join(
        f"{row['character_name']} {row['state_type']} {row['key']} {row['value']}"
        for row in state["characters"]
        if not row["archived"]
    ).lower()
    active_scene_text = "\n".join(row["value"] for row in state["scene_state"] if not row["archived"]).lower()
    object_text = "\n".join(
        f"{row['name']} {row['value']} {row['owner_character_name']}"
        for row in state["objects"]
        if not row["archived"]
    ).lower()
    all_prompt_text = f"{prose_context}\n{visual_context}".lower()
    if "red cloak" not in active_character_text:
        failures.append("Mara's current red cloak was not active.")
    if "green cloak" in active_character_text:
        failures.append("Archived green cloak leaked as active outfit.")
    if "left shoulder" not in active_character_text:
        failures.append("Left shoulder injury was not active.")
    if "right knee" in active_character_text:
        failures.append("Superseded right knee injury remained active.")
    if "bronze compass" not in object_text or "elias" not in object_text:
        failures.append("Bronze compass secret/owner state was not preserved.")
    if "iron chapel key" not in object_text or "mara" not in object_text:
        failures.append("Iron chapel key ownership did not update back to Mara.")
    if "camp" not in active_scene_text:
        failures.append("Camp did not become the current active location.")
    if "chapel nave" in active_scene_text:
        failures.append("Old chapel nave location remained active.")
    if "silver dagger" in all_prompt_text:
        failures.append("Low-confidence silver dagger leaked into prompt context.")
    for needle in ("red cloak", "left shoulder", "bronze compass", "camp"):
        if needle not in all_prompt_text:
            failures.append(f"Prompt contexts did not include expected continuity: {needle}.")
    return failures


def markdown_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    if not rows:
        return "_None._"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        values = [str(row.get(column, "")).replace("\n", " ")[:220] for column in columns]
        body.append("| " + " | ".join(values) + " |")
    return "\n".join([header, divider, *body])


async def main() -> int:
    DATA_DIR.joinpath("logs").mkdir(parents=True, exist_ok=True)
    try:
        health = request_json("/health")
    except Exception as error:
        REPORT_PATH.write_text(f"# Long State Shakedown Report\n\nBackend unavailable: {error}\n", encoding="utf-8")
        print(f"Backend unavailable: {error}")
        return 1
    if not health.get("ok"):
        print(f"Backend health is not OK: {health}")
        return 1

    run = run_controlled_story()
    session_id = run["session_id"]
    summary_result = await maybe_update_session_summary(session_id, force=True)
    state = query_state(session_id)
    prose_context, prompt_item_count = format_story_state_for_prompt(session_id)
    visual_context = format_visual_story_state_for_prompt(session_id)
    recent_scenes = latest_scene_prompt_dicts(session_id)
    summary = latest_summary(session_id) or {}
    prompt = build_scene_prompt(
        session_id=session_id,
        director_note="Continue from camp. Keep Mara's current wound, outfit, objects, and Elias's secret straight.",
        mode="continue",
        recent_scenes=recent_scenes,
        session_summary=summary.get("summary_text"),
    )
    model_settings = load_model_settings(resolve_active_preset=True)
    failures = assert_shakedown(state, prose_context, visual_context)
    warning_lines = [warning for warning in run["warnings"] if warning]

    report = [
        "# Long State Shakedown Report",
        "",
        f"- Updated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Session: `{session_id}`",
        f"- Story: {SESSION_TITLE}",
        f"- Scenes inserted this run: {len(run['scenes'])}",
        f"- Prompt state items included: {prompt_item_count}",
        f"- Active prompt state items total: {active_prompt_state_count(session_id)}",
        f"- Archived/resolved state items total: {archived_state_count(session_id)}",
        f"- Full next user prompt size: {len(prompt)} characters",
        f"- Story state context size: {len(prose_context)} characters",
        f"- Visual state context size: {len(visual_context)} characters",
        f"- System prompt size: {len(model_settings.system_prompt or '')} characters",
        "",
        "## Scenes Tested",
        "",
        markdown_table(
            [
                {
                    "index": str(index),
                    "kind": scene["kind"],
                    "scene_id": scene["scene_id"],
                    "version_id": scene["version_id"],
                    "text": scene["text"],
                }
                for index, scene in enumerate(run["scenes"], start=1)
            ],
            ["index", "kind", "scene_id", "version_id", "text"],
        ),
        "",
        "## State Extracted / Merged",
        "",
        "This shakedown used controlled extractor-shaped JSON payloads to isolate merge, archive, prompt, summary, and image-context behavior. The separate `state_engine_smoke_test.bat` exercises live LM Studio extraction.",
        "",
        "### Active Character State",
        markdown_table([row for row in state["characters"] if not row["archived"]], ["character_name", "state_type", "key", "value"]),
        "",
        "### Active Objects",
        markdown_table([row for row in state["objects"] if not row["archived"]], ["name", "state_type", "value", "owner_character_name"]),
        "",
        "### Active Scene State",
        markdown_table([row for row in state["scene_state"] if not row["archived"]], ["key", "value"]),
        "",
        "### Relationships",
        markdown_table([row for row in state["relationships"] if not row["archived"]], ["character_a_name", "character_b_name", "relationship_key", "content"]),
        "",
        "### Plot Threads",
        markdown_table(state["threads"], ["title", "status", "content"]),
        "",
        "## Summary Behavior",
        "",
        f"- Summary result: `{summary_result.get('status')}`",
        f"- Summary to scene: `{summary_result.get('to_scene_id') or (summary or {}).get('to_scene_id') or ''}`",
        f"- Summary length: {len((summary or {}).get('summary_text') or '')} characters",
        f"- Summary error: {summary_result.get('error') or 'none'}",
        "",
        "## Prompt Context Audit",
        "",
        f"- Recent scenes included directly: {len(recent_scenes)}",
        f"- Full prompt exceeds 30k chars: {'yes' if len(prompt) > 30000 else 'no'}",
        f"- Full prompt exceeds 20k chars: {'yes' if len(prompt) > 20000 else 'no'}",
        "",
        "```text",
        prose_context[:5000],
        "```",
        "",
        "## Image Prompt Continuity Notes",
        "",
        "```text",
        visual_context[:3000],
        "```",
        "",
        "## State Mistakes Found",
        "",
        "\n".join(f"- {failure}" for failure in failures) if failures else "- None in controlled merge/prompt shakedown.",
        "",
        "## Merge Warnings",
        "",
        "\n".join(f"- {warning}" for warning in warning_lines[:40]) if warning_lines else "- None.",
        "",
        "## Remaining Drift Risks",
        "",
        "- Live LM Studio extraction can still omit facts that the controlled merge payload includes; continue checking the State panel on real stories.",
        "- Summaries are additive and safe, but should be reviewed after a longer creative run for tone and specificity.",
        "- Versioned-scene state now archives prior same-scene version state, but a rewritten scene that omits an important still-true fact may need re-extraction or a manual State panel correction.",
        "",
        "## Verdict",
        "",
        "- Stable enough for real-use shakedown if the live extractor keeps producing concise supported facts.",
        "- Ready for a focused Story State review/edit UI pass before deeper memory v2.",
    ]
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"Long shakedown session: {session_id}")
    print(f"Report: {REPORT_PATH}")
    print(f"Summary status: {summary_result.get('status')}")
    print(f"Prompt items included: {prompt_item_count}")
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Long state shakedown passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
