from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.database import db_session
from app.schemas import SceneImagePromptRead
from app.services.image_prompt_service import (
    generate_image_prompt,
    prompt_quality_warnings,
    resolve_image_prompt_style,
    style_label_from_prompt,
    visual_context_source_hash,
)
from app.services.image_workflows import get_workflow
from app.settings.store import image_generation_is_paused, load_image_settings


LAST_IMAGE_PROMPT_EVENT: dict[str, Any] = {
    "session_id": None,
    "scene_id": None,
    "version_id": None,
    "workflow_id": None,
    "status": None,
    "error": None,
}


def clean_text(value: str | None) -> str:
    return (value or "").strip()


def prompt_safe_workflow_notes(value: str | None) -> str:
    """Keep user-authored visual notes, but strip StoryDriver mapping boilerplate."""
    safe_lines: list[str] = []
    for line in (value or "").splitlines():
        normalized = line.strip().lower()
        if not normalized:
            continue
        if "detected automatically by storydriver" in normalized:
            continue
        if normalized.startswith(
            (
                "positive:",
                "negative:",
                "seed:",
                "output:",
                "positive prompt",
                "negative prompt",
                "seed node",
                "output node",
                "workflow id",
                "selected workflow",
                "preferred storydriver workflow",
            )
        ):
            continue
        if "node" in normalized and any(term in normalized for term in ("prompt", "seed", "output", "input", "mapping")):
            continue
        if any(term in normalized for term in ("class_type", "source_hash", "workflow mapping", "advanced mapping")):
            continue
        safe_lines.append(line.strip())
    return "\n".join(safe_lines).strip()


def json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def json_dict_of_lists(value: str | None) -> dict[str, list[str]]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    cleaned: dict[str, list[str]] = {}
    for key, values in parsed.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(values, list):
            cleaned[name] = [str(item).strip() for item in values if str(item).strip()][:10]
        elif str(values).strip():
            cleaned[name] = [str(values).strip()]
    return cleaned


