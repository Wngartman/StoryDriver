from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.database import db_session
from app.generation.model_provider import LMStudioClient, LMStudioError, model_client_for_settings
from app.generation.router import resolve_task_model_settings, task_parameters
from app.memory.foundation import render_story_foundation_for_prompt


MIN_SCENES_FOR_SUMMARY = 9
RECENT_SCENES_TO_KEEP = 6
SUMMARY_MIN_NEW_SCENES = 3
LAST_SUMMARY_EVENT: dict[str, Any] = {
    "session_id": None,
    "status": None,
    "from_scene_id": None,
    "to_scene_id": None,
    "error": None,
    "task_type": None,
    "model": None,
    "timeout_seconds": None,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clean_text(value: Any, max_length: int = 4000) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())[:max_length]


def last_summary_event() -> dict[str, Any]:
    return dict(LAST_SUMMARY_EVENT)


def summary_status(session_id: str | None = None) -> dict[str, Any]:
    event = last_summary_event()
    event_applies = not session_id or event.get("session_id") == session_id
    scene_count = 0
    latest_scene_id = None
    scenes_since_summary = 0
    needs_summary = False
    with db_session() as db:
        if session_id:
            row = db.execute(
                """
                SELECT session_id, summary_text, from_scene_id, to_scene_id, updated_at
                FROM session_summaries
                WHERE session_id = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            scene_rows = db.execute(
                """
                SELECT id
                FROM scenes
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
            scene_ids = [scene["id"] for scene in scene_rows]
            scene_count = len(scene_ids)
            latest_scene_id = scene_ids[-1] if scene_ids else None
        else:
            row = db.execute(
                """
                SELECT session_id, summary_text, from_scene_id, to_scene_id, updated_at
                FROM session_summaries
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
    stored = (
        {
            "session_id": row["session_id"],
            "from_scene_id": row["from_scene_id"],
            "to_scene_id": row["to_scene_id"],
            "updated_at": row["updated_at"],
            "summary_length": len(row["summary_text"] or ""),
        }
        if row
        else None
    )
    if session_id and scene_count:
        summarized_scene = (stored or {}).get("to_scene_id")
        if summarized_scene and summarized_scene in scene_ids:
            scenes_since_summary = max(0, scene_count - scene_ids.index(summarized_scene) - 1)
        else:
            scenes_since_summary = scene_count
        needs_summary = scene_count >= MIN_SCENES_FOR_SUMMARY and (
            not summarized_scene or scenes_since_summary >= SUMMARY_MIN_NEW_SCENES
        )
    return {
        "session_id": event.get("session_id") if event_applies else session_id,
        "status": event.get("status") if event_applies and event.get("status") else ("completed" if stored else "none"),
        "from_scene_id": event.get("from_scene_id") if event_applies and event.get("from_scene_id") else (stored or {}).get("from_scene_id"),
        "to_scene_id": event.get("to_scene_id") if event_applies and event.get("to_scene_id") else (stored or {}).get("to_scene_id"),
        "error": event.get("error") if event_applies else None,
        "task_type": event.get("task_type") if event_applies else None,
        "model": event.get("model") if event_applies else None,
        "timeout_seconds": event.get("timeout_seconds") if event_applies else None,
        "latest_stored_summary": stored,
        "scene_count": scene_count,
        "latest_scene_id": latest_scene_id,
        "scenes_since_summary": scenes_since_summary,
        "needs_summary": needs_summary,
    }


def update_last_summary_event(
    *,
    session_id: str,
    status: str,
    from_scene_id: str | None = None,
    to_scene_id: str | None = None,
    error: str | None = None,
    task_type: str | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
) -> None:
    payload = {
        "session_id": session_id,
        "status": status,
        "from_scene_id": from_scene_id,
        "to_scene_id": to_scene_id,
        "error": error,
        "task_type": task_type,
        "model": model,
        "timeout_seconds": timeout_seconds,
    }
    LAST_SUMMARY_EVENT.update(payload)


def load_scene_texts(session_id: str) -> list[dict[str, str]]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT
                s.id,
                COALESCE(
                    (
                        SELECT sv.generated_text
                        FROM scene_versions sv
                        WHERE sv.scene_id = s.id AND sv.session_id = s.session_id
                        ORDER BY sv.version_index DESC
                        LIMIT 1
                    ),
                    s.generated_text
                ) AS generated_text,
                s.created_at
            FROM scenes s
            WHERE s.session_id = ?
            ORDER BY s.created_at ASC, s.id ASC
            """,
            (session_id,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def latest_summary(session_id: str) -> dict[str, Any] | None:
    with db_session() as db:
        row = db.execute(
            """
            SELECT id, summary_text, from_scene_id, to_scene_id, created_at, updated_at
            FROM session_summaries
            WHERE session_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    return {key: row[key] for key in row.keys()} if row else None


def should_summarize(session_id: str) -> tuple[bool, list[dict[str, str]], dict[str, Any] | None]:
    scenes = load_scene_texts(session_id)
    if len(scenes) < MIN_SCENES_FOR_SUMMARY:
        update_last_summary_event(session_id=session_id, status="skipped")
        return False, scenes, None
    summary = latest_summary(session_id)
    cutoff = scenes[-RECENT_SCENES_TO_KEEP - 1]
    if summary and summary.get("to_scene_id") == cutoff["id"]:
        update_last_summary_event(session_id=session_id, status="current", to_scene_id=cutoff["id"])
        return False, scenes, summary
    if summary and summary.get("to_scene_id"):
        try:
            prior_index = next(index for index, scene in enumerate(scenes) if scene["id"] == summary["to_scene_id"])
        except StopIteration:
            prior_index = -1
        cutoff_index = scenes.index(cutoff)
        if cutoff_index - prior_index < SUMMARY_MIN_NEW_SCENES:
            update_last_summary_event(session_id=session_id, status="current", to_scene_id=summary["to_scene_id"])
            return False, scenes, summary
    return True, scenes, summary


def build_summary_prompt(
    scenes: list[dict[str, str]],
    prior_summary: str,
    cutoff_scene_id: str,
    foundation_context: str = "",
) -> tuple[str, str, str]:
    cutoff_index = next((index for index, scene in enumerate(scenes) if scene["id"] == cutoff_scene_id), -1)
    older_scenes = scenes[: cutoff_index + 1] if cutoff_index >= 0 else scenes[:-RECENT_SCENES_TO_KEEP]
    from_scene_id = older_scenes[0]["id"] if older_scenes else ""
    scene_lines: list[str] = []
    for index, scene in enumerate(older_scenes[-16:], start=max(1, len(older_scenes) - 15)):
        text = clean_text(scene.get("generated_text"), 1200)
        if text:
            scene_lines.extend([f"[Scene {index}]", text, ""])
    prompt = "\n".join(
        [
            "Create a compact rolling continuity summary for older StoryDriver scenes.",
            "This summary will be used as factual support for future prose prompts. It must be useful over long writing sessions without taking over the next scene.",
            "",
            "Preserve durable facts in a scannable structure:",
            "- Current premise and active unresolved plot threads.",
            "- Character goals, emotional stakes, relationship history, promises, betrayals, debts, secrets, and what each character knows.",
            "- Current or last-known locations, injuries, clothing changes, object ownership, and important scene-blocking facts.",
            "- World/location rules, factions, threats, deadlines, and consequences that should remain true.",
            "",
            "Avoid:",
            "- Vague literary recap.",
            "- New events or speculation.",
            "- Prose style, dialogue, or dramatic narration.",
            "- Repeating recent scene text line by line.",
            "",
            "Prior rolling summary:",
            prior_summary or "None",
            "",
            "Story Foundation / Character Bible durable context:",
            foundation_context or "None",
            "Use this only to preserve base identity/world consistency. Do not duplicate the whole foundation into the summary.",
            "",
            "Older scenes to compress:",
            "\n".join(scene_lines)[:18000],
            "",
            "Return the updated continuity summary under 900 words. Use short labeled sections only when they help clarity.",
        ]
    )
    return prompt, from_scene_id, cutoff_scene_id


async def summarize_with_lm_studio(prompt: str) -> tuple[str, Any]:
    model_settings, resolved_model = resolve_task_model_settings("summary_generation")
    client = model_client_for_settings(model_settings)
    model = model_settings.model.strip()
    if not model:
        models = await client.list_models()
        model = next((item.get("id") for item in models if item.get("id")), "")
    if not model:
        raise LMStudioError("No LM Studio model is selected or loaded for story summarization.")
    system_prompt = "You compress story continuity for a local directed-fiction writing app. Return summary text only."
    if clean_text(resolved_model.notes, 1200):
        system_prompt = f"{system_prompt}\n\nTask notes:\n{clean_text(resolved_model.notes, 1200)}"
    result = await client.generate_scene_routed(
        model=model,
        system_prompt=system_prompt,
        user_prompt=prompt,
        parameters=task_parameters(
            resolved_model,
            {
                "temperature": 0.2,
                "top_p": 0.85,
                "max_tokens": 1200,
            },
            max_tokens_min=700,
            max_tokens_max=1800,
        ),
        timeout=resolved_model.timeout_seconds,
        inference_backend=resolved_model.inference_backend,
        reasoning_mode=resolved_model.reasoning_mode,
        context_length=resolved_model.context_length,
        fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
    )
    raw = result["text"]
    resolved_model.model = model
    return clean_text(raw, 8000), resolved_model


async def maybe_update_session_summary(session_id: str, *, force: bool = False) -> dict[str, Any]:
    scenes = load_scene_texts(session_id)
    summary = latest_summary(session_id)
    if len(scenes) < MIN_SCENES_FOR_SUMMARY and not force:
        update_last_summary_event(session_id=session_id, status="skipped")
        return {"status": "skipped", "reason": "not_enough_scenes"}
    if not force:
        should_run, scenes, summary = should_summarize(session_id)
        if not should_run:
            return {"status": LAST_SUMMARY_EVENT.get("status") or "current"}
    if len(scenes) <= RECENT_SCENES_TO_KEEP:
        update_last_summary_event(session_id=session_id, status="skipped")
        return {"status": "skipped", "reason": "recent_context_only"}

    cutoff = scenes[-RECENT_SCENES_TO_KEEP - 1]
    prompt, from_scene_id, to_scene_id = build_summary_prompt(
        scenes,
        clean_text(summary.get("summary_text") if summary else "", 6000),
        cutoff["id"],
        render_story_foundation_for_prompt(session_id, limit=2600),
    )
    update_last_summary_event(session_id=session_id, status="running", from_scene_id=from_scene_id, to_scene_id=to_scene_id)
    try:
        summary_text, resolved_model = await summarize_with_lm_studio(prompt)
        if not summary_text:
            raise LMStudioError("LM Studio returned an empty story summary.")
        with db_session() as db:
            summary_id = str(uuid4())
            db.execute(
                """
                INSERT INTO session_summaries (id, session_id, summary_text, from_scene_id, to_scene_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (summary_id, session_id, summary_text, from_scene_id, to_scene_id),
            )
        update_last_summary_event(
            session_id=session_id,
            status="completed",
            from_scene_id=from_scene_id,
            to_scene_id=to_scene_id,
            task_type=resolved_model.task_type,
            model=resolved_model.model or None,
            timeout_seconds=resolved_model.timeout_seconds,
        )
        return {"status": "completed", "summary_id": summary_id, "to_scene_id": to_scene_id}
    except Exception as error:
        update_last_summary_event(
            session_id=session_id,
            status="failed",
            from_scene_id=from_scene_id,
            to_scene_id=to_scene_id,
            error=str(error),
        )
        return {"status": "failed", "error": str(error)}


def schedule_session_summary(session_id: str, *, delay_seconds: float = 0.0) -> None:
    async def runner() -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await maybe_update_session_summary(session_id)

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
