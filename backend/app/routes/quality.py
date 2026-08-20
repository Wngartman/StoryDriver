from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status

from app.config import DATA_DIR
from app.database import db_session
from app.schemas import (
    QualityHintRead,
    QualityHintsResponse,
    QualityNoteCreate,
    QualityNoteRead,
    QualityNoteUpdate,
    SceneQualityChecklistRead,
    SceneQualityChecklistUpdate,
)
from app.services.auto_title import is_generic_title
from app.tts.kokoro import KokoroClient
from app.services.scene_image_prompts import row_to_scene_image_prompt
from app.memory.summaries import summary_status
from app.settings.store import image_generation_is_paused, load_image_settings, load_tts_settings


router = APIRouter(prefix="/sessions/{session_id}/quality", tags=["quality"])

NOTE_FIELDS = {"note_type", "severity", "status", "note_text", "scene_id", "version_id"}
CHECKLIST_FIELDS = {
    "director_note_followed",
    "length_okay",
    "no_assistant_tone",
    "continuity_okay",
    "state_extracted_okay",
    "image_prompt_okay",
    "narration_okay",
    "image_generation_okay",
    "notes",
}
WORLD_FIELDS = ("setting", "tone", "rules", "locations", "factions", "conflicts", "history")
LOG_DIR = DATA_DIR / "logs"
REPETITION_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "because",
    "before",
    "behind",
    "between",
    "could",
    "down",
    "even",
    "from",
    "into",
    "just",
    "like",
    "look",
    "more",
    "only",
    "over",
    "said",
    "scene",
    "she",
    "that",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "through",
    "under",
    "upon",
    "very",
    "were",
    "what",
    "when",
    "where",
    "with",
    "would",
    "your",
}


def json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_runtime_speed_check() -> dict[str, Any]:
    path = LOG_DIR / "LMSTUDIO_CURRENT_SPEED_TEST_LATEST.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    direct = {}
    for item in data.get("direct_results") or []:
        if item.get("label") == "direct_minimal_current":
            direct = item
            break
    return {
        "checked_at": parse_timestamp(data.get("generated_at")),
        "label": data.get("label"),
        "direct_tps": direct.get("visible_tokens_per_second_estimated"),
        "hidden_reasoning_chars": direct.get("reasoning_chars"),
    }


def has_newer_healthy_speed_check(scene_created_at: str | None) -> bool:
    latest = latest_runtime_speed_check()
    checked_at = latest.get("checked_at")
    scene_time = parse_timestamp(scene_created_at)
    if checked_at is None or scene_time is None or checked_at < scene_time:
        return False
    try:
        direct_tps = float(latest.get("direct_tps"))
    except (TypeError, ValueError):
        return False
    try:
        hidden_reasoning = int(latest.get("hidden_reasoning_chars") or 0)
    except (TypeError, ValueError):
        hidden_reasoning = 0
    return direct_tps >= 70 and hidden_reasoning <= 20


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def scene_speed_currently_ok(stats: dict[str, Any]) -> bool:
    visible_tps = stats.get("visible_prose_tokens_per_second") or stats.get("tokens_per_second")
    try:
        visible_tps_float = float(visible_tps)
    except (TypeError, ValueError):
        return False
    try:
        reasoning_chars = int(stats.get("reasoning_chars") or 0)
    except (TypeError, ValueError):
        reasoning_chars = 0
    return visible_tps_float >= 70 and reasoning_chars <= 20


def repeated_focus_hint(text: str) -> str:
    words = [
        word.lower()
        for word in re.findall(r"\b[a-zA-Z][a-zA-Z'-]{4,}\b", text or "")
        if word.lower() not in REPETITION_STOPWORDS
    ]
    if len(words) < 700:
        return ""
    counts: dict[str, int] = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    word, count = max(counts.items(), key=lambda item: item[1], default=("", 0))
    if count >= 28 and count / max(1, len(words)) >= 0.045:
        return word
    return ""


def ensure_session(db, session_id: str) -> None:
    row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")