def row_to_scene_image_prompt(row) -> SceneImagePromptRead:
    characters = json_list(row["characters_included_json"])
    continuity = json_dict_of_lists(row["continuity_used_json"] if "continuity_used_json" in row.keys() else None)
    style_used = style_label_from_prompt(row["style_prompt"] or "")
    return SceneImagePromptRead(
        id=row["id"],
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        version_id=row["version_id"],
        workflow_id=row["workflow_id"] or "",
        workflow_name=row["workflow_name"] or "",
        visual_beat=row["visual_beat"] or "",
        prompt=row["prompt"] or "",
        negative_prompt=row["negative_prompt"] or "",
        characters_included=characters,
        continuity_used=continuity,
        confidence=float(row["confidence"] or 0),
        style_used=style_used,
        quality_warnings=prompt_quality_warnings(
            prompt=row["prompt"] or "",
            visual_beat=row["visual_beat"] or "",
            characters_included=characters,
            continuity_used=continuity,
            style_used=style_used,
        ),
        style_prompt=row["style_prompt"] or "",
        workflow_notes=row["workflow_notes"] or "",
        source_hash=row["source_hash"] or "",
        status=row["status"] or "ready",
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def update_last_prompt_event(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    workflow_id: str | None,
    status: str,
    error: str | None = None,
) -> None:
    LAST_IMAGE_PROMPT_EVENT.update(
        {
            "session_id": session_id,
            "scene_id": scene_id,
            "version_id": version_id,
            "workflow_id": workflow_id,
            "status": status,
            "error": error,
        }
    )


def last_image_prompt_event() -> dict[str, Any]:
    return dict(LAST_IMAGE_PROMPT_EVENT)


def load_session_image_prompt_settings(session_id: str) -> dict[str, Any]:
    with db_session() as db:
        session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        row = db.execute(
            """
            SELECT selected_workflow_id, image_prompt_style, default_negative_prompt,
                   preferred_visual_tone, realism_notes, lighting_camera_notes,
                   auto_open_preview, auto_attach_generated
            FROM session_image_settings
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return {
            "selected_workflow_id": None,
            "image_prompt_style": "",
            "default_negative_prompt": "",
            "preferred_visual_tone": "",
            "realism_notes": "",
            "lighting_camera_notes": "",
            "auto_open_preview": True,
            "auto_attach_generated": True,
        }
    return {
        "selected_workflow_id": row["selected_workflow_id"],
        "image_prompt_style": row["image_prompt_style"] or "",
        "default_negative_prompt": row["default_negative_prompt"] or "",
        "preferred_visual_tone": row["preferred_visual_tone"] or "",
        "realism_notes": row["realism_notes"] or "",
        "lighting_camera_notes": row["lighting_camera_notes"] or "",
        "auto_open_preview": bool(row["auto_open_preview"]),
        "auto_attach_generated": bool(row["auto_attach_generated"]),
    }


def version_key(version_id: str | None) -> str:
    return clean_text(version_id)


def load_scene_image_prompt_settings(
    session_id: str,
    scene_id: str,
    version_id: str | None = None,
) -> dict[str, Any]:
    key = version_key(version_id)
    with db_session() as db:
        scene = db.execute(
            "SELECT id FROM scenes WHERE id = ? AND session_id = ?",
            (scene_id, session_id),
        ).fetchone()
        if scene is None:
            raise HTTPException(status_code=404, detail="Selected scene was not found.")
        row = db.execute(
            """
            SELECT session_id, scene_id, version_id, selected_workflow_id, updated_at
            FROM scene_image_settings
            WHERE session_id = ? AND scene_id = ? AND version_id = ?
            """,
            (session_id, scene_id, key),
        ).fetchone()
    if row is None:
        return {
            "session_id": session_id,
            "scene_id": scene_id,
            "version_id": version_id,
            "selected_workflow_id": None,
            "updated_at": None,
        }
    return {
        "session_id": row["session_id"],
        "scene_id": row["scene_id"],
        "version_id": row["version_id"] or None,
        "selected_workflow_id": row["selected_workflow_id"] or None,
        "updated_at": row["updated_at"],
    }


def save_scene_image_prompt_settings(
    session_id: str,
    scene_id: str,
    version_id: str | None = None,
    selected_workflow_id: str | None = None,
) -> dict[str, Any]:
    key = version_key(version_id)
    with db_session() as db:
        scene = db.execute(
            "SELECT id FROM scenes WHERE id = ? AND session_id = ?",
            (scene_id, session_id),
        ).fetchone()
        if scene is None:
            raise HTTPException(status_code=404, detail="Selected scene was not found.")
        if selected_workflow_id:
            workflow = get_workflow(selected_workflow_id)
            if workflow is None:
                raise HTTPException(status_code=404, detail="Selected workflow file was not found.")
        db.execute(
            """
            INSERT INTO scene_image_settings (id, session_id, scene_id, version_id, selected_workflow_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id, scene_id, version_id) DO UPDATE SET
                selected_workflow_id = excluded.selected_workflow_id
            """,
            (str(uuid4()), session_id, scene_id, key, selected_workflow_id or None),
        )
    return load_scene_image_prompt_settings(session_id, scene_id, version_id)


def load_scene_text(session_id: str, scene_id: str, version_id: str | None) -> tuple[str, str | None]:
    with db_session() as db:
        scene = db.execute(
            "SELECT id, generated_text FROM scenes WHERE id = ? AND session_id = ?",
            (scene_id, session_id),
        ).fetchone()
        if scene is None:
            raise HTTPException(status_code=404, detail="Selected scene was not found.")
        if version_id:
            version = db.execute(
                """
                SELECT id, generated_text
                FROM scene_versions
                WHERE id = ? AND scene_id = ? AND session_id = ?
                """,
                (version_id, scene_id, session_id),
            ).fetchone()
            if version is None:
                raise HTTPException(status_code=404, detail="Selected scene version was not found.")
            return version["generated_text"], version["id"]
        version = db.execute(
            """
            SELECT id, generated_text
            FROM scene_versions
            WHERE scene_id = ? AND session_id = ?
            ORDER BY version_index DESC
            LIMIT 1
            """,
            (scene_id, session_id),
        ).fetchone()
        if version:
            return version["generated_text"], version["id"]
        return scene["generated_text"], None


def resolve_prompt_workflow(
    session_id: str,
    workflow_id: str | None = None,
    scene_id: str | None = None,
    version_id: str | None = None,
):
    session_settings = load_session_image_prompt_settings(session_id)
    global_settings = load_image_settings()
    scene_workflow_id = None
    if scene_id and not workflow_id:
        scene_settings = load_scene_image_prompt_settings(session_id, scene_id, version_id)
        scene_workflow_id = scene_settings.get("selected_workflow_id")
    selected_id = workflow_id or scene_workflow_id or session_settings["selected_workflow_id"] or global_settings.selected_workflow_id
    workflow_info = get_workflow(selected_id) if selected_id else None
    return workflow_info, session_settings


def workflow_notes_with_style(workflow_info, style_prompt: str) -> tuple[str, str]:
    workflow_notes = ""
    if workflow_info and workflow_info.config:
        workflow_notes = prompt_safe_workflow_notes(workflow_info.config.notes)
    combined_notes = "\n".join(part for part in [workflow_notes, clean_text(style_prompt)] if part)
    return workflow_notes, combined_notes


def prompt_source_hash(
    *,
    scene_text: str,
    workflow_id: str,
    workflow_name: str,
    workflow_notes: str,
    style_prompt: str,
    preferred_visual_tone: str,
    realism_notes: str,
    lighting_camera_notes: str,
    negative_prompt: str,
    visual_context_hash: str,
) -> str:
    payload = {
        "scene_text": scene_text,
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "workflow_notes": workflow_notes,
        "style_prompt": style_prompt,
        "preferred_visual_tone": preferred_visual_tone,
        "realism_notes": realism_notes,
        "lighting_camera_notes": lighting_camera_notes,
        "negative_prompt": negative_prompt,
        "visual_context_hash": visual_context_hash,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_cached_scene_image_prompt(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    workflow_id: str | None = None,
    source_hash: str | None = None,
) -> SceneImagePromptRead | None:
    params: list[Any] = [session_id, scene_id]
    clauses = ["session_id = ?", "scene_id = ?", "status = 'ready'"]
    if version_id:
        clauses.append("version_id = ?")
        params.append(version_id)
    else:
        clauses.append("version_id IS NULL")
    if workflow_id is not None:
        clauses.append("workflow_id = ?")
        params.append(workflow_id)
    if source_hash:
        clauses.append("source_hash = ?")
        params.append(source_hash)
    with db_session() as db:
        row = db.execute(
            f"""
            SELECT *
            FROM scene_image_prompts
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return row_to_scene_image_prompt(row) if row else None


def count_scene_image_prompts() -> int:
    with db_session() as db:
        return int(db.execute("SELECT COUNT(*) AS count FROM scene_image_prompts").fetchone()["count"])


def save_scene_image_prompt(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    workflow_id: str,
    workflow_name: str,
    visual_beat: str,
    prompt: str,
    negative_prompt: str,
    characters_included: list[str],
    confidence: float,
    continuity_used: dict[str, list[str]] | None,
    style_prompt: str,
    workflow_notes: str,
    source_hash: str,
    status: str = "ready",
    error: str | None = None,
) -> SceneImagePromptRead:
    existing = load_cached_scene_image_prompt(
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id,
        workflow_id=workflow_id,
    )
    characters_json = json.dumps(characters_included[:8], ensure_ascii=False)
    continuity_json = json.dumps(continuity_used or {}, ensure_ascii=False, sort_keys=True)
    with db_session() as db:
        if existing:
            prompt_id = existing.id
            db.execute(
                """
                UPDATE scene_image_prompts
                SET workflow_name = ?, visual_beat = ?, prompt = ?, negative_prompt = ?,
                    characters_included_json = ?, continuity_used_json = ?, confidence = ?, style_prompt = ?,
                    workflow_notes = ?, source_hash = ?, status = ?, error = ?
                WHERE id = ?
                """,
                (
                    workflow_name,
                    visual_beat,
                    prompt,
                    negative_prompt,
                    characters_json,
                    continuity_json,
                    confidence,
                    style_prompt,
                    workflow_notes,
                    source_hash,
                    status,
                    error,
                    prompt_id,
                ),
            )
        else:
            prompt_id = str(uuid4())
            db.execute(
                """
                INSERT INTO scene_image_prompts (
                    id, session_id, scene_id, version_id, workflow_id, workflow_name,
                    visual_beat, prompt, negative_prompt, characters_included_json,
                    continuity_used_json, confidence, style_prompt, workflow_notes, source_hash, status, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt_id,
                    session_id,
                    scene_id,
                    version_id,
                    workflow_id,
                    workflow_name,
                    visual_beat,
                    prompt,
                    negative_prompt,
                    characters_json,
                    continuity_json,
                    confidence,
                    style_prompt,
                    workflow_notes,
                    source_hash,
                    status,
                    error,
                ),
            )
        row = db.execute("SELECT * FROM scene_image_prompts WHERE id = ?", (prompt_id,)).fetchone()
    return row_to_scene_image_prompt(row)


async def prepare_scene_image_prompt(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    workflow_id: str | None = None,
    negative_prompt: str | None = None,
    force_refresh: bool = False,
) -> SceneImagePromptRead:
    scene_text, resolved_version_id = load_scene_text(session_id, scene_id, version_id)
    if not scene_text.strip():
        raise HTTPException(status_code=400, detail="Selected scene version has no prose to image.")

    workflow_info, session_settings = resolve_prompt_workflow(
        session_id,
        workflow_id,
        scene_id=scene_id,
        version_id=resolved_version_id,
    )
    resolved_workflow_id = workflow_info.id if workflow_info else (workflow_id or session_settings["selected_workflow_id"] or "")
    workflow_name = workflow_info.name if workflow_info else ""
    style_prompt = session_settings["image_prompt_style"]
    preferred_visual_tone = session_settings["preferred_visual_tone"]
    realism_notes = session_settings["realism_notes"]
    lighting_camera_notes = session_settings["lighting_camera_notes"]
    style_info = resolve_image_prompt_style(
        style_prompt,
        preferred_visual_tone,
        realism_notes,
        lighting_camera_notes,
    )
    style_context = clean_text(style_info["text"])
    workflow_notes, combined_notes = workflow_notes_with_style(workflow_info, style_context)
    selected_negative = (
        negative_prompt
        if negative_prompt is not None
        else session_settings["default_negative_prompt"] or None
    )
    source_negative = "\n".join(part for part in [selected_negative or "", style_info["negative_prompt"]] if clean_text(part))
    source_hash = prompt_source_hash(
        scene_text=scene_text,
        workflow_id=resolved_workflow_id,
        workflow_name=workflow_name,
        workflow_notes=workflow_notes,
        style_prompt=style_context,
        preferred_visual_tone=style_info["fields"]["preferred_visual_tone"],
        realism_notes=style_info["fields"]["realism_notes"],
        lighting_camera_notes=style_info["fields"]["lighting_camera_notes"],
        negative_prompt=source_negative,
        visual_context_hash=visual_context_source_hash(session_id=session_id, scene_text=scene_text),
    )

    if not force_refresh:
        cached = load_cached_scene_image_prompt(
            session_id=session_id,
            scene_id=scene_id,
            version_id=resolved_version_id,
            workflow_id=resolved_workflow_id,
            source_hash=source_hash,
        )
        if cached:
            update_last_prompt_event(
                session_id=session_id,
                scene_id=scene_id,
                version_id=resolved_version_id,
                workflow_id=resolved_workflow_id,
                status="cached",
            )
            return cached

    try:
        prompt_data = await generate_image_prompt(
            session_id=session_id,
            scene_text=scene_text,
            workflow_name=workflow_name,
            workflow_notes=combined_notes,
            negative_prompt=selected_negative,
            style_used=style_info["label"],
            style_negative_prompt=style_info["negative_prompt"],
        )
        saved = save_scene_image_prompt(
            session_id=session_id,
            scene_id=scene_id,
            version_id=resolved_version_id,
            workflow_id=resolved_workflow_id,
            workflow_name=workflow_name,
            visual_beat=clean_text(prompt_data.get("visual_beat")),
            prompt=clean_text(prompt_data.get("prompt")),
            negative_prompt=clean_text(prompt_data.get("negative_prompt")) or clean_text(selected_negative),
            characters_included=[
                clean_text(str(item))
                for item in prompt_data.get("characters_included", [])
                if clean_text(str(item))
            ],
            continuity_used=prompt_data.get("continuity_used") if isinstance(prompt_data.get("continuity_used"), dict) else {},
            confidence=float(prompt_data.get("confidence", 0.0) or 0.0),
            style_prompt=style_context,
            workflow_notes=workflow_notes,
            source_hash=source_hash,
        )
        update_last_prompt_event(
            session_id=session_id,
            scene_id=scene_id,
            version_id=resolved_version_id,
            workflow_id=resolved_workflow_id,
            status="ready",
        )
        return saved
    except Exception as error:
        message = str(error)
        update_last_prompt_event(
            session_id=session_id,
            scene_id=scene_id,
            version_id=resolved_version_id,
            workflow_id=resolved_workflow_id,
            status="failed",
            error=message,
        )
        raise


def schedule_scene_image_prompt(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    delay_seconds: float = 0.0,
) -> None:
    if image_generation_is_paused(load_image_settings()):
        update_last_prompt_event(
            session_id=session_id,
            scene_id=scene_id,
            version_id=version_id,
            workflow_id=None,
            status="paused",
            error=None,
        )
        return

    async def runner() -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            await prepare_scene_image_prompt(
                session_id=session_id,
                scene_id=scene_id,
                version_id=version_id,
                force_refresh=True,
            )
        except Exception as error:
            update_last_prompt_event(
                session_id=session_id,
                scene_id=scene_id,
                version_id=version_id,
                workflow_id=None,
                status="failed",
                error=str(error),
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(runner())

    def consume_exception(completed: asyncio.Task) -> None:
        try:
            completed.exception()
        except asyncio.CancelledError:
            return

    task.add_done_callback(consume_exception)