def ensure_scene_target(
    db,
    session_id: str,
    scene_id: str | None,
    version_id: str | None = None,
) -> None:
    if not scene_id:
        return
    scene = db.execute(
        "SELECT id FROM scenes WHERE id = ? AND session_id = ?",
        (scene_id, session_id),
    ).fetchone()
    if scene is None:
        raise HTTPException(status_code=404, detail="Scene not found for this story")
    if version_id:
        version = db.execute(
            """
            SELECT id
            FROM scene_versions
            WHERE id = ? AND scene_id = ? AND session_id = ?
            """,
            (version_id, scene_id, session_id),
        ).fetchone()
        if version is None:
            raise HTTPException(status_code=404, detail="Scene version not found for this story")


def bool_from_row(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


def bool_to_db(value: bool | None):
    if value is None:
        return None
    return 1 if value else 0


def row_to_note(row) -> QualityNoteRead:
    return QualityNoteRead(
        id=row["id"],
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        version_id=row["version_id"],
        note_type=row["note_type"],
        severity=row["severity"],
        status=row["status"],
        note_text=row["note_text"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_checklist(row) -> SceneQualityChecklistRead:
    return SceneQualityChecklistRead(
        id=row["id"],
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        version_id=row["version_id"] or "",
        director_note_followed=bool_from_row(row["director_note_followed"]),
        length_okay=bool_from_row(row["length_okay"]),
        no_assistant_tone=bool_from_row(row["no_assistant_tone"]),
        continuity_okay=bool_from_row(row["continuity_okay"]),
        state_extracted_okay=bool_from_row(row["state_extracted_okay"]),
        image_prompt_okay=bool_from_row(row["image_prompt_okay"]),
        narration_okay=bool_from_row(row["narration_okay"]),
        image_generation_okay=bool_from_row(row["image_generation_okay"]),
        notes=row["notes"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def empty_checklist(session_id: str, scene_id: str, version_id: str = "") -> SceneQualityChecklistRead:
    now = ""
    return SceneQualityChecklistRead(
        id="",
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id or "",
        created_at=now,
        updated_at=now,
    )


def add_hint(
    hints: list[QualityHintRead],
    hint_id: str,
    hint_type: str,
    severity: str,
    message: str,
    *,
    scene_id: str | None = None,
    version_id: str | None = None,
    action_label: str = "",
) -> None:
    hints.append(
        QualityHintRead(
            id=hint_id,
            hint_type=hint_type,
            severity=severity,
            message=message,
            scene_id=scene_id,
            version_id=version_id,
            action_label=action_label,
        )
    )


def latest_version_row(db, session_id: str):
    return db.execute(
        """
        SELECT id, scene_id, director_note, generated_text, generation_stats_json, mode, created_at
        FROM scene_versions
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()


def add_generation_hints(db, session_id: str, hints: list[QualityHintRead]) -> None:
    row = latest_version_row(db, session_id)
    if row is None:
        return
    stats = json_dict(row["generation_stats_json"])
    text = row["generated_text"] or ""
    director_note = row["director_note"] or ""
    writing_length = stats.get("writing_length") if isinstance(stats.get("writing_length"), dict) else {}
    length_mode = str(writing_length.get("mode") or stats.get("writing_length_mode") or "").lower()
    requested_chapter = length_mode == "chapter" or any(
        token in director_note.lower()
        for token in ("chapter", "first chapter", "long", "7-12 minutes", "7–12 minutes", "full chapter")
    )
    words = int(stats.get("word_count") or word_count(text))
    if requested_chapter and words and words < 1500:
        add_hint(
            hints,
            "chapter-too-short",
            "writing",
            "medium",
            f"Latest chapter-length passage is only about {words:,} words.",
            scene_id=row["scene_id"],
            version_id=row["id"],
            action_label="Review length mode",
        )

    runtime_rechecked_ok = has_newer_healthy_speed_check(row["created_at"])
    visible_tps = stats.get("visible_prose_tokens_per_second") or stats.get("tokens_per_second")
    if isinstance(visible_tps, (int, float)) and visible_tps < 35 and not runtime_rechecked_ok:
        created_at = row["created_at"] or "unknown time"
        add_hint(
            hints,
            "visible-speed-low",
            "speed",
            "medium",
            (
                f"Latest saved scene visible prose speed was {visible_tps:.1f} t/s ({created_at}). "
                "This is historical scene data, not a fresh runtime test. Run verify_gemma_fast_runtime.bat, "
                "then recover_storydriver_speed.bat --diagnose-only if speed is still low."
            ),
            scene_id=row["scene_id"],
            version_id=row["id"],
            action_label="Verify Gemma runtime",
        )
    first_token = stats.get("first_token_latency_seconds")
    if isinstance(first_token, (int, float)) and first_token > 12 and not runtime_rechecked_ok and not scene_speed_currently_ok(stats):
        created_at = row["created_at"] or "unknown time"
        add_hint(
            hints,
            "first-token-slow",
            "speed",
            "medium",
            (
                f"Latest saved scene first visible token took {first_token:.1f}s ({created_at}). "
                "If this repeats on a fresh scene, run recover_storydriver_speed.bat --diagnose-only; "
                "use --confirm only when you want the no-reboot recovery path to act."
            ),
            scene_id=row["scene_id"],
            version_id=row["id"],
            action_label="Recover if repeated",
        )
    reasoning_chars = stats.get("reasoning_chars")
    if isinstance(reasoning_chars, (int, float)) and reasoning_chars > 1000:
        add_hint(
            hints,
            "reasoning-heavy",
            "speed",
            "medium",
            f"Latest generation streamed about {int(reasoning_chars):,} hidden reasoning characters.",
            scene_id=row["scene_id"],
            version_id=row["id"],
            action_label="Check prompt template",
        )
    repeated_word = repeated_focus_hint(text)
    if repeated_word:
        add_hint(
            hints,
            "possible-scene-fixation",
            "writing",
            "low",
            f"Latest long passage may be over-fixating on '{repeated_word}'. Review only if it reads repetitive.",
            scene_id=row["scene_id"],
            version_id=row["id"],
            action_label="Review latest scene",
        )


def add_title_hints(db, session_id: str, hints: list[QualityHintRead]) -> None:
    session = db.execute(
        """
        SELECT id, title, title_source, auto_title_status, auto_title_error
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if session is None:
        return
    scene_count = db.execute(
        "SELECT COUNT(*) AS count FROM scenes WHERE session_id = ?",
        (session_id,),
    ).fetchone()["count"]
    status_text = session["auto_title_status"] or "skipped"
    if status_text == "failed":
        message = "Automatic title generation failed. Writing is unaffected."
        if session["auto_title_error"]:
            message += f" {str(session['auto_title_error'])[:160]}"
        add_hint(
            hints,
            "auto-title-failed",
            "writing",
            "low",
            message,
            action_label="Retry title",
        )
        return
    if scene_count and session["title_source"] != "user_set" and is_generic_title(session["title"]) and status_text != "pending":
        add_hint(
            hints,
            "auto-title-missing",
            "writing",
            "low",
            "This story still has a placeholder title after a saved scene.",
            action_label="Retry title",
        )


def add_summary_hints(db, session_id: str, hints: list[QualityHintRead]) -> None:
    status_info = summary_status(session_id)
    scene_count = int(status_info.get("scene_count") or 0)
    if scene_count < 9:
        return
    status_text = status_info.get("status") or "none"
    if status_text == "failed":
        message = "Rolling story summary failed. Recent scenes still work, but long-session recall may be weaker."
        if status_info.get("error"):
            message += f" {str(status_info['error'])[:160]}"
        add_hint(
            hints,
            "summary-failed",
            "state",
            "medium",
            message,
            action_label="Refresh summary",
        )
        return
    if status_info.get("needs_summary"):
        scenes_since = int(status_info.get("scenes_since_summary") or scene_count)
        add_hint(
            hints,
            "summary-stale",
            "state",
            "low",
            f"Rolling summary is {scenes_since} scene(s) behind. Refresh it before a long continuation if continuity feels thin.",
            action_label="Refresh summary",
        )


def add_story_state_hints(db, session_id: str, hints: list[QualityHintRead]) -> None:
    latest = latest_version_row(db, session_id)
    latest_run = db.execute(
        """
        SELECT id, scene_id, version_id, status, error
        FROM story_state_runs
        WHERE session_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if latest_run and latest_run["status"] == "failed":
        message = "Latest Story State extraction failed."
        if latest_run["error"]:
            message += f" {str(latest_run['error'])[:140]}"
        add_hint(
            hints,
            "state-extraction-failed",
            "state",
            "high",
            message,
            scene_id=latest_run["scene_id"],
            version_id=latest_run["version_id"],
            action_label="Re-extract selected scene",
        )
    elif latest:
        run_for_latest = db.execute(
            """
            SELECT id
            FROM story_state_runs
            WHERE session_id = ? AND scene_id = ? AND COALESCE(version_id, '') = COALESCE(?, '')
              AND status = 'completed'
            LIMIT 1
            """,
            (session_id, latest["scene_id"], latest["id"]),
        ).fetchone()
        if run_for_latest is None:
            add_hint(
                hints,
                "state-extraction-stale",
                "state",
                "low",
                "Latest scene version has not completed Story State extraction yet.",
                scene_id=latest["scene_id"],
                version_id=latest["id"],
                action_label="Wait or re-extract",
            )

    conflict_total = 0
    for table, archive_clause in (
        ("character_live_state", "AND COALESCE(archived, 0) = 0"),
        ("relationship_state", "AND COALESCE(archived, 0) = 0"),
        ("world_live_state", "AND COALESCE(archived, 0) = 0"),
        ("scene_live_state", "AND COALESCE(archived, 0) = 0"),
        ("object_state", "AND COALESCE(archived, 0) = 0"),
        ("plot_threads", "AND COALESCE(status, 'active') != 'archived'"),
        ("emotional_memories", "AND COALESCE(archived, 0) = 0"),
    ):
        row = db.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM {table}
            WHERE session_id = ?
              {archive_clause}
              AND COALESCE(disabled, 0) = 0
              AND COALESCE(review_status, 'ok') != 'ok'
            """,
            (session_id,),
        ).fetchone()
        conflict_total += int(row["count"] if row else 0)
    if conflict_total:
        add_hint(
            hints,
            "state-needs-review",
            "state",
            "medium" if conflict_total < 4 else "high",
            f"{conflict_total} active Story State item(s) need review.",
            action_label="Open Story State",
        )


def add_world_and_character_hints(db, session_id: str, hints: list[QualityHintRead]) -> None:
    world = db.execute(
        "SELECT setting, tone, rules, locations, factions, conflicts, history FROM world_notes WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if world is None or not any(str(world[field] or "").strip() for field in WORLD_FIELDS):
        add_hint(
            hints,
            "world-notes-empty",
            "state",
            "low",
            "This story has no world notes yet.",
            action_label="Add world notes",
        )

    low_visual_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM session_characters AS sc
        JOIN characters AS c ON c.id = sc.character_id
        LEFT JOIN character_visual_profiles AS v ON v.character_id = c.id
        WHERE sc.session_id = ?
          AND sc.is_active = 1
          AND c.auto_created = 1
          AND (
            TRIM(COALESCE(c.appearance, '')) = ''
            AND TRIM(COALESCE(c.image_prompt, '')) = ''
            AND TRIM(COALESCE(v.base_visual_description, '')) = ''
            AND TRIM(COALESCE(v.face_description, '')) = ''
            AND TRIM(COALESCE(v.hair, '')) = ''
            AND TRIM(COALESCE(v.body_build, '')) = ''
            AND TRIM(COALESCE(v.default_outfit, '')) = ''
          )
        """,
        (session_id,),
    ).fetchone()
    count = int(low_visual_count["count"] if low_visual_count else 0)
    if count:
        add_hint(
            hints,
            "auto-character-low-visual-detail",
            "character",
            "low",
            f"{count} auto-created character(s) have little or no visual profile detail.",
            action_label="Review characters",
        )


def add_image_prompt_hints(db, session_id: str, hints: list[QualityHintRead]) -> None:
    if image_generation_is_paused(load_image_settings()):
        return
    row = db.execute(
        """
        SELECT *
        FROM scene_image_prompts
        WHERE session_id = ?
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return
    if (row["status"] or "") == "failed":
        add_hint(
            hints,
            "image-prompt-failed",
            "image",
            "medium",
            "Latest image prompt generation failed.",
            scene_id=row["scene_id"],
            version_id=row["version_id"],
            action_label="Preview image prompt",
        )
        return
    prompt = row_to_scene_image_prompt(row)
    important_warnings = [
        warning
        for warning in prompt.quality_warnings
        if "workflow" in warning.lower()
        or "longer than" in warning.lower()
        or "no clear visual beat" in warning.lower()
        or "debug" in warning.lower()
    ]
    if important_warnings:
        add_hint(
            hints,
            "image-prompt-warning",
            "image",
            "medium",
            important_warnings[0],
            scene_id=prompt.scene_id,
            version_id=prompt.version_id,
            action_label="Preview image prompt",
        )


async def add_tts_hints(hints: list[QualityHintRead]) -> None:
    settings = load_tts_settings()
    if settings.tts_provider != "kokoro":
        return
    try:
        reachable, error = await KokoroClient(settings.kokoro_base_url).is_reachable()
    except Exception as exc:
        reachable = False
        error = str(exc)
    if not reachable:
        add_hint(
            hints,
            "kokoro-offline",
            "tts",
            "medium",
            f"Kokoro narration is offline at {settings.kokoro_base_url}. Scene writing is unaffected.",
            action_label="Start Kokoro",
        )


@router.get("/notes", response_model=list[QualityNoteRead])
def list_quality_notes(
    session_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[QualityNoteRead]:
    with db_session() as db:
        ensure_session(db, session_id)
        params: list[Any] = [session_id]
        clause = ""
        if status_filter:
            clause = "AND status = ?"
            params.append(status_filter)
        rows = db.execute(
            f"""
            SELECT *
            FROM session_quality_notes
            WHERE session_id = ?
            {clause}
            ORDER BY
              CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
              created_at DESC
            """,
            params,
        ).fetchall()
    return [row_to_note(row) for row in rows]


@router.post("/notes", response_model=QualityNoteRead, status_code=status.HTTP_201_CREATED)
def create_quality_note(session_id: str, payload: QualityNoteCreate) -> QualityNoteRead:
    note_id = str(uuid4())
    note_text = payload.note_text.strip()
    with db_session() as db:
        ensure_session(db, session_id)
        ensure_scene_target(db, session_id, payload.scene_id, payload.version_id)
        db.execute(
            """
            INSERT INTO session_quality_notes (
                id, session_id, scene_id, version_id, note_type, severity, status, note_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                session_id,
                payload.scene_id,
                payload.version_id,
                payload.note_type,
                payload.severity,
                payload.status,
                note_text,
            ),
        )
        row = db.execute("SELECT * FROM session_quality_notes WHERE id = ?", (note_id,)).fetchone()
    return row_to_note(row)


@router.patch("/notes/{note_id}", response_model=QualityNoteRead)
def update_quality_note(session_id: str, note_id: str, payload: QualityNoteUpdate) -> QualityNoteRead:
    updates = payload.model_dump(exclude_unset=True)
    with db_session() as db:
        ensure_session(db, session_id)
        existing = db.execute(
            "SELECT * FROM session_quality_notes WHERE id = ? AND session_id = ?",
            (note_id, session_id),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Quality note not found")
        scene_id = updates.get("scene_id", existing["scene_id"])
        version_id = updates.get("version_id", existing["version_id"])
        ensure_scene_target(db, session_id, scene_id, version_id)
        values: dict[str, Any] = {}
        for field, value in updates.items():
            if field not in NOTE_FIELDS:
                continue
            values[field] = value.strip() if isinstance(value, str) else value
        if values:
            assignments = ", ".join(f"{field} = ?" for field in values)
            db.execute(
                f"UPDATE session_quality_notes SET {assignments} WHERE id = ? AND session_id = ?",
                (*values.values(), note_id, session_id),
            )
        row = db.execute(
            "SELECT * FROM session_quality_notes WHERE id = ? AND session_id = ?",
            (note_id, session_id),
        ).fetchone()
    return row_to_note(row)


@router.delete("/notes/{note_id}")
def delete_quality_note(session_id: str, note_id: str) -> dict[str, bool]:
    with db_session() as db:
        ensure_session(db, session_id)
        db.execute(
            "DELETE FROM session_quality_notes WHERE id = ? AND session_id = ?",
            (note_id, session_id),
        )
    return {"ok": True}


@router.get("/scenes/{scene_id}/checklist", response_model=SceneQualityChecklistRead)
def get_scene_quality_checklist(
    session_id: str,
    scene_id: str,
    version_id: str | None = Query(default=None),
) -> SceneQualityChecklistRead:
    resolved_version_id = version_id or ""
    with db_session() as db:
        ensure_session(db, session_id)
        ensure_scene_target(db, session_id, scene_id, version_id)
        row = db.execute(
            """
            SELECT *
            FROM scene_quality_checklists
            WHERE session_id = ? AND scene_id = ? AND version_id = ?
            """,
            (session_id, scene_id, resolved_version_id),
        ).fetchone()
    return row_to_checklist(row) if row else empty_checklist(session_id, scene_id, resolved_version_id)


@router.put("/scenes/{scene_id}/checklist", response_model=SceneQualityChecklistRead)
def save_scene_quality_checklist(
    session_id: str,
    scene_id: str,
    payload: SceneQualityChecklistUpdate,
) -> SceneQualityChecklistRead:
    version_id = payload.version_id or ""
    updates = payload.model_dump(exclude_unset=True)
    updates.pop("version_id", None)
    with db_session() as db:
        ensure_session(db, session_id)
        ensure_scene_target(db, session_id, scene_id, version_id or None)
        existing = db.execute(
            """
            SELECT id
            FROM scene_quality_checklists
            WHERE session_id = ? AND scene_id = ? AND version_id = ?
            """,
            (session_id, scene_id, version_id),
        ).fetchone()
        if existing is None:
            checklist_id = str(uuid4())
            db.execute(
                """
                INSERT INTO scene_quality_checklists (id, session_id, scene_id, version_id)
                VALUES (?, ?, ?, ?)
                """,
                (checklist_id, session_id, scene_id, version_id),
            )
        values: dict[str, Any] = {}
        for field, value in updates.items():
            if field not in CHECKLIST_FIELDS:
                continue
            if field == "notes":
                values[field] = value.strip() if isinstance(value, str) else ""
            else:
                values[field] = bool_to_db(value)
        if values:
            assignments = ", ".join(f"{field} = ?" for field in values)
            db.execute(
                f"""
                UPDATE scene_quality_checklists
                SET {assignments}
                WHERE session_id = ? AND scene_id = ? AND version_id = ?
                """,
                (*values.values(), session_id, scene_id, version_id),
            )
        row = db.execute(
            """
            SELECT *
            FROM scene_quality_checklists
            WHERE session_id = ? AND scene_id = ? AND version_id = ?
            """,
            (session_id, scene_id, version_id),
        ).fetchone()
    return row_to_checklist(row)


@router.get("/hints", response_model=QualityHintsResponse)
async def get_quality_hints(session_id: str) -> QualityHintsResponse:
    with db_session() as db:
        ensure_session(db, session_id)
        hints: list[QualityHintRead] = []
        add_title_hints(db, session_id, hints)
        add_generation_hints(db, session_id, hints)
        add_story_state_hints(db, session_id, hints)
        add_summary_hints(db, session_id, hints)
        add_world_and_character_hints(db, session_id, hints)
        add_image_prompt_hints(db, session_id, hints)
        open_issue_row = db.execute(
            """
            SELECT COUNT(*) AS count
            FROM session_quality_notes
            WHERE session_id = ? AND status = 'open'
            """,
            (session_id,),
        ).fetchone()
    await add_tts_hints(hints)
    return QualityHintsResponse(
        session_id=session_id,
        hints=hints[:8],
        open_issue_count=int(open_issue_row["count"] if open_issue_row else 0),
    )
