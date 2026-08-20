import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from math import ceil
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.config import DATA_DIR
from app.database import db_session
from app.schemas import (
    GenerateSceneRequest,
    ProsePromptPreviewRead,
    ProsePromptPreviewRequest,
    SceneRead,
    SceneVersionRead,
    SessionAutoTitleRequest,
    SessionCreate,
    SessionDeleteJobRead,
    SessionDeleteResponse,
    SessionRead,
    SessionUpdate,
)
from app.generation.model_provider import (
    LMStudioClient,
    LMStudioError,
    LMStudioOfflineError,
    model_client_for_settings,
    native_base_url_from_openai_base,
    native_chat_body,
)
from app.services.generation_locks import GenerationConflictError, story_generation_job
from app.services.generated_file_cleanup import (
    collect_story_generated_file_references,
    delete_generated_file_references,
)
from app.services.story_delete_jobs import (
    SessionDeleteNotFoundError,
    get_delete_job,
    list_delete_jobs,
    resume_delete_job,
    start_session_delete_job,
)
from app.generation.router import resolve_task_model_settings
from app.generation.pipeline import (
    PIPELINE_NAME,
    PIPELINE_SCHEMA_VERSION,
    cleanup_internal_generation_artifacts,
    compact_pipeline_stats,
    deterministic_scene_plan,
    run_quality_review_pass,
    run_scene_planning_pass,
    run_targeted_repair_pass,
    should_run_targeted_repair,
)
from app.generation.prompt_builder import (
    build_scene_prompt,
    build_genre_time_helper,
    load_active_characters,
    load_world_notes,
    recommended_max_tokens_for_length,
    resolve_writing_length,
)
from app.memory.foundation import ensure_story_foundation, get_story_foundation
from app.memory.summaries import schedule_session_summary
from app.settings.store import load_tts_settings
from app.memory.engine import schedule_story_state_extraction
from app.memory.characters import schedule_auto_character_detection
from app.services.auto_title import (
    can_auto_title_row,
    clean_generated_title,
    deterministic_title_fallback,
    generated_title_is_usable,
    is_generic_title,
    title_is_user_set,
)
from app.tts.presynth import schedule_scene_tts_presynthesis
from app.tts.qwen import ensure_qwen_unloaded_before_writing
from app.diagnostics.runtime import diagnostic_logging_enabled
from app.repositories.generation_runs import record_generation_run, update_generation_run


router = APIRouter(prefix="/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)
RECENT_SCENE_LIMIT = 6
RECENT_SCENE_CHAR_BUDGET = 12000
RECENT_SCENE_SINGLE_CHAR_LIMIT = 3500
VERSIONED_MODES = {"regenerate", "rewrite", "revise"}
SESSION_SELECT_COLUMNS = (
    "id, title, created_at, updated_at, archived_at, title_source, auto_title_status, "
    "auto_title_error, last_auto_title_attempt_at, auto_title_job_id, auto_title_scene_id, "
    "auto_title_version_id, deletion_status, deletion_job_id, "
    "deletion_started_at, deletion_finished_at, deletion_error"
)
WRITING_PATH = "deliberate_pipeline"
WRITING_PATH_LABEL = "Deliberate Pipeline"
ADHERENCE_CHECK_MODES = {"off", "warn", "retry_once"}
SESSION_DELETE_COUNT_TABLES = [
    "scenes",
    "scene_versions",
    "story_memories",
    "session_summaries",
    "generation_runs",
    "narration_jobs",
    "pronunciation_aliases",
    "generated_images",
    "session_image_settings",
    "scene_image_settings",
    "scene_image_prompts",
    "session_quality_notes",
    "scene_quality_checklists",
    "story_state_runs",
    "character_live_state",
    "character_state_events",
    "relationship_state",
    "emotional_memories",
    "world_live_state",
    "scene_live_state",
    "object_state",
    "plot_threads",
    "state_snapshots",
    "story_foundations",
    "session_characters",
    "world_notes",
]
ASSISTANT_ENDING_PATTERNS = (
    r"\bwhat would you like\b",
    r"\bwhat happens next\b",
    r"\blet me know\b",
    r"\bi can continue\b",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dict(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def table_exists(db, table_name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def count_story_rows(db, session_id: str) -> dict[str, int]:
    counts = {"sessions": 1}
    for table_name in SESSION_DELETE_COUNT_TABLES:
        if not table_exists(db, table_name):
            counts[table_name] = 0
            continue
        row = db.execute(
            f"SELECT COUNT(*) AS count FROM {table_name} WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        counts[table_name] = int(row["count"] if row else 0)
    counts["generated_audio"] = 0
    return counts


def story_generated_file_refs(db, session_id: str) -> list[str]:
    return collect_story_generated_file_references(db, session_id)


def estimated_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text) / 4))


def _prompt_section_chars(prompt: str, header: str, end_headers: tuple[str, ...]) -> int:
    start = prompt.find(header)
    if start < 0:
        return 0
    content_start = start + len(header)
    end_positions = [prompt.find(marker, content_start) for marker in end_headers]
    end_positions = [position for position in end_positions if position >= 0]
    end = min(end_positions) if end_positions else len(prompt)
    return max(0, end - content_start)


def build_prompt_diagnostics(
    *,
    system_prompt: str,
    user_prompt: str,
    task_notes: str,
    director_note: str,
    session_summary: str | None,
    recent_scenes: list[dict],
) -> dict:
    section_enders = (
        "WORLD NOTES:",
        "ACTIVE CHARACTERS:",
        "SESSION SUMMARY:",
        "RELEVANT STORY MEMORY:",
        "RECENT STORY PASSAGES:",
        "GENRE / TIME-PERIOD DICTION GUIDE:",
        "DIRECTOR NOTE REQUIREMENTS:",
        "STRUCTURED SCENE PLAN:",
        "TARGET PASSAGE FOR REWRITE/REVISION:",
        "DIRECTOR NOTE (strongest immediate instruction):",
        "MODE INSTRUCTION:",
    )
    total_chars = len(system_prompt or "") + len(user_prompt or "")
    estimated_tokens = estimated_token_count(f"{system_prompt}\n{user_prompt}")
    warnings: list[str] = []
    if estimated_tokens >= 8000:
        warnings.append(
            f"Prompt is large ({estimated_tokens:,} estimated tokens). If speed drops, reduce recent scenes or archived state."
        )
    return {
        "system_prompt_chars": len(system_prompt or ""),
        "task_notes_chars": len(task_notes or ""),
        "user_prompt_chars": len(user_prompt or ""),
        "story_state_chars": _prompt_section_chars(user_prompt, "CURRENT STORY STATE", section_enders),
        "active_characters_chars": _prompt_section_chars(user_prompt, "ACTIVE CHARACTERS:", section_enders),
        "world_state_chars": _prompt_section_chars(user_prompt, "WORLD NOTES:", section_enders),
        "summary_chars": len(session_summary or "")
        or _prompt_section_chars(user_prompt, "SESSION SUMMARY:", section_enders),
        "recent_scenes_chars": sum(len((scene.get("generated_text") or "")) for scene in recent_scenes),
        "director_note_chars": len(director_note or ""),
        "total_prompt_chars": total_chars,
        "prompt_estimated_tokens": estimated_tokens,
        "prompt_size_warnings": warnings,
    }


def prose_task_notes_for_prompt(task_notes: str, prompt_mode: str) -> str:
    return task_notes or ""


def normalize_adherence_check_mode(value: str | None) -> str:
    normalized = (value or "warn").strip().lower()
    return normalized if normalized in ADHERENCE_CHECK_MODES else "warn"


def chapter_extension_enabled_for_settings(model_settings) -> bool:
    return bool(getattr(model_settings, "chapter_extension_enabled", True))


def resolve_writing_length_for_settings(model_settings, director_note: str) -> dict:
    return resolve_writing_length(
        director_note=director_note,
        configured_mode=model_settings.writing_length_mode,
        custom_word_min=model_settings.custom_word_min,
        custom_word_max=model_settings.custom_word_max,
    )


def compact_sentence_parts(text: str, limit: int = 5) -> list[str]:
    clean = " ".join((text or "").split())
    if not clean:
        return []
    parts = [part.strip(" -") for part in re.split(r"(?<=[.!?;])\s+|\n+|,\s+(?=\w)", clean) if part.strip(" -")]
    if len(parts) <= 1 and len(clean) > 180:
        parts = [clean[index : index + 180].strip() for index in range(0, min(len(clean), 900), 180)]
    return parts[:limit]


def text_excerpt(text: str | None, limit: int = 260) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rsplit(" ", 1)[0].strip() + "..."


def lmstudio_chat_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    parameters: dict,
    stream: bool,
) -> dict:
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": stream,
    }
    body.update({key: value for key, value in parameters.items() if value is not None and value != ""})
    return body


def write_prose_debug_files(
    *,
    session_id: str,
    mode: str,
    model: str,
    task_profile: str,
    task_label: str,
    system_prompt: str,
    user_prompt: str,
    task_notes: str,
    director_note: str,
    writing_length: dict,
    parameters: dict,
    prompt_diagnostics: dict,
    lm_studio_url: str = "",
    resolved_timeout_seconds: int | None = None,
    streaming: bool | None = None,
    prompt_mode: str = "standard",
    writing_path: str = WRITING_PATH,
    app_planning_enabled: bool = True,
    chapter_extension_enabled: bool = True,
    adherence_check_mode: str = "warn",
    scene_plan: dict[str, Any] | None = None,
    genre_time_helper: dict[str, Any] | None = None,
    planning_time_seconds: float | None = None,
    inference_backend: str = "openai_compatible",
    reasoning_mode: str = "auto",
    context_length: int | None = None,
    fallback_to_openai_compatible: bool = True,
) -> None:
    if not diagnostic_logging_enabled():
        return
    logs_dir = DATA_DIR / "logs"
    prompt_path = logs_dir / "last_prose_prompt_debug.txt"
    settings_path = logs_dir / "last_prose_settings_debug.json"
    payload_path = logs_dir / "last_lmstudio_prose_request_payload.json"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        if inference_backend == "native_rest":
            request_payload = native_chat_body(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                parameters=parameters,
                stream=bool(streaming),
                reasoning_mode=reasoning_mode,
                context_length=context_length,
            )
        else:
            request_payload = lmstudio_chat_payload(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                parameters=parameters,
                stream=bool(streaming),
            )
        prompt_path.write_text(
            "\n".join(
                [
                    "StoryDriver local prose prompt debug",
                    f"Generated: {utc_now()}",
                    "This file is local-only and may contain story text.",
                    "",
                    f"Session: {session_id}",
                    f"Mode: {mode}",
                    f"Task profile: {task_profile} ({task_label})",
                    f"Prose prompt mode: {prompt_mode}",
                    f"Writing path: {WRITING_PATH_LABEL}",
                    f"App planning: {'on' if app_planning_enabled else 'off'}",
                    f"Chapter extension: {'on' if chapter_extension_enabled else 'off'}",
                    f"Adherence check: {adherence_check_mode}",
                    f"Inference backend: {inference_backend}",
                    f"Reasoning mode: {reasoning_mode}",
                    f"Model sent to LM Studio: {model}",
                    "",
                    "=== SYSTEM PROMPT ===",
                    system_prompt or "",
                    "",
                    "=== USER PROMPT ===",
                    user_prompt or "",
                ]
            ),
            encoding="utf-8",
        )
        settings_path.write_text(
            json.dumps(
                {
                    "generated_at": utc_now(),
                    "session_id": session_id,
                    "mode": mode,
                    "model": model,
                    "task_profile": task_profile,
                    "task_label": task_label,
                    "prose_prompt_mode": prompt_mode,
                    "writing_path": writing_path,
                    "writing_process_mode": PIPELINE_NAME,
                    "writing_process_label": WRITING_PATH_LABEL,
                    "app_planning_enabled": app_planning_enabled,
                    "chapter_extension_enabled": chapter_extension_enabled,
                    "adherence_check_mode": adherence_check_mode,
                    "scene_plan": scene_plan,
                    "genre_time_helper": genre_time_helper,
                    "planning_time_seconds": planning_time_seconds,
                    "inference_backend": inference_backend,
                    "native_chat_url": native_base_url_from_openai_base(lm_studio_url) + "/chat"
                    if inference_backend == "native_rest"
                    else None,
                    "reasoning_mode": reasoning_mode,
                    "context_length": context_length,
                    "fallback_to_openai_compatible": fallback_to_openai_compatible,
                    "streaming": streaming,
                    "timeout_seconds": resolved_timeout_seconds,
                    "temperature": parameters.get("temperature"),
                    "top_p": parameters.get("top_p"),
                    "max_tokens": parameters.get("max_tokens"),
                    "presence_penalty": parameters.get("presence_penalty"),
                    "frequency_penalty": parameters.get("frequency_penalty"),
                    "seed": parameters.get("seed"),
                    "stop": parameters.get("stop"),
                    "writing_length": writing_length,
                    "director_note_chars": len(director_note or ""),
                    "task_notes_chars": len(task_notes or ""),
                    "prompt_diagnostics": prompt_diagnostics,
                    "measurement_note": (
                        "StoryDriver tokens/sec is estimated visible prose tokens divided by full LM stream wall time. "
                        "It includes prefill and first-visible-token latency; LM Studio UI may report raw decode speed differently."
                    ),
                    "runtime_note": (
                        "GPU/offload/context runtime settings are not guessed by StoryDriver. If LM Studio REST does not expose "
                        "documented controls, they must be configured in LM Studio before loading the model."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        payload_path.write_text(
            json.dumps(
                {
                    **request_payload,
                    "_storydriver_debug": {
                        "generated_at": utc_now(),
                        "task_profile": task_profile,
                        "task_label": task_label,
                        "writing_mode": mode,
                        "prose_prompt_mode": prompt_mode,
                        "writing_path": writing_path,
                        "writing_process_mode": "deliberate",
                        "writing_process_label": WRITING_PATH_LABEL,
                        "app_planning_enabled": app_planning_enabled,
                        "chapter_extension_enabled": chapter_extension_enabled,
                        "adherence_check_mode": adherence_check_mode,
                        "scene_plan": scene_plan,
                        "genre_time_helper": genre_time_helper,
                        "planning_time_seconds": planning_time_seconds,
                        "inference_backend": inference_backend,
                        "reasoning_mode": reasoning_mode,
                        "context_length": context_length,
                        "fallback_to_openai_compatible": fallback_to_openai_compatible,
                        "writing_length": writing_length,
                        "system_prompt_chars": len(system_prompt or ""),
                        "task_notes_chars": len(task_notes or ""),
                        "user_prompt_chars": len(user_prompt or ""),
                        "prompt_estimated_tokens": prompt_diagnostics.get("prompt_estimated_tokens"),
                        "prompt_diagnostics": prompt_diagnostics,
                        "extra_body": {},
                        "secrets_included": False,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        # Debug files should never break scene generation.
        return


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def build_generation_stats(
    *,
    text: str,
    elapsed_seconds: float,
    model: str,
    task_profile: str,
    task_label: str,
    first_token_latency_seconds: float | None = None,
    writing_length: dict | None = None,
    adherence_warnings: list[str] | None = None,
    requested_max_tokens: int | None = None,
    extra: dict | None = None,
) -> dict:
    token_count = estimated_token_count(text)
    words = word_count(text)
    tokens_per_second = token_count / elapsed_seconds if elapsed_seconds > 0 else None
    stats = {
        "tokens_generated": token_count,
        "tokens_estimated": True,
        "word_count": words,
        "output_chars": len(text or ""),
        "estimated_reading_time_minutes": round(words / 190, 1) if words else 0,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "tokens_per_second": round(tokens_per_second, 2) if tokens_per_second is not None else None,
        "visible_prose_tokens_per_second": round(tokens_per_second, 2) if tokens_per_second is not None else None,
        "speed_measurement_note": (
            "Visible prose speed is estimated from generated content text over full LM generation wall time. "
            "Reasoning/raw stream speed is reported separately when LM Studio emits reasoning_content."
        ),
        "model": model,
        "task_profile": task_profile,
        "task_label": task_label,
        "writing_length": writing_length or {},
        "requested_max_tokens": requested_max_tokens,
        "adherence_warnings": adherence_warnings or [],
        "first_token_latency_seconds": (
            round(first_token_latency_seconds, 3) if first_token_latency_seconds is not None else None
        ),
    }
    if extra:
        stats.update(extra)
    return stats


def rounded_seconds(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, float(value)), 3)


def generation_stage_timing_breakdown(
    *,
    prepare_generation_seconds: float | None = None,
    model_resolve_seconds: float | None = None,
    story_foundation_seconds: float | None = None,
    planning_time_seconds: float | None = None,
    prompt_builder_seconds: float | None = None,
    pre_write_cleanup_seconds: float | None = None,
    request_to_lm_start_seconds: float | None = None,
    request_to_first_visible_prose_seconds: float | None = None,
    lm_request_to_first_visible_prose_seconds: float | None = None,
    prose_model_seconds: float | None = None,
    review_seconds: float | None = None,
    repair_seconds: float | None = None,
    scene_save_seconds: float | None = None,
    request_to_scene_saved_seconds: float | None = None,
) -> dict[str, float | None]:
    review_value = float(review_seconds or 0.0)
    repair_value = float(repair_seconds or 0.0)
    return {
        "prepare_generation_seconds": rounded_seconds(prepare_generation_seconds),
        "model_resolve_seconds": rounded_seconds(model_resolve_seconds),
        "story_foundation_seconds": rounded_seconds(story_foundation_seconds),
        "scene_planning_seconds": rounded_seconds(planning_time_seconds),
        "prompt_builder_seconds": rounded_seconds(prompt_builder_seconds),
        "pre_write_cleanup_seconds": rounded_seconds(pre_write_cleanup_seconds),
        "request_to_prose_request_seconds": rounded_seconds(request_to_lm_start_seconds),
        "request_to_first_visible_prose_seconds": rounded_seconds(request_to_first_visible_prose_seconds),
        "lm_request_to_first_visible_prose_seconds": rounded_seconds(lm_request_to_first_visible_prose_seconds),
        "prose_model_seconds": rounded_seconds(prose_model_seconds),
        "review_seconds": rounded_seconds(review_seconds),
        "repair_seconds": rounded_seconds(repair_seconds),
        "post_prose_quality_seconds": rounded_seconds(review_value + repair_value),
        "scene_save_seconds": rounded_seconds(scene_save_seconds),
        "request_to_scene_saved_seconds": rounded_seconds(request_to_scene_saved_seconds),
    }


def row_to_session(row) -> SessionRead:
    keys = row.keys()
    return SessionRead(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=row["archived_at"] if "archived_at" in keys else None,
        title_source=row["title_source"] if "title_source" in keys else "placeholder",
        auto_title_status=row["auto_title_status"] if "auto_title_status" in keys else "skipped",
        auto_title_error=row["auto_title_error"] if "auto_title_error" in keys else None,
        last_auto_title_attempt_at=(
            row["last_auto_title_attempt_at"] if "last_auto_title_attempt_at" in keys else None
        ),
        auto_title_job_id=row["auto_title_job_id"] if "auto_title_job_id" in keys else None,
        auto_title_scene_id=row["auto_title_scene_id"] if "auto_title_scene_id" in keys else None,
        auto_title_version_id=row["auto_title_version_id"] if "auto_title_version_id" in keys else None,
        deletion_status=row["deletion_status"] if "deletion_status" in keys else "",
        deletion_job_id=row["deletion_job_id"] if "deletion_job_id" in keys else None,
        deletion_started_at=row["deletion_started_at"] if "deletion_started_at" in keys else None,
        deletion_finished_at=row["deletion_finished_at"] if "deletion_finished_at" in keys else None,
        deletion_error=row["deletion_error"] if "deletion_error" in keys else None,
    )


def row_to_version(row) -> SceneVersionRead:
    return SceneVersionRead(
        id=row["id"],
        scene_id=row["scene_id"],
        session_id=row["session_id"],
        director_note=row["director_note"],
        generated_text=row["generated_text"],
        mode=row["mode"],
        version_index=row["version_index"],
        created_at=row["created_at"],
        generation_stats=json_dict(row["generation_stats_json"] if "generation_stats_json" in row.keys() else None),
    )


def scene_read_from_row(row, versions: list[SceneVersionRead]) -> SceneRead:
    active = versions[-1] if versions else None
    return SceneRead(
        id=row["id"],
        session_id=row["session_id"],
        director_note=active.director_note if active else row["director_note"],
        generated_text=active.generated_text if active else row["generated_text"],
        mode=active.mode if active else row["mode"],
        created_at=row["created_at"],
        updated_at=row["updated_at"] if "updated_at" in row.keys() else None,
        active_version_id=active.id if active else None,
        active_version_index=active.version_index if active else 1,
        version_count=len(versions) or 1,
        versions=versions,
        generation_stats=active.generation_stats if active else json_dict(row["generation_stats_json"] if "generation_stats_json" in row.keys() else None),
    )


def fallback_version_from_row(row) -> SceneVersionRead:
    return SceneVersionRead(
        id=f"{row['id']}-v1",
        scene_id=row["id"],
        session_id=row["session_id"],
        director_note=row["director_note"],
        generated_text=row["generated_text"],
        mode=row["mode"] if "mode" in row.keys() else "continue",
        version_index=1,
        created_at=row["created_at"],
        generation_stats=json_dict(row["generation_stats_json"] if "generation_stats_json" in row.keys() else None),
    )


def normalize_versions(scene_id: str, versions: list[SceneVersionRead]) -> list[SceneVersionRead]:
    sorted_versions = sorted(
        versions,
        key=lambda version: (version.created_at, version.version_index, version.id),
    )
    return [
        version.model_copy(update={"scene_id": scene_id, "version_index": index})
        for index, version in enumerate(sorted_versions, start=1)
    ]


def is_legacy_version_branch(row, versions: list[SceneVersionRead], has_root_scene: bool) -> bool:
    if not has_root_scene:
        return False
    row_mode = row["mode"] if "mode" in row.keys() else "continue"
    if row_mode not in VERSIONED_MODES:
        return False
    return not any(version.version_index == 1 and version.mode == "continue" for version in versions)


def load_grouped_scene_bundle(db, session_id: str) -> tuple[list[SceneRead], dict[str, str]]:
    rows = db.execute(
        """
        SELECT id, session_id, director_note, generated_text, generation_stats_json, mode, created_at, updated_at
        FROM scenes
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    ).fetchall()

    version_rows = db.execute(
        """
        SELECT id, scene_id, session_id, director_note, generated_text, generation_stats_json, mode, version_index, created_at
        FROM scene_versions
        WHERE session_id = ?
        ORDER BY scene_id ASC, version_index ASC
        """,
        (session_id,),
    ).fetchall()
    versions_by_scene: dict[str, list[SceneVersionRead]] = {}
    for version_row in version_rows:
        versions_by_scene.setdefault(version_row["scene_id"], []).append(row_to_version(version_row))

    groups: list[dict] = []
    branch_to_root: dict[str, str] = {}
    for row in rows:
        versions = versions_by_scene.get(row["id"]) or [fallback_version_from_row(row)]
        if is_legacy_version_branch(row, versions, bool(groups)):
            root_id = groups[-1]["row"]["id"]
            branch_to_root[row["id"]] = root_id
            groups[-1]["versions"].extend(versions)
            continue
        groups.append({"row": row, "versions": versions})

    scenes = [
        scene_read_from_row(group["row"], normalize_versions(group["row"]["id"], group["versions"]))
        for group in groups
    ]
    return scenes, branch_to_root


def load_grouped_scenes(db, session_id: str) -> list[SceneRead]:
    scenes, _ = load_grouped_scene_bundle(db, session_id)
    return scenes


def scene_read_to_prompt_dict(scene: SceneRead) -> dict:
    return {
        "id": scene.id,
        "session_id": scene.session_id,
        "director_note": scene.director_note,
        "generated_text": scene.generated_text,
        "mode": scene.mode,
        "created_at": scene.created_at,
        "updated_at": scene.updated_at,
    }


def trim_scene_text_for_prompt(text: str, limit: int) -> str:
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    tail = clean[-max(500, limit - 120) :].lstrip()
    return f"[Earlier passage text trimmed for speed; summary and Story State preserve continuity.]\n{tail}"


def trim_recent_scenes_for_prompt(scenes: list[dict]) -> list[dict]:
    if not scenes:
        return []
    remaining = RECENT_SCENE_CHAR_BUDGET
    kept_reversed: list[dict] = []
    for scene in reversed(scenes[-RECENT_SCENE_LIMIT:]):
        text = scene.get("generated_text") or ""
        if remaining <= 800 and kept_reversed:
            break
        allowance = min(RECENT_SCENE_SINGLE_CHAR_LIMIT, max(800, remaining))
        trimmed_text = trim_scene_text_for_prompt(text, allowance)
        kept_reversed.append({**scene, "generated_text": trimmed_text})
        remaining -= len(trimmed_text)
    return list(reversed(kept_reversed))


def generation_parameters(model_settings, writing_length: dict | None = None) -> dict:
    recommended_tokens = recommended_max_tokens_for_length(writing_length) if writing_length else 0
    params = {
        "temperature": model_settings.temperature,
        "top_p": model_settings.top_p,
        "top_k": model_settings.top_k,
        "min_p": model_settings.min_p,
        "repeat_penalty": model_settings.repeat_penalty,
        "max_tokens": model_settings.max_tokens,
        "presence_penalty": model_settings.presence_penalty,
        "frequency_penalty": model_settings.frequency_penalty,
        "seed": model_settings.seed,
    }
    stop_strings = [
        item.strip()
        for item in model_settings.stop_strings.replace("\n", ",").split(",")
        if item.strip()
    ]
    if stop_strings:
        params["stop"] = stop_strings
    if writing_length:
        params["max_tokens"] = max(
            int(params.get("max_tokens") or 0),
            recommended_tokens,
        )
    if thinking_enabled_for_settings(model_settings):
        params["max_tokens"] = min(
            64000,
            max(int(params.get("max_tokens") or 0), recommended_tokens + reasoning_reserve_tokens(writing_length)),
        )
    return params


def thinking_enabled_for_settings(model_settings) -> bool:
    mode = str(getattr(model_settings, "reasoning_mode", "auto") or "auto").strip().lower()
    if mode == "off":
        return False
    if mode in {"low", "medium", "high", "on"}:
        return True
    system_prompt = str(getattr(model_settings, "system_prompt", "") or "").lower()
    return "/think" in system_prompt or "<think" in system_prompt


def reasoning_reserve_tokens(writing_length: dict | None) -> int:
    if not writing_length:
        return 1200
    mode = str(writing_length.get("mode") or "").lower()
    if mode == "beat":
        return 900
    if mode == "scene":
        return 1800
    if mode == "chapter":
        return 4200
    max_words = int(writing_length.get("max_words") or writing_length.get("min_words") or 1200)
    return max(1200, min(8000, int(max_words * 0.8)))


def thinking_budget_metadata(model_settings, writing_length: dict | None, parameters: dict) -> dict[str, Any]:
    recommended_tokens = recommended_max_tokens_for_length(writing_length) if writing_length else 0
    reserve = reasoning_reserve_tokens(writing_length) if thinking_enabled_for_settings(model_settings) else 0
    return {
        "thinking_enabled": thinking_enabled_for_settings(model_settings),
        "requested_length_mode": (writing_length or {}).get("mode"),
        "recommended_prose_tokens": recommended_tokens,
        "reasoning_reserve_tokens": reserve,
        "total_max_tokens_sent": parameters.get("max_tokens"),
        "capped_at_storydriver_limit": int(parameters.get("max_tokens") or 0) >= 64000,
    }


def task_type_for_generation_mode(mode: str) -> str:
    return "rewrite_revision" if mode in VERSIONED_MODES else "prose_generation"


DIRECTOR_NAME_EXCLUSIONS = {
    "Add",
    "Adult",
    "After",
    "Again",
    "Also",
    "Before",
    "Being",
    "Create",
    "Continue",
    "Detail",
    "Details",
    "Did",
    "Each",
    "End",
    "Events",
    "First",
    "Focus",
    "Introduce",
    "Make",
    "Keep",
    "Let",
    "Preserve",
    "Move",
    "Opening",
    "Regenerate",
    "Revise",
    "Rewrite",
    "Relationship",
    "Same",
    "Show",
    "Start",
    "Stay",
    "Stronger",
    "This",
    "Write",
    "Story",
    "Chapter",
    "Scene",
    "Science",
    "No",
    "The",
    "They",
    "Their",
    "Parents",
    "Streets",
    "Farm",
    "Town",
    "Bandit",
    "Camp",
    "Girl",
    "Grounded",
    "Magic",
    "Tension",
    "Tactical",
    "Use",
}


def director_named_characters(director_note: str) -> list[str]:
    title_tokens: list[tuple[str, int, int]] = [
        (re.sub(r"^(?:Adult|Young|Older|Younger|Main|Major|Primary)\s+", "", match.group(0)).strip(), match.start(), match.end())
        for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", director_note or "")
    ]
    title_tokens = [token for token in title_tokens if token[0]]
    surname_tokens: set[str] = set()
    for index, (first, _first_start, first_end) in enumerate(title_tokens[:-1]):
        second, second_start, _second_end = title_tokens[index + 1]
        separator = (director_note or "")[first_end:second_start]
        if not separator.isspace():
            continue
        if first in DIRECTOR_NAME_EXCLUSIONS or second in DIRECTOR_NAME_EXCLUSIONS:
            continue
        surname_tokens.add(second.lower())
    names: list[str] = []
    for first, _start, _end in title_tokens:
        if not first:
            continue
        if first in DIRECTOR_NAME_EXCLUSIONS:
            continue
        if first.lower() in surname_tokens:
            continue
        if first not in names:
            names.append(first)
    return names[:8]


def likely_character_name_count(text: str) -> int:
    exclusions = DIRECTOR_NAME_EXCLUSIONS | {
        "Before",
        "Being",
        "Bring",
        "Did",
        "When",
        "Where",
        "What",
        "There",
        "Then",
        "Only",
        "Every",
        "Little",
        "Old",
    }
    counts: dict[str, int] = {}
    for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", text or ""):
        normalized = re.sub(r"^(?:Adult|Young|Older|Younger|Main|Major|Primary)\s+", "", match.group(0)).strip()
        first = normalized.split()[0] if normalized else ""
        if first in exclusions:
            continue
        counts[first] = counts.get(first, 0) + 1
    return len([name for name, count in counts.items() if count >= 2])


def magic_terms_without_negation(text: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"\b(magic|spell|spells|enchanted|enchantment|sorcery|wizard|witch)\b", text or "", re.I):
        before = (text or "")[max(0, match.start() - 90) : match.start()].lower()
        phrase = (text or "")[max(0, match.start() - 30) : match.end() + 50].lower()
        if re.search(r"\b(no|not|without|never|lacked|lacks|lack|possessed no|had no|has no)\b", before):
            continue
        if "no magic" in phrase or "without magic" in phrase:
            continue
        terms.append(match.group(0).lower())
    return sorted(set(terms))


def note_explicitly_allows_time_skip(note: str) -> bool:
    normalized = (note or "").lower()
    if re.search(r"\b(no|do not|don't|without)\s+(?:time[-\s]?skip|skip|jump ahead|fast[-\s]?forward)\b", normalized):
        return False
    return bool(
        re.search(
            r"\b(time[-\s]?skip|skip ahead|jump ahead|fast[-\s]?forward|montage|days?\s+later|weeks?\s+later|months?\s+later|years?\s+later)\b",
            normalized,
        )
    )


def note_requests_setup_or_intro(note: str) -> bool:
    return bool(
        re.search(
            r"\b(first\s+chapter|introduc(?:e|tion|tions)|prepare|preparation|planning|plan(?:s|ning)?|briefing|discussion|discuss|talk(?:ing)?|conversation)\b",
            note or "",
            re.I,
        )
    )


def note_requests_shorter_version(note: str) -> bool:
    return bool(
        re.search(
            r"\b(shorter|tighten|trim|condense|more concise|brief(?:er)?|cut down|less verbose)\b",
            note or "",
            re.I,
        )
    )


def later_action_evidence_for_setup_warning(text: str) -> str | None:
    normalized = " ".join((text or "").lower().split())
    patterns = [
        r"\bafter\s+(?:the\s+)?(?:battle|rescue|attack|mission)\b",
        r"\b(?:reached|arrived at)\s+the\s+battlefield\b",
        r"\b(?:the|their|his|her)\s+(?:attack|rescue|mission|battle)\s+(?:was|had been)\s+(?:over|done|finished|won|lost)\b",
        r"\b(?:they|he|she|we)\s+(?:(?:charged|attacked|stormed|rescued|escaped|defeated)\b|won\b(?!['’]t))",
    ]
    hypothetical_guard = re.compile(
        r"\b(?:would|could|might|should|may|planned|planning|plan|intended|intend|wanted|want|ready|before|if|when|whether|to)\b$"
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        before = normalized[max(0, match.start() - 55) : match.start()].strip()
        if hypothetical_guard.search(before):
            continue
        return normalized[max(0, match.start() - 40) : match.end() + 60]
    return None


def repeated_paragraph_signature_count(text: str) -> int:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n+", text or "") if len(paragraph.strip()) >= 120]
    signatures: dict[str, int] = {}
    for paragraph in paragraphs:
        words = re.findall(r"\b[a-z]{4,}\b", paragraph.lower())[:45]
        if len(words) < 18:
            continue
        signature = " ".join(words[:25])
        signatures[signature] = signatures.get(signature, 0) + 1
    return max(signatures.values(), default=0)


def generation_adherence_warning_details(
    director_note: str,
    generated_text: str,
    writing_length: dict,
    *,
    mode: str | None = None,
    target_scene: dict | None = None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []

    def add_warning(code: str, severity: str, message: str, evidence: str | None = None) -> None:
        detail = {"code": code, "severity": severity, "message": message}
        if evidence:
            detail["evidence"] = evidence[:360]
        warnings.append(detail)

    words = re.findall(r"\b[\w'-]+\b", generated_text or "")
    min_words = int(writing_length.get("min_words") or 0)
    if not generated_text.strip():
        add_warning("empty_passage", "major", "Generated passage was empty.")
        return warnings
    target_words = word_count((target_scene or {}).get("generated_text", "")) if target_scene else 0
    if mode in VERSIONED_MODES and target_words >= 400 and not note_requests_shorter_version(director_note):
        version_floor = int(target_words * 0.6)
        if len(words) < version_floor:
            add_warning(
                "version_length_drift",
                "minor",
                f"Versioned passage is much shorter than the target scene: {len(words)} words vs prior {target_words}.",
            )
    else:
        short_threshold = 0.9 if writing_length.get("mode") == "chapter" else 0.75
        if min_words and len(words) < int(min_words * short_threshold):
            add_warning(
                "short_passage",
                "minor",
                f"Generated passage is short for {writing_length.get('label', 'selected length')}: {len(words)} words vs target {writing_length.get('min_words')}-{writing_length.get('max_words')}.",
            )
    note = director_note.lower()
    text = generated_text.lower()
    magic_terms = magic_terms_without_negation(generated_text)
    if ("no magic" in note or "without magic" in note) and magic_terms:
        add_warning(
            "possible_magic_violation",
            "major",
            f"Director note requested no magic, but the passage may contain magic language: {', '.join(magic_terms)}.",
        )
    for name in director_named_characters(director_note):
        if not re.search(rf"\b{re.escape(name)}\b", generated_text):
            add_warning(
                "named_character_missing",
                "major",
                f"Director note named {name}, but the passage may not include them.",
            )
    if re.search(r"\b(3|three)\s+(women|sisters)\b", note) and likely_character_name_count(generated_text) < 3:
        add_warning(
            "requested_group_underrepresented",
            "major",
            "Director note asked for three major women/sisters, but fewer than three recurring character names were detected.",
        )
    if "bandit camp" in note and "bandit" not in text:
        add_warning(
            "requested_bandits_missing",
            "minor",
            "Director note mentioned a bandit camp, but bandits may be missing from the passage.",
        )
    if "little girl" in note and not re.search(r"\b(girl|child|daughter)\b", text):
        add_warning(
            "requested_child_reference_missing",
            "minor",
            "Director note mentioned a captured little girl, but she may be missing from the passage.",
        )
    if not note_explicitly_allows_time_skip(director_note) and re.search(
        r"\b(hours?|days?|weeks?|months?|years?)\s+later\b|\bby\s+(?:dawn|morning|noon|evening|nightfall)\b|\bafter\s+(?:the\s+)?(?:battle|rescue|attack|mission)\b",
        text,
    ):
        add_warning("possible_time_skip", "major", "Passage may contain an unauthorized time skip or aftermath jump.")
    setup_evidence = later_action_evidence_for_setup_warning(generated_text) if note_requests_setup_or_intro(director_note) else None
    if setup_evidence:
        add_warning(
            "setup_scope_jump",
            "major",
            "Director note appears to request setup/introduction/planning, but the passage may jump into or past later action.",
            setup_evidence,
        )
    if repeated_paragraph_signature_count(generated_text) >= 2:
        add_warning("possible_fixation", "minor", "Passage may repeat the same paragraph-level idea instead of moving the scene.")
    if any(re.search(pattern, text[-900:]) for pattern in ASSISTANT_ENDING_PATTERNS):
        add_warning("assistant_style_ending", "major", "Passage may contain assistant-style ending language.")
    if any(term in text[-1200:] for term in ("i can't", "i cannot", "as an ai", "i'm unable", "i am unable")):
        add_warning("assistant_refusal_framing", "major", "Passage may contain refusal or assistant-style framing.")
    return warnings


def generation_adherence_warnings(
    director_note: str,
    generated_text: str,
    writing_length: dict,
    *,
    mode: str | None = None,
    target_scene: dict | None = None,
) -> list[str]:
    return [
        detail["message"]
        for detail in generation_adherence_warning_details(
            director_note,
            generated_text,
            writing_length,
            mode=mode,
            target_scene=target_scene,
        )
    ]


def chapter_needs_extension(generated_text: str, writing_length: dict) -> bool:
    if writing_length.get("mode") != "chapter":
        return False
    return word_count(generated_text) < int(writing_length.get("min_words") or 1800)


def build_chapter_extension_prompt(
    *,
    director_note: str,
    current_text: str,
    writing_length: dict,
) -> str:
    current_words = word_count(current_text)
    min_words = int(writing_length.get("min_words") or 1800)
    remaining = max(350, min_words - current_words + 250)
    tail = current_text[-5000:]
    return "\n".join(
        [
            "StoryDriver chapter extension request",
            "",
            "The drafted chapter stopped before the requested length. Continue the same chapter in narrated fiction prose only.",
            f"Current length: {current_words} words. Target minimum: {min_words} words.",
            f"Write roughly {remaining} more words, continuing naturally from the final paragraph below.",
            "Do not restart, recap, apologize, explain, add headings, or ask what happens next.",
            "Do not contradict the director note. Keep the same grounded continuity and constraints.",
            "Do not use the extra length as permission to skip hours/days, jump to aftermath, or begin later action that the director only framed as future setup.",
            "",
            "DIRECTOR NOTE:",
            director_note.strip(),
            "",
            "CURRENT CHAPTER ENDING:",
            tail,
            "",
            "CONTINUE FROM HERE:",
        ]
    )


def log_generation_warnings(session_id: str, warnings: list[str]) -> None:
    if not warnings or not diagnostic_logging_enabled():
        return
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "writing_generation_warnings.log").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"session_id": session_id, "warnings": warnings}, ensure_ascii=False) + "\n")


def queue_scene_background_work(scene: SceneRead) -> None:
    version_id = scene.active_version_id or (scene.versions[-1].id if scene.versions else None)
    generated_text = scene.generated_text or (scene.versions[-1].generated_text if scene.versions else "")
    tts_settings = load_tts_settings()
    fast_reading = bool(tts_settings.fast_reading_mode)
    tts_delay = 0.2 if fast_reading else 1.0
    state_delay = 18.0 if fast_reading else 0.0
    summary_delay = 30.0 if fast_reading else 0.0
    schedule_scene_tts_presynthesis(
        session_id=scene.session_id,
        scene_id=scene.id,
        version_id=version_id,
        text=generated_text,
        delay_seconds=tts_delay,
    )
    schedule_auto_character_detection(
        session_id=scene.session_id,
        scene_id=scene.id,
        version_id=version_id,
    )
    schedule_story_state_extraction(
        session_id=scene.session_id,
        scene_id=scene.id,
        version_id=version_id,
        delay_seconds=state_delay,
    )
    schedule_session_summary(scene.session_id, delay_seconds=summary_delay)


async def resolve_model(client: LMStudioClient, selected_model: str) -> str:
    if selected_model.strip():
        return selected_model.strip()

    models = await client.list_models()
    first_model = next((model.get("id") for model in models if model.get("id")), None)
    if not first_model:
        raise HTTPException(
            status_code=400,
            detail="No model is selected and LM Studio did not return any loaded models. Select or load a model in LM Studio.",
        )
    return first_model


async def generate_short_title(session_id: str, scene_text: str, director_note: str = "") -> str:
    model_settings, resolved_model = resolve_task_model_settings("title_generation")
    client = model_client_for_settings(model_settings)
    model = await resolve_model(client, model_settings.model)
    world_notes = load_world_notes(session_id) or {}
    active_characters = load_active_characters(session_id)
    with db_session() as db:
        recent_titles = [
            row["title"]
            for row in db.execute(
                """
                SELECT title FROM sessions
                WHERE id != ? AND title_source IN ('auto', 'generated') AND archived_at IS NULL
                ORDER BY updated_at DESC LIMIT 12
                """,
                (session_id,),
            ).fetchall()
            if not is_generic_title(row["title"])
        ]
    foundation = get_story_foundation(session_id) or {}
    context_lines: list[str] = []
    if active_characters:
        names = [character.get("name", "").strip() for character in active_characters if character.get("name", "").strip()]
        if names:
            context_lines.append(f"Active characters: {', '.join(names[:8])}")
    if world_notes:
        compact_world = "; ".join(
            str(value).strip()
            for value in world_notes.values()
            if isinstance(value, str) and value.strip()
        )
        if compact_world:
            context_lines.append(f"World notes: {compact_world[:800]}")
    foundation_data = foundation.get("foundation") if isinstance(foundation, dict) else {}
    overview = foundation_data.get("overview") if isinstance(foundation_data, dict) else {}
    if isinstance(overview, dict):
        premise = str(overview.get("premise") or "").strip()
        genre = str(overview.get("genre") or "").strip()
        if premise:
            context_lines.append(f"Story premise: {premise[:700]}")
        if genre:
            context_lines.append(f"Genre: {genre[:120]}")
    if recent_titles:
        context_lines.append("Recent StoryDriver titles to differ from: " + " | ".join(recent_titles[:10]))

    scene_excerpt = scene_text[:3000]
    user_prompt_parts = [
        "Generate exactly one short title for this fiction story/session.",
        "Use the opening director note and the completed first scene. Infer genre, tone, conflict, place, and mood from the content itself.",
        "The title should usually be 2-6 words, specific to what is actually happening, and suitable for the story's own genre.",
        "Avoid placeholder/opening labels, vague filler, subtitles, explanations, and spoiler-heavy wording unless the opening scene makes the reveal central.",
        "Do not repeat the first word, lexical pattern, or X of Y structure used by the recent titles. Avoid Shadows, Echoes, Whispers, Chronicles, Legacy, Secrets, and Veil unless the literal subject is central and the word is not recent.",
        "Return plain text only.",
        "",
        *context_lines,
        "",
    ]
    if director_note.strip():
        user_prompt_parts.extend(["Opening director note:", director_note.strip()[:1000], ""])
    user_prompt_parts.extend(["First saved scene:", scene_excerpt])
    user_prompt = "\n".join(user_prompt_parts)
    system_prompt = "You generate concise fiction story titles only."
    if resolved_model.notes.strip():
        system_prompt = f"{system_prompt}\n\nTask notes:\n{resolved_model.notes.strip()[:1200]}"
    title = ""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            attempt_prompt = user_prompt
            if attempt:
                attempt_prompt = "\n".join(
                    [
                        user_prompt,
                        "",
                        "Retry requirement: the previous candidate was unusable or too generic. Return one more specific title from the actual premise and first scene. Plain title only.",
                    ]
                )
            result = await client.generate_scene_routed(
                model=model,
                system_prompt=system_prompt,
                user_prompt=attempt_prompt,
                parameters={
                    "temperature": 0.35 if attempt else 0.45,
                    "top_p": 0.85 if attempt else 0.9,
                    "max_tokens": 48 if attempt else 32,
                },
                timeout=resolved_model.timeout_seconds,
                inference_backend=resolved_model.inference_backend,
                reasoning_mode=resolved_model.reasoning_mode,
                context_length=resolved_model.context_length,
                fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
            )
            title = clean_generated_title(result["text"])
            if generated_title_is_usable(title, recent_titles):
                break
            title = ""
        except LMStudioError as error:
            last_error = error
            if "empty scene" in str(error).lower() and not attempt:
                continue
            break
    if generated_title_is_usable(title, recent_titles):
        return title
    character_names = [
        character.get("name", "").strip()
        for character in active_characters
        if character.get("name", "").strip()
    ]
    fallback = deterministic_title_fallback(
        director_note,
        scene_text,
        character_names=character_names,
        recent_titles=recent_titles,
    )
    if generated_title_is_usable(fallback, recent_titles):
        return fallback
    if last_error:
        raise last_error
    raise LMStudioError("Title generation returned an empty or generic title.")


def title_error_message(error: Exception | str) -> str:
    message = str(error).strip() or "Title generation failed."
    return message[:600]


def load_first_scene_title_payload(db, session_id: str) -> tuple[str, str, str, str]:
    row = db.execute(
        """
        SELECT sv.director_note, sv.generated_text, s.id AS scene_id, sv.id AS version_id
        FROM scene_versions sv
        JOIN scenes s ON s.id = sv.scene_id
        WHERE sv.session_id = ?
        ORDER BY s.created_at ASC, sv.version_index ASC, sv.created_at ASC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return "", "", "", ""
    return (
        row["director_note"] or "",
        row["generated_text"] or "",
        row["scene_id"],
        row["version_id"],
    )


def fetch_session_row(db, session_id: str):
    return db.execute(
        f"SELECT {SESSION_SELECT_COLUMNS} FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()


async def run_auto_title_update(
    session_id: str,
    *,
    scene_text: str = "",
    director_note: str = "",
    source: str = "manual",
    job_id: str | None = None,
    scene_id: str | None = None,
    version_id: str | None = None,
) -> SessionRead | None:
    attempt_at = utc_now()
    with db_session() as db:
        session = fetch_session_row(db, session_id)
        if session is None:
            return None
        if source == "background" and (
            not job_id
            or session["auto_title_job_id"] != job_id
            or session["auto_title_scene_id"] != scene_id
            or session["auto_title_version_id"] != version_id
        ):
            return row_to_session(session)
        if title_is_user_set(session):
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = 'user_set',
                    auto_title_error = NULL,
                    auto_title_job_id = NULL,
                    auto_title_scene_id = NULL,
                    auto_title_version_id = NULL
                WHERE id = ?
                """,
                (session_id,),
            )
            return row_to_session(fetch_session_row(db, session_id))
        if not is_generic_title(session["title"]):
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = CASE
                        WHEN auto_title_status = 'generated' THEN auto_title_status
                        ELSE 'skipped'
                    END,
                    auto_title_error = NULL,
                    auto_title_job_id = NULL,
                    auto_title_scene_id = NULL,
                    auto_title_version_id = NULL
                WHERE id = ?
                """,
                (session_id,),
            )
            return row_to_session(fetch_session_row(db, session_id))
        scene_count = db.execute(
            "SELECT COUNT(*) AS scene_count FROM scenes WHERE session_id = ?",
            (session_id,),
        ).fetchone()["scene_count"]
        if scene_count < 1:
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = 'skipped',
                    auto_title_error = 'No saved scene is available for titling.',
                    last_auto_title_attempt_at = ?
                WHERE id = ?
                """,
                (attempt_at, session_id),
            )
            return row_to_session(fetch_session_row(db, session_id))
        if scene_id and version_id:
            target = db.execute(
                """
                SELECT sv.director_note, sv.generated_text
                FROM scene_versions sv
                JOIN scenes s ON s.id = sv.scene_id
                WHERE sv.session_id = ? AND s.session_id = ?
                  AND s.id = ? AND sv.id = ? AND sv.scene_id = s.id
                """,
                (session_id, session_id, scene_id, version_id),
            ).fetchone()
            if target is None:
                return row_to_session(session)
            director_note = director_note or target["director_note"] or ""
            scene_text = scene_text or target["generated_text"] or ""
        elif not scene_text.strip():
            first_director_note, first_scene_text, scene_id, version_id = load_first_scene_title_payload(
                db, session_id
            )
            director_note = director_note or first_director_note
            scene_text = first_scene_text
        job_id = job_id or str(uuid4())
        if source != "background":
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = 'pending',
                    auto_title_error = NULL,
                    last_auto_title_attempt_at = ?,
                    auto_title_job_id = ?,
                    auto_title_scene_id = ?,
                    auto_title_version_id = ?
                WHERE id = ?
                """,
                (attempt_at, job_id, scene_id, version_id, session_id),
            )

    if not scene_text.strip():
        with db_session() as db:
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = 'failed',
                    auto_title_error = 'First scene text was empty.',
                    last_auto_title_attempt_at = ?,
                    auto_title_job_id = NULL,
                    auto_title_scene_id = NULL,
                    auto_title_version_id = NULL
                WHERE id = ? AND auto_title_job_id = ?
                """,
                (attempt_at, session_id, job_id),
            )
            return row_to_session(fetch_session_row(db, session_id))

    try:
        title = await generate_short_title(session_id, scene_text, director_note=director_note)
    except Exception as error:
        with db_session() as db:
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = 'failed',
                    auto_title_error = ?,
                    last_auto_title_attempt_at = ?,
                    auto_title_job_id = NULL,
                    auto_title_scene_id = NULL,
                    auto_title_version_id = NULL
                WHERE id = ? AND auto_title_job_id = ?
                """,
                (title_error_message(error), attempt_at, session_id, job_id),
            )
            return row_to_session(fetch_session_row(db, session_id))

    with db_session() as db:
        current = fetch_session_row(db, session_id)
        if current is None:
            return None
        job_matches = (
            current["auto_title_job_id"] == job_id
            and current["auto_title_scene_id"] == scene_id
            and current["auto_title_version_id"] == version_id
        )
        if not job_matches:
            return row_to_session(current)
        if job_matches and can_auto_title_row(current):
            db.execute(
                """
                UPDATE sessions
                SET title = ?,
                    title_source = 'auto',
                    auto_title_status = 'generated',
                    auto_title_error = NULL,
                    last_auto_title_attempt_at = ?,
                    auto_title_job_id = NULL,
                    auto_title_scene_id = NULL,
                    auto_title_version_id = NULL
                WHERE id = ? AND auto_title_job_id = ?
                """,
                (title, attempt_at, session_id, job_id),
            )
        elif title_is_user_set(current):
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = 'user_set',
                    auto_title_error = NULL,
                    auto_title_job_id = NULL,
                    auto_title_scene_id = NULL,
                    auto_title_version_id = NULL
                WHERE id = ?
                """,
                (session_id,),
            )
        else:
            db.execute(
                """
                UPDATE sessions
                SET auto_title_status = 'skipped',
                    auto_title_error = NULL,
                    auto_title_job_id = NULL,
                    auto_title_scene_id = NULL,
                    auto_title_version_id = NULL
                WHERE id = ?
                """,
                (session_id,),
            )
        return row_to_session(fetch_session_row(db, session_id))


async def auto_title_background_task(
    session_id: str,
    scene_id: str,
    version_id: str,
    job_id: str,
    scene_text: str,
    director_note: str,
) -> None:
    try:
        await run_auto_title_update(
            session_id,
            scene_text=scene_text,
            director_note=director_note,
            source="background",
            job_id=job_id,
            scene_id=scene_id,
            version_id=version_id,
        )
    except Exception:
        logger.exception("Background auto-title task failed for session %s", session_id)


def schedule_auto_title_after_first_scene(scene: SceneRead) -> None:
    if scene.mode != "continue":
        return
    scene_id = scene.id
    version_id = scene.active_version_id or (scene.versions[-1].id if scene.versions else "")
    if not version_id:
        return
    job_id = str(uuid4())
    with db_session() as db:
        session = fetch_session_row(db, scene.session_id)
        if session is None or not can_auto_title_row(session):
            return
        if session["auto_title_status"] == "pending" and session["auto_title_job_id"]:
            return
        scene_count = db.execute(
            "SELECT COUNT(*) AS scene_count FROM scenes WHERE session_id = ?",
            (scene.session_id,),
        ).fetchone()["scene_count"]
        if scene_count != 1:
            return
        target = db.execute(
            """
            SELECT 1
            FROM scene_versions sv
            JOIN scenes s ON s.id = sv.scene_id
            WHERE sv.session_id = ? AND s.session_id = ?
              AND s.id = ? AND sv.id = ?
            """,
            (scene.session_id, scene.session_id, scene_id, version_id),
        ).fetchone()
        if target is None:
            return
        db.execute(
            """
            UPDATE sessions
            SET auto_title_status = 'pending', auto_title_error = NULL,
                last_auto_title_attempt_at = ?, auto_title_job_id = ?,
                auto_title_scene_id = ?, auto_title_version_id = ?
            WHERE id = ?
            """,
            (utc_now(), job_id, scene_id, version_id, scene.session_id),
        )
    try:
        asyncio.create_task(
            auto_title_background_task(
                scene.session_id,
                scene_id,
                version_id,
                job_id,
                scene.generated_text or (scene.versions[-1].generated_text if scene.versions else ""),
                scene.director_note or (scene.versions[-1].director_note if scene.versions else ""),
            )
        )
    except RuntimeError:
        logger.warning("Could not schedule auto-title task because no event loop was running.")


def prepare_generation(session_id: str, payload: GenerateSceneRequest) -> dict:
    director_note = payload.director_note.strip()

    with db_session() as db:
        session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        grouped_scenes, branch_to_root = load_grouped_scene_bundle(db, session_id)
        is_first_scene = payload.mode == "continue" and not grouped_scenes
        recent_scenes = trim_recent_scenes_for_prompt(
            [scene_read_to_prompt_dict(scene) for scene in grouped_scenes[-RECENT_SCENE_LIMIT:]]
        )

        target_scene = None
        target_scene_id = payload.target_scene_id
        if target_scene_id in branch_to_root:
            target_scene_id = branch_to_root[target_scene_id]
        if not target_scene_id and payload.mode in VERSIONED_MODES and grouped_scenes:
            target_scene_id = grouped_scenes[-1].id

        if payload.mode in VERSIONED_MODES:
            if not target_scene_id:
                raise HTTPException(status_code=400, detail="No target scene is available for this action.")
            target_read = next((scene for scene in grouped_scenes if scene.id == target_scene_id), None)
            if target_read is None:
                raise HTTPException(status_code=404, detail="Target scene not found")
            target_scene = scene_read_to_prompt_dict(target_read)
            if payload.mode == "regenerate" and not director_note:
                director_note = (
                    target_read.versions[0].director_note
                    if target_read.versions
                    else target_scene["director_note"]
                )

        if payload.mode == "rewrite" and not director_note:
            director_note = "Rewrite this scene while preserving the same core story events."
        if not director_note:
            raise HTTPException(status_code=400, detail="Director note cannot be empty.")

        summary_row = db.execute(
            """
            SELECT summary_text
            FROM session_summaries
            WHERE session_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        session_summary = summary_row["summary_text"] if summary_row else None

    model_settings, resolved_model = resolve_task_model_settings(task_type_for_generation_mode(payload.mode))
    client = model_client_for_settings(model_settings)
    writing_length = resolve_writing_length_for_settings(model_settings, director_note)
    app_planning_enabled = True
    chapter_extension_enabled = chapter_extension_enabled_for_settings(model_settings)
    adherence_check_mode = normalize_adherence_check_mode(getattr(model_settings, "adherence_check_mode", "warn"))
    return {
        "client": client,
        "director_note": director_note,
        "model_settings": model_settings,
        "resolved_model": resolved_model,
        "writing_path": WRITING_PATH,
        "writing_process_mode": PIPELINE_NAME,
        "writing_process_label": WRITING_PATH_LABEL,
        "app_planning_enabled": app_planning_enabled,
        "chapter_extension_enabled": chapter_extension_enabled,
        "adherence_check_mode": adherence_check_mode,
        "writing_length": writing_length,
        "recent_scenes": recent_scenes,
        "session_summary": session_summary,
        "target_scene": target_scene,
        "target_scene_id": target_scene_id,
        "is_first_scene": is_first_scene,
    }


def save_generated_scene(
    *,
    session_id: str,
    director_note: str,
    generated_text: str,
    generation_stats: dict | None = None,
    mode: str,
    target_scene_id: str | None,
) -> SceneRead:
    stats_json = json.dumps(generation_stats or {}, ensure_ascii=False)
    with db_session() as db:
        if mode == "continue":
            scene_id = str(uuid4())
            version_id = str(uuid4())
            db.execute(
                """
                INSERT INTO scenes (id, session_id, director_note, generated_text, generation_stats_json, mode)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (scene_id, session_id, director_note, generated_text, stats_json, mode),
            )
            db.execute(
                """
                INSERT INTO scene_versions (
                    id, scene_id, session_id, director_note, generated_text, generation_stats_json, mode, version_index
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (version_id, scene_id, session_id, director_note, generated_text, stats_json, mode),
            )
        else:
            if not target_scene_id:
                raise HTTPException(status_code=400, detail="Target scene is required for versioned generation.")
            scene_id = target_scene_id
            latest_index = db.execute(
                "SELECT COALESCE(MAX(version_index), 0) AS max_index FROM scene_versions WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()["max_index"]
            version_id = str(uuid4())
            db.execute(
                """
                INSERT INTO scene_versions (
                    id, scene_id, session_id, director_note, generated_text, generation_stats_json, mode, version_index
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (version_id, scene_id, session_id, director_note, generated_text, stats_json, mode, latest_index + 1),
            )
            db.execute(
                """
                UPDATE scenes
                SET director_note = ?, generated_text = ?, generation_stats_json = ?, mode = ?
                WHERE id = ? AND session_id = ?
                """,
                (director_note, generated_text, stats_json, mode, scene_id, session_id),
            )

        db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (session_id,),
        )
        record_generation_run(
            db,
            session_id=session_id,
            scene_id=scene_id,
            version_id=version_id,
            mode=mode,
            stats=generation_stats or {},
        )
        scenes = load_grouped_scenes(db, session_id)
        scene = next((item for item in scenes if item.id == scene_id), None)

    if scene is None:
        raise HTTPException(status_code=500, detail="Generated scene could not be loaded.")
    return scene


def persist_generation_stats(scene: SceneRead, generation_stats: dict) -> SceneRead:
    stats_json = json.dumps(generation_stats or {}, ensure_ascii=False)
    with db_session() as db:
        db.execute("UPDATE scenes SET generation_stats_json = ? WHERE id = ?", (stats_json, scene.id))
        if scene.active_version_id:
            db.execute(
                "UPDATE scene_versions SET generation_stats_json = ? WHERE id = ?",
                (stats_json, scene.active_version_id),
            )
            update_generation_run(db, version_id=scene.active_version_id, stats=generation_stats)
    versions = [
        version.model_copy(update={"generation_stats": generation_stats})
        if scene.active_version_id and version.id == scene.active_version_id
        else version
        for version in scene.versions
    ]
    return scene.model_copy(update={"generation_stats": generation_stats, "versions": versions})


async def run_generation(session_id: str, payload: GenerateSceneRequest) -> SceneRead:
    request_started = perf_counter()
    request_received_at = utc_now()
    prepare_started = perf_counter()
    prepared = prepare_generation(session_id, payload)
    prepare_seconds = perf_counter() - prepare_started
    client = prepared["client"]
    model_settings = prepared["model_settings"]
    resolved_model = prepared["resolved_model"]
    task_profile = task_type_for_generation_mode(payload.mode)
    try:
        model_resolve_started = perf_counter()
        model = await resolve_model(client, model_settings.model)
        model_resolve_seconds = perf_counter() - model_resolve_started
        qwen_unload_started = perf_counter()
        qwen_unload = await ensure_qwen_unloaded_before_writing()
        qwen_unload_seconds = perf_counter() - qwen_unload_started
        prompt_mode = getattr(model_settings, "prose_prompt_mode", "standard") or "standard"
        task_notes_for_prompt = prose_task_notes_for_prompt(resolved_model.notes, prompt_mode)
        cleanup_result = cleanup_internal_generation_artifacts()
        foundation_result: dict[str, Any] | None = None
        foundation_time_seconds = 0.0
        if prepared.get("is_first_scene"):
            foundation_started = perf_counter()
            foundation_result = await ensure_story_foundation(
                session_id=session_id,
                director_note=prepared["director_note"],
                writing_length=prepared["writing_length"],
                force_refresh=False,
                apply_to_cards=True,
            )
            foundation_time_seconds = perf_counter() - foundation_started
        app_planning_enabled = prepared["app_planning_enabled"]
        planning_result = await run_scene_planning_pass(
            session_id=session_id,
            mode=payload.mode,
            director_note=prepared["director_note"],
            writing_length=prepared["writing_length"],
            recent_scenes=prepared["recent_scenes"],
            session_summary=prepared["session_summary"],
            target_scene=prepared["target_scene"],
            inherited_settings=model_settings,
            inherited_resolved=resolved_model,
            inherited_model=model,
        )
        scene_plan = planning_result["plan"]
        planning_metadata = planning_result["metadata"]
        planning_time_seconds = float(planning_metadata.get("duration_seconds") or 0.0)
        prompt_builder_started = perf_counter()
        world_notes = planning_result.get("world_notes") or load_world_notes(session_id)
        active_characters = planning_result.get("active_characters") or load_active_characters(session_id)
        genre_time_helper = build_genre_time_helper(
            director_note=prepared["director_note"],
            world_notes=world_notes,
            session_summary=prepared["session_summary"],
            recent_scenes=prepared["recent_scenes"],
            target_scene=prepared["target_scene"],
        )
        user_prompt = build_scene_prompt(
            session_id=session_id,
            director_note=prepared["director_note"],
            mode=payload.mode,
            recent_scenes=prepared["recent_scenes"],
            session_summary=prepared["session_summary"],
            target_scene=prepared["target_scene"],
            task_notes=task_notes_for_prompt,
            writing_length=prepared["writing_length"],
            writing_process_plan=scene_plan,
            world_notes=world_notes,
            active_characters=active_characters,
            genre_time_helper=genre_time_helper,
            prompt_mode=prompt_mode,
        )
        prompt_builder_seconds = perf_counter() - prompt_builder_started
        prompt_diagnostics = build_prompt_diagnostics(
            system_prompt=model_settings.system_prompt,
            user_prompt=user_prompt,
            task_notes=task_notes_for_prompt,
            director_note=prepared["director_note"],
            session_summary=prepared["session_summary"],
            recent_scenes=prepared["recent_scenes"],
        )
        parameters = generation_parameters(model_settings, prepared["writing_length"])
        write_prose_debug_files(
            session_id=session_id,
            mode=payload.mode,
            model=model,
            task_profile=task_profile,
            task_label=resolved_model.label,
            system_prompt=model_settings.system_prompt,
            user_prompt=user_prompt,
            task_notes=task_notes_for_prompt,
            director_note=prepared["director_note"],
            writing_length=prepared["writing_length"],
            parameters=parameters,
            prompt_diagnostics=prompt_diagnostics,
            lm_studio_url=model_settings.lm_studio_url,
            resolved_timeout_seconds=resolved_model.timeout_seconds,
            streaming=False,
            prompt_mode=prompt_mode,
            writing_path=prepared["writing_path"],
            app_planning_enabled=app_planning_enabled,
            chapter_extension_enabled=prepared["chapter_extension_enabled"],
            adherence_check_mode=prepared["adherence_check_mode"],
            scene_plan=scene_plan,
            genre_time_helper=genre_time_helper,
            planning_time_seconds=round(planning_time_seconds, 3),
            inference_backend=resolved_model.inference_backend,
            reasoning_mode=resolved_model.reasoning_mode,
            context_length=resolved_model.context_length,
            fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
        )
        generation_started = perf_counter()
        lm_request_started_at = utc_now()
        routed_generation = await client.generate_scene_routed(
            model=model,
            system_prompt=model_settings.system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=resolved_model.timeout_seconds,
            inference_backend=resolved_model.inference_backend,
            reasoning_mode=resolved_model.reasoning_mode,
            context_length=resolved_model.context_length,
            fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
        )
        generated_text = routed_generation["text"]
        chapter_extension_used = False
        initial_word_count = word_count(generated_text)
        if prepared["chapter_extension_enabled"] and chapter_needs_extension(generated_text, prepared["writing_length"]):
            extension_generation = await client.generate_scene_routed(
                model=model,
                system_prompt=model_settings.system_prompt,
                user_prompt=build_chapter_extension_prompt(
                    director_note=prepared["director_note"],
                    current_text=generated_text,
                    writing_length=prepared["writing_length"],
                ),
                parameters=parameters,
                timeout=resolved_model.timeout_seconds,
                inference_backend=resolved_model.inference_backend,
                reasoning_mode=resolved_model.reasoning_mode,
                context_length=resolved_model.context_length,
                fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
            )
            extension_text = extension_generation["text"]
            if extension_text.strip():
                generated_text = f"{generated_text.rstrip()}\n\n{extension_text.strip()}"
                chapter_extension_used = True
        prose_model_seconds = perf_counter() - generation_started
        review_result = await run_quality_review_pass(
            context_text=planning_result["context_text"],
            scene_plan=scene_plan,
            draft_text=generated_text,
            writing_length=prepared["writing_length"],
            inherited_settings=model_settings,
            inherited_resolved=resolved_model,
            inherited_model=model,
        )
        quality_review = review_result["review"]
        review_metadata = review_result["metadata"]
        review_time_seconds = float(review_metadata.get("duration_seconds") or 0.0)
        repair_metadata: dict[str, Any] | None = None
        repair_ran = False
        if should_run_targeted_repair(quality_review):
            repair_result = await run_targeted_repair_pass(
                context_text=planning_result["context_text"],
                scene_plan=scene_plan,
                draft_text=generated_text,
                quality_review=quality_review,
                writing_length=prepared["writing_length"],
                inherited_settings=model_settings,
                inherited_resolved=resolved_model,
                inherited_model=model,
                prose_parameters=parameters,
            )
            generated_text = repair_result["text"]
            repair_metadata = repair_result["metadata"]
            repair_ran = True
        repair_time_seconds = float((repair_metadata or {}).get("duration_seconds") or 0.0)
        warning_details = (
            generation_adherence_warning_details(
                prepared["director_note"],
                generated_text,
                prepared["writing_length"],
                mode=payload.mode,
                target_scene=prepared["target_scene"],
            )
            if prepared["adherence_check_mode"] != "off"
            else []
        )
        if quality_review.get("severity") == "minor":
            warning_details.extend(
                {
                    "code": "quality_review_minor",
                    "severity": "minor",
                    "message": f"Quality review: {issue}",
                }
                for issue in quality_review.get("issues", [])[:4]
            )
        warnings = [detail["message"] for detail in warning_details]
        generation_stats = build_generation_stats(
            text=generated_text,
            elapsed_seconds=prose_model_seconds,
            model=model,
            task_profile=task_profile,
            task_label=resolved_model.label,
            writing_length=prepared["writing_length"],
            adherence_warnings=warnings,
            requested_max_tokens=parameters.get("max_tokens"),
            extra={
                "chapter_extension_used": chapter_extension_used,
                "initial_word_count": initial_word_count,
                "prose_prompt_mode": prompt_mode,
                "writing_path": prepared["writing_path"],
                "writing_path_label": WRITING_PATH_LABEL,
                "writing_process_mode": prepared["writing_process_mode"],
                "writing_process_label": prepared["writing_process_label"],
                "app_planning_enabled": app_planning_enabled,
                "chapter_extension_enabled": prepared["chapter_extension_enabled"],
                "adherence_check_mode": prepared["adherence_check_mode"],
                "model_template_note": "Thinking behavior is controlled by the loaded LM Studio model/template; StoryDriver only measures reasoning and prose speed.",
                "story_foundation": {
                    "status": (foundation_result or {}).get("status") or "not_needed",
                    "created_character_count": len(((foundation_result or {}).get("applied") or {}).get("created_characters") or []),
                    "updated_character_count": len(((foundation_result or {}).get("applied") or {}).get("updated_characters") or []),
                    "updated_world_fields": ((foundation_result or {}).get("applied") or {}).get("updated_world_fields") or [],
                    "fallback_used": bool(((foundation_result or {}).get("generation") or {}).get("fallback_used")),
                    "generation_error": ((foundation_result or {}).get("generation") or {}).get("error"),
                    "prompt_chars": ((foundation_result or {}).get("generation") or {}).get("prompt_chars"),
                    "configured_timeout_seconds": ((foundation_result or {}).get("generation") or {}).get("configured_timeout_seconds"),
                    "effective_timeout_seconds": ((foundation_result or {}).get("generation") or {}).get("effective_timeout_seconds"),
                    "duration_seconds": round(foundation_time_seconds, 3),
                },
                "scene_plan": None,
                "scene_plan_metadata": planning_metadata,
                "genre_time_helper": genre_time_helper,
                "planning_time_seconds": round(planning_time_seconds, 3),
                "review_time_seconds": review_time_seconds,
                "repair_time_seconds": repair_time_seconds,
                "repair_ran": repair_ran,
                "adherence_warning_details": warning_details,
                "deliberate_pipeline": compact_pipeline_stats(
                    planning_metadata=planning_metadata,
                    review_metadata=review_metadata,
                    repair_metadata=repair_metadata,
                    repair_ran=repair_ran,
                ),
                "inference_backend": routed_generation.get("backend", resolved_model.inference_backend),
                "requested_inference_backend": resolved_model.inference_backend,
                "reasoning_mode": resolved_model.reasoning_mode,
                "context_length": resolved_model.context_length,
                "native_chat_stats": routed_generation.get("stats") or {},
                "native_chat_fallback_used": bool(routed_generation.get("fallback_used")),
                "native_chat_warnings": routed_generation.get("warnings") or [],
                "thinking_budget": thinking_budget_metadata(
                    model_settings,
                    prepared["writing_length"],
                    parameters,
                ),
                "generation_pipeline": PIPELINE_NAME,
                "generation_pipeline_schema": PIPELINE_SCHEMA_VERSION,
                "finalization_recovery_used": bool(routed_generation.get("finalization_recovery")),
                "finalization_recovery_stats": routed_generation.get("finalization_recovery") or {},
                "primary_finish_reason": routed_generation.get("finish_reason"),
                "empty_scene_classification": routed_generation.get("empty_classification"),
                "reasoning_chars": routed_generation.get("reasoning_chars"),
                "lmstudio_raw_tokens_per_second": (routed_generation.get("stats") or {}).get("tokens_per_second"),
                "lmstudio_time_to_first_token_seconds": (routed_generation.get("stats") or {}).get("time_to_first_token_seconds"),
                "lmstudio_reasoning_output_tokens": (routed_generation.get("stats") or {}).get("reasoning_output_tokens"),
                "prompt_diagnostics": prompt_diagnostics,
                "prompt_size_warnings": prompt_diagnostics.get("prompt_size_warnings", []),
                "qwen_unload_before_writing": qwen_unload,
                "internal_generation_artifact_cleanup": cleanup_result,
                "background_jobs_delayed_until_after_save": True,
                "writing_priority": "background state, summary, title, and narration warmup jobs queue after scene save",
                "stage_timings": generation_stage_timing_breakdown(
                    prepare_generation_seconds=prepare_seconds,
                    model_resolve_seconds=model_resolve_seconds,
                    story_foundation_seconds=foundation_time_seconds,
                    planning_time_seconds=planning_time_seconds,
                    prompt_builder_seconds=prompt_builder_seconds,
                    pre_write_cleanup_seconds=qwen_unload_seconds,
                    request_to_lm_start_seconds=generation_started - request_started,
                    prose_model_seconds=prose_model_seconds,
                    review_seconds=review_time_seconds,
                    repair_seconds=repair_time_seconds,
                ),
                "latency": {
                    "client_submitted_at": payload.client_submitted_at,
                    "request_received_at": request_received_at,
                    "lm_request_started_at": lm_request_started_at,
                    "prompt_builder_seconds": round(prompt_builder_seconds, 3),
                    "story_foundation_seconds": round(foundation_time_seconds, 3),
                    "planning_time_seconds": round(planning_time_seconds, 3),
                    "prepare_generation_seconds": round(prepare_seconds, 3),
                    "model_resolve_seconds": round(model_resolve_seconds, 3),
                    "qwen_unload_seconds": round(qwen_unload_seconds, 3),
                    "request_to_lm_start_seconds": round(generation_started - request_started, 3),
                    "scene_generation_seconds": round(prose_model_seconds, 3),
                    "post_prose_quality_seconds": round(review_time_seconds + repair_time_seconds, 3),
                    "request_to_quality_complete_seconds": round(perf_counter() - request_started, 3),
                },
            },
        )
        log_generation_warnings(session_id, warnings)
    except LMStudioOfflineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LMStudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    save_started = perf_counter()
    scene = save_generated_scene(
        session_id=session_id,
        director_note=prepared["director_note"],
        generated_text=generated_text,
        generation_stats=generation_stats,
        mode=payload.mode,
        target_scene_id=prepared["target_scene_id"],
    )
    generation_stats.setdefault("latency", {})
    generation_stats["latency"].update(
        {
            "scene_save_seconds": round(perf_counter() - save_started, 3),
            "scene_saved_at": utc_now(),
            "request_to_scene_saved_seconds": round(perf_counter() - request_started, 3),
        }
    )
    generation_stats.setdefault("stage_timings", {}).update(
        {
            "scene_save_seconds": generation_stats["latency"]["scene_save_seconds"],
            "request_to_scene_saved_seconds": generation_stats["latency"]["request_to_scene_saved_seconds"],
        }
    )
    scene = persist_generation_stats(scene, generation_stats)
    schedule_auto_title_after_first_scene(scene)
    queue_scene_background_work(scene)
    generation_stats["latency"]["background_jobs_queued_at"] = utc_now()
    scene = persist_generation_stats(scene, generation_stats)
    return scene


def preview_generation_payload(payload: ProsePromptPreviewRequest) -> GenerateSceneRequest:
    director_note = payload.director_note.strip() or "Continue the story with the next grounded scene."
    return GenerateSceneRequest(
        director_note=director_note,
        mode=payload.mode,
        target_scene_id=payload.target_scene_id,
    )


def model_settings_with_preview_overrides(model_settings, payload: ProsePromptPreviewRequest):
    updates: dict[str, Any] = {}
    if payload.system_prompt_override is not None:
        updates["system_prompt"] = payload.system_prompt_override
    if payload.prose_prompt_mode_override is not None:
        updates["prose_prompt_mode"] = payload.prose_prompt_mode_override
    if payload.writing_process_mode_override is not None:
        updates["writing_process_mode"] = payload.writing_process_mode_override
    if payload.app_planning_enabled_override is not None:
        updates["app_planning_enabled"] = payload.app_planning_enabled_override
    if payload.chapter_extension_enabled_override is not None:
        updates["chapter_extension_enabled"] = payload.chapter_extension_enabled_override
    if payload.adherence_check_mode_override is not None:
        updates["adherence_check_mode"] = payload.adherence_check_mode_override
    if payload.writing_length_mode_override is not None:
        updates["writing_length_mode"] = payload.writing_length_mode_override
    if payload.custom_word_min_override is not None:
        updates["custom_word_min"] = payload.custom_word_min_override
    if payload.custom_word_max_override is not None:
        updates["custom_word_max"] = payload.custom_word_max_override
    if payload.max_tokens_override is not None:
        updates["max_tokens"] = payload.max_tokens_override
    return model_settings.model_copy(update=updates) if updates else model_settings


@router.post("/{session_id}/prose-prompt-preview", response_model=ProsePromptPreviewRead)
def prose_prompt_preview(session_id: str, payload: ProsePromptPreviewRequest) -> ProsePromptPreviewRead:
    generation_payload = preview_generation_payload(payload)
    prepared = prepare_generation(session_id, generation_payload)
    model_settings = model_settings_with_preview_overrides(prepared["model_settings"], payload)
    resolved_model = prepared["resolved_model"]
    task_profile = task_type_for_generation_mode(generation_payload.mode)
    prompt_mode = getattr(model_settings, "prose_prompt_mode", "standard") or "standard"
    raw_task_notes = payload.task_notes_override if payload.task_notes_override is not None else resolved_model.notes
    task_notes_for_prompt = prose_task_notes_for_prompt(raw_task_notes or "", prompt_mode)
    writing_length = resolve_writing_length_for_settings(model_settings, prepared["director_note"])
    app_planning_enabled = True
    chapter_extension_enabled = chapter_extension_enabled_for_settings(model_settings)
    adherence_check_mode = normalize_adherence_check_mode(getattr(model_settings, "adherence_check_mode", "warn"))
    world_notes = load_world_notes(session_id)
    active_characters = load_active_characters(session_id)
    scene_plan = deterministic_scene_plan(
        session_id=session_id,
        mode=generation_payload.mode,
        director_note=prepared["director_note"],
        writing_length=writing_length,
        recent_scenes=prepared["recent_scenes"],
        session_summary=prepared["session_summary"],
        target_scene=prepared["target_scene"],
        world_notes=world_notes,
        active_characters=active_characters,
    )
    genre_time_helper = build_genre_time_helper(
        director_note=prepared["director_note"],
        world_notes=world_notes,
        session_summary=prepared["session_summary"],
        recent_scenes=prepared["recent_scenes"],
        target_scene=prepared["target_scene"],
    )
    user_prompt = build_scene_prompt(
        session_id=session_id,
        director_note=prepared["director_note"],
        mode=generation_payload.mode,
        recent_scenes=prepared["recent_scenes"],
        session_summary=prepared["session_summary"],
        target_scene=prepared["target_scene"],
        task_notes=task_notes_for_prompt,
        writing_length=writing_length,
        writing_process_plan=scene_plan,
        world_notes=world_notes,
        active_characters=active_characters,
        genre_time_helper=genre_time_helper,
        prompt_mode=prompt_mode,
        mark_state_used=False,
    )
    prompt_diagnostics = build_prompt_diagnostics(
        system_prompt=model_settings.system_prompt,
        user_prompt=user_prompt,
        task_notes=task_notes_for_prompt,
        director_note=prepared["director_note"],
        session_summary=prepared["session_summary"],
        recent_scenes=prepared["recent_scenes"],
    )
    parameters = generation_parameters(model_settings, writing_length)
    notes = [
        "System prompt is the creative authority for prose.",
        "Story State is included as factual continuity only.",
        "Preview does not call LM Studio or save a scene.",
    ]
    if prompt_mode == "direct":
        notes.append("Direct mode keeps task notes visible/editable while keeping system prompt priority high.")
    notes.append("Every generation now runs structured scene planning, prose writing, quality review, and at most one targeted repair.")
    notes.append("Preview shows the deterministic plan fallback shape; live generation asks the routed scene-planning model first.")
    if not chapter_extension_enabled:
        notes.append("Chapter Extension is off, so short chapters are reported but not automatically extended.")
    notes.append("Thinking mode is controlled by the loaded LM Studio model/template; StoryDriver measures hidden reasoning, first visible prose, and speed.")
    if not (model_settings.model or resolved_model.model):
        notes.append("Model is blank, so generation will auto-select the first loaded LM Studio model.")
    return ProsePromptPreviewRead(
        session_id=session_id,
        mode=generation_payload.mode,
        prompt_mode=prompt_mode,
        writing_path=WRITING_PATH,
        writing_process_mode=PIPELINE_NAME,
        app_planning_enabled=app_planning_enabled,
        chapter_extension_enabled=chapter_extension_enabled,
        adherence_check_mode=adherence_check_mode,
        scene_plan=scene_plan,
        genre_time_helper=genre_time_helper,
        system_prompt=model_settings.system_prompt,
        task_notes=task_notes_for_prompt,
        user_prompt=user_prompt,
        director_note=prepared["director_note"],
        writing_length=writing_length,
        parameters=parameters,
        prompt_diagnostics=prompt_diagnostics,
        task_profile=task_profile,
        task_label=resolved_model.label,
        model=model_settings.model or resolved_model.model or "",
        lm_studio_url=model_settings.lm_studio_url,
        inference_backend=model_settings.inference_backend,
        reasoning_mode=model_settings.reasoning_mode,
        uses_global_model=resolved_model.uses_global_model,
        override_fields=resolved_model.override_fields,
        hidden_style_instructions=False,
        notes=notes,
    )


@router.get("", response_model=list[SessionRead])
def list_sessions() -> list[SessionRead]:
    with db_session() as db:
        rows = db.execute(
            f"""
            SELECT {SESSION_SELECT_COLUMNS}
            FROM sessions
            WHERE archived_at IS NULL
              AND COALESCE(deletion_status, '') != 'deleting'
            ORDER BY updated_at DESC, created_at DESC
            """
        ).fetchall()
    return [row_to_session(row) for row in rows]


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate) -> SessionRead:
    session_id = str(uuid4())
    title = payload.title.strip() or "Untitled Story"
    title_source = "placeholder" if is_generic_title(title) else "user_set"
    auto_title_status = "skipped" if title_source == "placeholder" else "user_set"

    with db_session() as db:
        db.execute(
            """
            INSERT INTO sessions (id, title, title_source, auto_title_status)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, title, title_source, auto_title_status),
        )
        row = db.execute(
            f"""
            SELECT {SESSION_SELECT_COLUMNS}
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()

    return row_to_session(row)


@router.get("/delete-jobs", response_model=list[SessionDeleteJobRead])
def list_session_delete_jobs(limit: int = Query(20, ge=1, le=100), include_completed: bool = Query(True)) -> list[SessionDeleteJobRead]:
    return [SessionDeleteJobRead(**job) for job in list_delete_jobs(limit=limit, include_completed=include_completed)]


@router.get("/delete-jobs/{job_id}", response_model=SessionDeleteJobRead)
def get_session_delete_job(job_id: str) -> SessionDeleteJobRead:
    job = get_delete_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Delete job not found")
    return SessionDeleteJobRead(**job)


@router.post("/delete-jobs/{job_id}/resume", response_model=SessionDeleteJobRead)
def resume_session_delete_job(job_id: str) -> SessionDeleteJobRead:
    try:
        job = resume_delete_job(job_id)
    except SessionDeleteNotFoundError:
        raise HTTPException(status_code=404, detail="Delete job not found") from None
    return SessionDeleteJobRead(**job)


@router.patch("/{session_id}", response_model=SessionRead)
def update_session(session_id: str, payload: SessionUpdate) -> SessionRead:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    with db_session() as db:
        existing = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Session not found")
        db.execute(
            """
            UPDATE sessions
            SET title = ?,
                title_source = 'user_set',
                auto_title_status = 'user_set',
                auto_title_error = NULL,
                auto_title_job_id = NULL,
                auto_title_scene_id = NULL,
                auto_title_version_id = NULL
            WHERE id = ?
            """,
            (title, session_id),
        )
        row = db.execute(
            f"""
            SELECT {SESSION_SELECT_COLUMNS}
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()

    return row_to_session(row)


@router.get("/{session_id}", response_model=SessionRead)
def get_session(session_id: str) -> SessionRead:
    with db_session() as db:
        row = fetch_session_row(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
    return row_to_session(row)


@router.post("/{session_id}/auto-title", response_model=SessionRead)
async def auto_title_session(session_id: str, payload: SessionAutoTitleRequest) -> SessionRead:
    session = await run_auto_title_update(
        session_id,
        scene_text=(payload.scene_text or "")[:80000],
        director_note=payload.director_note or "",
        source="api",
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/{session_id}/archive", response_model=SessionRead)
def archive_session(session_id: str) -> SessionRead:
    with db_session() as db:
        existing = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Session not found")
        db.execute(
            """
            UPDATE sessions
            SET archived_at = COALESCE(archived_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (session_id,),
        )
        row = db.execute(
            f"""
            SELECT {SESSION_SELECT_COLUMNS}
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    return row_to_session(row)


@router.post("/{session_id}/restore", response_model=SessionRead)
def restore_session(session_id: str) -> SessionRead:
    with db_session() as db:
        existing = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Session not found")
        db.execute(
            """
            UPDATE sessions
            SET archived_at = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (session_id,),
        )
        row = db.execute(
            f"""
            SELECT {SESSION_SELECT_COLUMNS}
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    return row_to_session(row)


@router.delete("/{session_id}", response_model=SessionDeleteResponse)
def delete_session(session_id: str, permanent: bool = Query(False)) -> SessionDeleteResponse:
    try:
        job = start_session_delete_job(session_id, permanent=permanent)
    except SessionDeleteNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found") from None

    return SessionDeleteResponse(
        id=job["session_id"],
        title=job["title"],
        deleted_at=job["started_at"],
        permanent=job["permanent"],
        status=job["status"],
        job_id=job["job_id"],
        message=job["message"],
        started_at=job["started_at"],
        finished_at=job.get("finished_at"),
        error=job.get("error"),
        counts=job.get("counts") or {},
        files_deleted=job.get("files_deleted") or [],
        files_skipped=job.get("files_skipped") or [],
    )


@router.get("/{session_id}/scenes", response_model=list[SceneRead])
def list_scenes(session_id: str) -> list[SceneRead]:
    with db_session() as db:
        session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        scenes = load_grouped_scenes(db, session_id)

    return scenes


@router.post("/{session_id}/generate", response_model=SceneRead, status_code=status.HTTP_201_CREATED)
async def generate_scene(session_id: str, payload: GenerateSceneRequest) -> SceneRead:
    try:
        async with story_generation_job():
            return await run_generation(session_id, payload)
    except GenerationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/generate-stream")
async def generate_scene_stream(session_id: str, payload: GenerateSceneRequest) -> StreamingResponse:
    async def events():
        def event(payload: dict) -> str:
            return json.dumps(payload) + "\n"

        try:
            async with story_generation_job():
                request_started = perf_counter()
                request_received_at = utc_now()
                yield event({"type": "status", "stage": "preparing_context", "message": "Preparing context"})
                prepare_started = perf_counter()
                prepared = prepare_generation(session_id, payload)
                client = prepared["client"]
                model_settings = prepared["model_settings"]
                resolved_model = prepared["resolved_model"]
                task_profile = task_type_for_generation_mode(payload.mode)
                yield event({"type": "status", "stage": "waiting_model", "message": "Waiting for model"})
                model_resolve_started = perf_counter()
                model = await resolve_model(client, model_settings.model)
                model_resolve_seconds = perf_counter() - model_resolve_started
                qwen_unload_started = perf_counter()
                qwen_unload = await ensure_qwen_unloaded_before_writing()
                qwen_unload_seconds = perf_counter() - qwen_unload_started
                prompt_mode = getattr(model_settings, "prose_prompt_mode", "standard") or "standard"
                task_notes_for_prompt = prose_task_notes_for_prompt(resolved_model.notes, prompt_mode)
                cleanup_result = cleanup_internal_generation_artifacts()
                foundation_result: dict[str, Any] | None = None
                foundation_time_seconds = 0.0
                if prepared.get("is_first_scene"):
                    yield event({"type": "status", "stage": "story_foundation", "message": "Creating story foundation"})
                    foundation_started = perf_counter()
                    foundation_result = await ensure_story_foundation(
                        session_id=session_id,
                        director_note=prepared["director_note"],
                        writing_length=prepared["writing_length"],
                        force_refresh=False,
                        apply_to_cards=True,
                    )
                    foundation_time_seconds = perf_counter() - foundation_started
                yield event({"type": "status", "stage": "planning_scene", "message": "Planning scene"})
                app_planning_enabled = prepared["app_planning_enabled"]
                planning_result = await run_scene_planning_pass(
                    session_id=session_id,
                    mode=payload.mode,
                    director_note=prepared["director_note"],
                    writing_length=prepared["writing_length"],
                    recent_scenes=prepared["recent_scenes"],
                    session_summary=prepared["session_summary"],
                    target_scene=prepared["target_scene"],
                    inherited_settings=model_settings,
                    inherited_resolved=resolved_model,
                    inherited_model=model,
                )
                scene_plan = planning_result["plan"]
                planning_metadata = planning_result["metadata"]
                planning_time_seconds = float(planning_metadata.get("duration_seconds") or 0.0)
                prompt_builder_started = perf_counter()
                world_notes = planning_result.get("world_notes") or load_world_notes(session_id)
                active_characters = planning_result.get("active_characters") or load_active_characters(session_id)
                genre_time_helper = build_genre_time_helper(
                    director_note=prepared["director_note"],
                    world_notes=world_notes,
                    session_summary=prepared["session_summary"],
                    recent_scenes=prepared["recent_scenes"],
                    target_scene=prepared["target_scene"],
                )
                user_prompt = build_scene_prompt(
                    session_id=session_id,
                    director_note=prepared["director_note"],
                    mode=payload.mode,
                    recent_scenes=prepared["recent_scenes"],
                    session_summary=prepared["session_summary"],
                    target_scene=prepared["target_scene"],
                    task_notes=task_notes_for_prompt,
                    writing_length=prepared["writing_length"],
                    writing_process_plan=scene_plan,
                    world_notes=world_notes,
                    active_characters=active_characters,
                    genre_time_helper=genre_time_helper,
                    prompt_mode=prompt_mode,
                )
                prompt_builder_seconds = perf_counter() - prompt_builder_started
                prompt_diagnostics = build_prompt_diagnostics(
                    system_prompt=model_settings.system_prompt,
                    user_prompt=user_prompt,
                    task_notes=task_notes_for_prompt,
                    director_note=prepared["director_note"],
                    session_summary=prepared["session_summary"],
                    recent_scenes=prepared["recent_scenes"],
                )
                parameters = generation_parameters(model_settings, prepared["writing_length"])
                write_prose_debug_files(
                    session_id=session_id,
                    mode=payload.mode,
                    model=model,
                    task_profile=task_profile,
                    task_label=resolved_model.label,
                    system_prompt=model_settings.system_prompt,
                    user_prompt=user_prompt,
                    task_notes=task_notes_for_prompt,
                    director_note=prepared["director_note"],
                    writing_length=prepared["writing_length"],
                    parameters=parameters,
                    prompt_diagnostics=prompt_diagnostics,
                    lm_studio_url=model_settings.lm_studio_url,
                    resolved_timeout_seconds=resolved_model.timeout_seconds,
                    streaming=bool(resolved_model.streaming),
                    prompt_mode=prompt_mode,
                    writing_path=prepared["writing_path"],
                    app_planning_enabled=app_planning_enabled,
                    chapter_extension_enabled=prepared["chapter_extension_enabled"],
                    adherence_check_mode=prepared["adherence_check_mode"],
                    scene_plan=scene_plan,
                    genre_time_helper=genre_time_helper,
                    planning_time_seconds=round(planning_time_seconds, 3),
                    inference_backend=resolved_model.inference_backend,
                    reasoning_mode=resolved_model.reasoning_mode,
                    context_length=resolved_model.context_length,
                    fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
                )
                prepare_seconds = perf_counter() - prepare_started
                yield event({"type": "status", "stage": "waiting_model", "message": "Waiting for model"})

                chunks: list[str] = []
                first_chunk = True
                generation_started = perf_counter()
                lm_request_started_at = utc_now()
                first_token_latency: float | None = None
                first_token_from_request: float | None = None
                first_reasoning_latency: float | None = None
                reasoning_chunks = 0
                reasoning_chars = 0
                thinking_status_sent = False
                native_chat_stats: dict = {}
                native_chat_warnings: list[str] = []
                native_chat_fallback_used = False
                primary_finish_reason: str | None = None
                primary_empty_classification: str | None = None
                primary_final_visible_chars = 0
                finalization_recovery_used = False
                finalization_recovery_stats: dict[str, Any] = {}
                finalization_reasoning_chars = 0

                async def routed_prose_events():
                    if resolved_model.streaming:
                        async for routed_event in client.stream_scene_events_routed(
                            model=model,
                            system_prompt=model_settings.system_prompt,
                            user_prompt=user_prompt,
                            parameters=parameters,
                            timeout=resolved_model.timeout_seconds,
                            inference_backend=resolved_model.inference_backend,
                            reasoning_mode=resolved_model.reasoning_mode,
                            context_length=resolved_model.context_length,
                            fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
                        ):
                            yield routed_event
                        return
                    result = await client.generate_scene_routed(
                        model=model,
                        system_prompt=model_settings.system_prompt,
                        user_prompt=user_prompt,
                        parameters=parameters,
                        timeout=resolved_model.timeout_seconds,
                        inference_backend=resolved_model.inference_backend,
                        reasoning_mode=resolved_model.reasoning_mode,
                        context_length=resolved_model.context_length,
                        fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
                    )
                    for warning in result.get("warnings") or []:
                        yield {"type": "warning", "message": warning, "fallback_used": result.get("fallback_used")}
                    yield {"type": "stats", "stats": result.get("stats") or {}}
                    yield {
                        "type": "final_result",
                        "text": result.get("text") or "",
                        "stats": result.get("stats") or {},
                        "finish_reason": result.get("finish_reason"),
                        "classification": result.get("empty_classification"),
                    }

                async for stream_event in routed_prose_events():
                    if stream_event.get("type") == "warning":
                        native_chat_fallback_used = bool(stream_event.get("fallback_used"))
                        warning = stream_event.get("message", "")
                        if warning:
                            native_chat_warnings.append(warning)
                            yield event({"type": "warning", "message": warning})
                        continue
                    if stream_event.get("type") == "stats":
                        stats = stream_event.get("stats")
                        if isinstance(stats, dict):
                            native_chat_stats = stats
                        continue
                    if stream_event.get("type") == "finish":
                        primary_finish_reason = stream_event.get("finish_reason")
                        continue
                    if stream_event.get("type") == "final_result":
                        primary_finish_reason = stream_event.get("finish_reason") or primary_finish_reason
                        primary_empty_classification = stream_event.get("classification") or primary_empty_classification
                        stats = stream_event.get("stats")
                        if isinstance(stats, dict) and stats:
                            native_chat_stats = stats
                        final_text = str(stream_event.get("text") or "")
                        primary_final_visible_chars = len(final_text)
                        if final_text and not chunks:
                            first_chunk = False
                            first_token_latency = perf_counter() - generation_started
                            first_token_from_request = perf_counter() - request_started
                            yield event({"type": "status", "stage": "writing_scene", "message": "Writing scene"})
                            chunks.append(final_text)
                            yield event({"type": "delta", "text": final_text})
                        continue
                    if stream_event.get("type") == "reasoning":
                        reasoning_text = stream_event.get("text", "")
                        reasoning_chunks += 1
                        reasoning_chars += len(reasoning_text)
                        if first_reasoning_latency is None:
                            first_reasoning_latency = perf_counter() - generation_started
                        if first_chunk and not thinking_status_sent:
                            thinking_status_sent = True
                            yield event(
                                {
                                    "type": "status",
                                    "stage": "thinking",
                                    "message": "Model is thinking",
                                }
                            )
                        continue
                    chunk = stream_event.get("text", "")
                    if not chunk:
                        continue
                    if first_chunk:
                        first_chunk = False
                        first_token_latency = perf_counter() - generation_started
                        first_token_from_request = perf_counter() - request_started
                        yield event({"type": "status", "stage": "writing_scene", "message": "Writing scene"})
                    chunks.append(chunk)
                    yield event({"type": "delta", "text": chunk})

                generated_text = "".join(chunks).strip()
                if not generated_text:
                    if reasoning_chars > 0:
                        yield event(
                            {
                                "type": "status",
                                "stage": "finalizing_scene",
                                "message": "The model finished thinking but did not produce prose. StoryDriver is finalizing the scene...",
                            }
                        )
                        finalization_recovery_used = True
                        async for recovery_event in client.stream_finalization_recovery_events(
                            model=model,
                            original_system_prompt=model_settings.system_prompt,
                            original_user_prompt=user_prompt,
                            parameters=parameters,
                            timeout=resolved_model.timeout_seconds,
                        ):
                            if recovery_event.get("type") == "stats":
                                stats = recovery_event.get("stats")
                                if isinstance(stats, dict):
                                    finalization_recovery_stats = stats
                                continue
                            if recovery_event.get("type") == "finish":
                                finalization_recovery_stats["finish_reason"] = recovery_event.get("finish_reason")
                                continue
                            if recovery_event.get("type") == "final_result":
                                stats = recovery_event.get("stats")
                                if isinstance(stats, dict) and stats:
                                    finalization_recovery_stats.update(stats)
                                final_text = str(recovery_event.get("text") or "")
                                if final_text and not chunks:
                                    if first_token_latency is None:
                                        first_token_latency = perf_counter() - generation_started
                                        first_token_from_request = perf_counter() - request_started
                                    yield event({"type": "status", "stage": "writing_scene", "message": "Writing scene"})
                                    chunks.append(final_text)
                                    yield event({"type": "delta", "text": final_text})
                                continue
                            if recovery_event.get("type") == "reasoning":
                                text = recovery_event.get("text", "")
                                finalization_reasoning_chars += len(text)
                                continue
                            chunk = recovery_event.get("text", "")
                            if not chunk:
                                continue
                            if first_token_latency is None:
                                first_token_latency = perf_counter() - generation_started
                                first_token_from_request = perf_counter() - request_started
                                yield event({"type": "status", "stage": "writing_scene", "message": "Writing scene"})
                            chunks.append(chunk)
                            yield event({"type": "delta", "text": chunk})
                        generated_text = "".join(chunks).strip()
                    if not generated_text:
                        if primary_empty_classification == "output_budget_exhausted":
                            raise LMStudioError("The model exhausted its output budget before writing the scene.")
                        if primary_empty_classification == "reasoning_channel_unclosed":
                            raise LMStudioError("The model stayed in its thinking channel and did not produce visible prose.")
                        if primary_empty_classification == "reasoning_only_output" or reasoning_chars > 0:
                            raise LMStudioError("The model finished thinking but did not produce visible prose.")
                        raise LMStudioError("LM Studio returned an empty scene.")
                chapter_extension_used = False
                initial_word_count = word_count(generated_text)
                chapter_extension_reasoning_chars = 0
                chapter_extension_finish_reason: str | None = None
                chapter_extension_empty_classification: str | None = None
                chapter_extension_finalization_recovery_used = False
                if prepared["chapter_extension_enabled"] and chapter_needs_extension(generated_text, prepared["writing_length"]):
                    yield event({"type": "status", "stage": "writing_scene", "message": "Extending chapter"})
                    extension_prompt = build_chapter_extension_prompt(
                        director_note=prepared["director_note"],
                        current_text=generated_text,
                        writing_length=prepared["writing_length"],
                    )
                    extension_started = False

                    async def append_extension_text(text: str) -> None:
                        nonlocal chapter_extension_used, extension_started
                        if not text:
                            return
                        if not extension_started:
                            extension_started = True
                            chapter_extension_used = True
                            chunks.append("\n\n")
                            yield_separator = event({"type": "delta", "text": "\n\n"})
                            await response_queue.put(yield_separator)
                        chunks.append(text)
                        await response_queue.put(event({"type": "delta", "text": text}))

                    response_queue: asyncio.Queue[str] = asyncio.Queue()

                    async for extension_event in client.stream_scene_events_routed(
                        model=model,
                        system_prompt=model_settings.system_prompt,
                        user_prompt=extension_prompt,
                        parameters=parameters,
                        timeout=resolved_model.timeout_seconds,
                        inference_backend=resolved_model.inference_backend,
                        reasoning_mode=resolved_model.reasoning_mode,
                        context_length=resolved_model.context_length,
                        fallback_to_openai_compatible=resolved_model.fallback_to_openai_compatible,
                    ):
                        if extension_event.get("type") == "warning":
                            warning = extension_event.get("message", "")
                            if warning:
                                native_chat_warnings.append(warning)
                                yield event({"type": "warning", "message": warning})
                            continue
                        if extension_event.get("type") == "finish":
                            chapter_extension_finish_reason = extension_event.get("finish_reason")
                            continue
                        if extension_event.get("type") == "final_result":
                            chapter_extension_finish_reason = (
                                extension_event.get("finish_reason") or chapter_extension_finish_reason
                            )
                            chapter_extension_empty_classification = (
                                extension_event.get("classification") or chapter_extension_empty_classification
                            )
                            final_text = str(extension_event.get("text") or "")
                            if final_text and not extension_started:
                                await append_extension_text(final_text)
                                while not response_queue.empty():
                                    yield await response_queue.get()
                            continue
                        if extension_event.get("type") == "reasoning":
                            chapter_extension_reasoning_chars += len(str(extension_event.get("text") or ""))
                            continue
                        chunk = str(extension_event.get("text") or "")
                        if not chunk:
                            continue
                        await append_extension_text(chunk)
                        while not response_queue.empty():
                            yield await response_queue.get()

                    if not extension_started and chapter_extension_reasoning_chars > 0:
                        chapter_extension_finalization_recovery_used = True
                        yield event(
                            {
                                "type": "status",
                                "stage": "finalizing_scene",
                                "message": "The model finished thinking but did not produce extension prose. StoryDriver is finalizing the chapter...",
                            }
                        )
                        async for recovery_event in client.stream_finalization_recovery_events(
                            model=model,
                            original_system_prompt=model_settings.system_prompt,
                            original_user_prompt=extension_prompt,
                            parameters=parameters,
                            timeout=resolved_model.timeout_seconds,
                        ):
                            if recovery_event.get("type") == "finish":
                                chapter_extension_finish_reason = recovery_event.get("finish_reason")
                                continue
                            if recovery_event.get("type") == "final_result":
                                final_text = str(recovery_event.get("text") or "")
                                if final_text and not extension_started:
                                    await append_extension_text(final_text)
                                    while not response_queue.empty():
                                        yield await response_queue.get()
                                continue
                            if recovery_event.get("type") == "reasoning":
                                finalization_reasoning_chars += len(str(recovery_event.get("text") or ""))
                                continue
                            chunk = str(recovery_event.get("text") or "")
                            if not chunk:
                                continue
                            await append_extension_text(chunk)
                            while not response_queue.empty():
                                yield await response_queue.get()
                    generated_text = "".join(chunks).strip()
                prose_model_seconds = perf_counter() - generation_started
                yield event({"type": "status", "stage": "checking_continuity", "message": "Checking continuity"})
                review_result = await run_quality_review_pass(
                    context_text=planning_result["context_text"],
                    scene_plan=scene_plan,
                    draft_text=generated_text,
                    writing_length=prepared["writing_length"],
                    inherited_settings=model_settings,
                    inherited_resolved=resolved_model,
                    inherited_model=model,
                )
                quality_review = review_result["review"]
                review_metadata = review_result["metadata"]
                review_time_seconds = float(review_metadata.get("duration_seconds") or 0.0)
                repair_metadata: dict[str, Any] | None = None
                repair_ran = False
                if should_run_targeted_repair(quality_review):
                    yield event({"type": "status", "stage": "refining_scene", "message": "Refining scene"})
                    repair_result = await run_targeted_repair_pass(
                        context_text=planning_result["context_text"],
                        scene_plan=scene_plan,
                        draft_text=generated_text,
                        quality_review=quality_review,
                        writing_length=prepared["writing_length"],
                        inherited_settings=model_settings,
                        inherited_resolved=resolved_model,
                        inherited_model=model,
                        prose_parameters=parameters,
                    )
                    generated_text = repair_result["text"]
                    chunks = [generated_text]
                    repair_metadata = repair_result["metadata"]
                    repair_ran = True
                    yield event({"type": "replace", "text": generated_text})
                repair_time_seconds = float((repair_metadata or {}).get("duration_seconds") or 0.0)
                warning_details = (
                    generation_adherence_warning_details(
                        prepared["director_note"],
                        generated_text,
                        prepared["writing_length"],
                        mode=payload.mode,
                        target_scene=prepared["target_scene"],
                    )
                    if prepared["adherence_check_mode"] != "off"
                    else []
                )
                if quality_review.get("severity") == "minor":
                    warning_details.extend(
                        {
                            "code": "quality_review_minor",
                            "severity": "minor",
                            "message": f"Quality review: {issue}",
                        }
                        for issue in quality_review.get("issues", [])[:4]
                    )
                warnings = [detail["message"] for detail in warning_details]
                generation_elapsed = prose_model_seconds
                visible_tokens = estimated_token_count(generated_text)
                reasoning_tokens = estimated_token_count("x" * reasoning_chars) if reasoning_chars else 0
                raw_stream_tokens = visible_tokens + reasoning_tokens
                visible_after_first_seconds = (
                    max(0.001, generation_elapsed - first_token_latency)
                    if first_token_latency is not None
                    else None
                )
                generation_stats = build_generation_stats(
                    text=generated_text,
                    elapsed_seconds=generation_elapsed,
                    model=model,
                    task_profile=task_profile,
                    task_label=resolved_model.label,
                    first_token_latency_seconds=first_token_latency,
                    writing_length=prepared["writing_length"],
                    adherence_warnings=warnings,
                    requested_max_tokens=parameters.get("max_tokens"),
                    extra={
                        "chapter_extension_used": chapter_extension_used,
                        "initial_word_count": initial_word_count,
                        "chapter_extension_reasoning_chars": chapter_extension_reasoning_chars,
                        "chapter_extension_finish_reason": chapter_extension_finish_reason,
                        "chapter_extension_empty_classification": chapter_extension_empty_classification,
                        "chapter_extension_finalization_recovery_used": chapter_extension_finalization_recovery_used,
                        "prompt_diagnostics": prompt_diagnostics,
                        "prompt_size_warnings": prompt_diagnostics.get("prompt_size_warnings", []),
                        "prose_prompt_mode": prompt_mode,
                        "writing_path": prepared["writing_path"],
                        "writing_path_label": WRITING_PATH_LABEL,
                        "writing_process_mode": prepared["writing_process_mode"],
                        "writing_process_label": prepared["writing_process_label"],
                        "app_planning_enabled": app_planning_enabled,
                        "chapter_extension_enabled": prepared["chapter_extension_enabled"],
                        "adherence_check_mode": prepared["adherence_check_mode"],
                        "model_template_note": "Thinking behavior is controlled by the loaded LM Studio model/template; StoryDriver only measures reasoning and prose speed.",
                        "story_foundation": {
                            "status": (foundation_result or {}).get("status") or "not_needed",
                            "created_character_count": len(((foundation_result or {}).get("applied") or {}).get("created_characters") or []),
                            "updated_character_count": len(((foundation_result or {}).get("applied") or {}).get("updated_characters") or []),
                            "updated_world_fields": ((foundation_result or {}).get("applied") or {}).get("updated_world_fields") or [],
                            "fallback_used": bool(((foundation_result or {}).get("generation") or {}).get("fallback_used")),
                            "generation_error": ((foundation_result or {}).get("generation") or {}).get("error"),
                            "prompt_chars": ((foundation_result or {}).get("generation") or {}).get("prompt_chars"),
                            "configured_timeout_seconds": ((foundation_result or {}).get("generation") or {}).get("configured_timeout_seconds"),
                            "effective_timeout_seconds": ((foundation_result or {}).get("generation") or {}).get("effective_timeout_seconds"),
                            "duration_seconds": round(foundation_time_seconds, 3),
                        },
                        "scene_plan": None,
                        "scene_plan_metadata": planning_metadata,
                        "genre_time_helper": genre_time_helper,
                        "planning_time_seconds": round(planning_time_seconds, 3),
                        "review_time_seconds": review_time_seconds,
                        "repair_time_seconds": repair_time_seconds,
                        "repair_ran": repair_ran,
                        "adherence_warning_details": warning_details,
                        "deliberate_pipeline": compact_pipeline_stats(
                            planning_metadata=planning_metadata,
                            review_metadata=review_metadata,
                            repair_metadata=repair_metadata,
                            repair_ran=repair_ran,
                        ),
                        "inference_backend": "openai_compatible"
                        if native_chat_fallback_used
                        else resolved_model.inference_backend,
                        "requested_inference_backend": resolved_model.inference_backend,
                        "reasoning_mode": resolved_model.reasoning_mode,
                        "context_length": resolved_model.context_length,
                        "native_chat_stats": native_chat_stats,
                        "native_chat_fallback_used": native_chat_fallback_used,
                        "native_chat_warnings": native_chat_warnings,
                        "thinking_budget": thinking_budget_metadata(
                            model_settings,
                            prepared["writing_length"],
                            parameters,
                        ),
                        "generation_pipeline": PIPELINE_NAME,
                        "generation_pipeline_schema": PIPELINE_SCHEMA_VERSION,
                        "finalization_recovery_used": finalization_recovery_used,
                        "finalization_recovery_stats": finalization_recovery_stats,
                        "finalization_reasoning_chars": finalization_reasoning_chars,
                        "primary_finish_reason": primary_finish_reason,
                        "empty_scene_classification": primary_empty_classification,
                        "primary_final_visible_chars": primary_final_visible_chars,
                        "lmstudio_raw_tokens_per_second": native_chat_stats.get("tokens_per_second"),
                        "lmstudio_time_to_first_token_seconds": native_chat_stats.get("time_to_first_token_seconds"),
                        "lmstudio_reasoning_output_tokens": native_chat_stats.get("reasoning_output_tokens"),
                        "qwen_unload_before_writing": qwen_unload,
                        "internal_generation_artifact_cleanup": cleanup_result,
                        "background_jobs_delayed_until_after_save": True,
                        "writing_priority": "background state, summary, title, and narration warmup jobs queue after scene save",
                        "first_reasoning_latency_seconds": round(first_reasoning_latency, 3)
                        if first_reasoning_latency is not None
                        else None,
                        "reasoning_chunks": reasoning_chunks,
                        "reasoning_chars": reasoning_chars,
                        "reasoning_tokens_estimated": reasoning_tokens,
                        "reasoning_tokens_per_second": round(reasoning_tokens / generation_elapsed, 2)
                        if reasoning_tokens and generation_elapsed > 0
                        else None,
                        "raw_stream_tokens_estimated": raw_stream_tokens,
                        "raw_stream_tokens_per_second": round(raw_stream_tokens / generation_elapsed, 2)
                        if raw_stream_tokens and generation_elapsed > 0
                        else None,
                        "visible_prose_tokens_after_first_per_second": round(visible_tokens / visible_after_first_seconds, 2)
                        if visible_after_first_seconds
                        else None,
                        "visible_after_first_seconds": round(visible_after_first_seconds, 3)
                        if visible_after_first_seconds
                        else None,
                        "stage_timings": generation_stage_timing_breakdown(
                            prepare_generation_seconds=prepare_seconds,
                            model_resolve_seconds=model_resolve_seconds,
                            story_foundation_seconds=foundation_time_seconds,
                            planning_time_seconds=planning_time_seconds,
                            prompt_builder_seconds=prompt_builder_seconds,
                            pre_write_cleanup_seconds=qwen_unload_seconds,
                            request_to_lm_start_seconds=generation_started - request_started,
                            request_to_first_visible_prose_seconds=first_token_from_request,
                            lm_request_to_first_visible_prose_seconds=first_token_latency,
                            prose_model_seconds=prose_model_seconds,
                            review_seconds=review_time_seconds,
                            repair_seconds=repair_time_seconds,
                        ),
                        "latency": {
                            "client_submitted_at": payload.client_submitted_at,
                            "request_received_at": request_received_at,
                            "lm_request_started_at": lm_request_started_at,
                            "prompt_builder_seconds": round(prompt_builder_seconds, 3),
                            "story_foundation_seconds": round(foundation_time_seconds, 3),
                            "planning_time_seconds": round(planning_time_seconds, 3),
                            "prepare_generation_seconds": round(prepare_seconds, 3),
                            "model_resolve_seconds": round(model_resolve_seconds, 3),
                            "qwen_unload_seconds": round(qwen_unload_seconds, 3),
                            "request_to_lm_start_seconds": round(generation_started - request_started, 3),
                            "request_to_first_token_seconds": round(first_token_from_request, 3)
                            if first_token_from_request is not None
                            else None,
                            "lm_request_to_first_token_seconds": round(first_token_latency, 3)
                            if first_token_latency is not None
                            else None,
                            "scene_generation_seconds": round(generation_elapsed, 3),
                            "post_prose_quality_seconds": round(review_time_seconds + repair_time_seconds, 3),
                            "request_to_quality_complete_seconds": round(perf_counter() - request_started, 3),
                        },
                    },
                )
                log_generation_warnings(session_id, warnings)
                yield event({"type": "status", "stage": "saving_scene", "message": "Saving scene"})
                save_started = perf_counter()
                scene = save_generated_scene(
                    session_id=session_id,
                    director_note=prepared["director_note"],
                    generated_text=generated_text,
                    generation_stats=generation_stats,
                    mode=payload.mode,
                    target_scene_id=prepared["target_scene_id"],
                )
                generation_stats.setdefault("latency", {})
                generation_stats["latency"].update(
                    {
                        "scene_save_seconds": round(perf_counter() - save_started, 3),
                        "scene_saved_at": utc_now(),
                        "request_to_scene_saved_seconds": round(perf_counter() - request_started, 3),
                    }
                )
                generation_stats.setdefault("stage_timings", {}).update(
                    {
                        "scene_save_seconds": generation_stats["latency"]["scene_save_seconds"],
                        "request_to_scene_saved_seconds": generation_stats["latency"]["request_to_scene_saved_seconds"],
                    }
                )
                scene = persist_generation_stats(scene, generation_stats)
                schedule_auto_title_after_first_scene(scene)
                queue_scene_background_work(scene)
                generation_stats["latency"]["background_jobs_queued_at"] = utc_now()
                scene = persist_generation_stats(scene, generation_stats)
                for warning in warnings:
                    yield event({"type": "warning", "message": warning})
                yield event({"type": "scene", "scene": scene.model_dump()})
        except GenerationConflictError as exc:
            yield event({"type": "error", "detail": str(exc), "status": 409})
        except HTTPException as exc:
            yield event({"type": "error", "detail": exc.detail, "status": exc.status_code})
        except LMStudioOfflineError as exc:
            yield event({"type": "error", "detail": str(exc), "status": 503})
        except LMStudioError as exc:
            yield event({"type": "error", "detail": str(exc), "status": 502})
        except Exception as exc:
            yield event({"type": "error", "detail": f"Generation failed: {exc}", "status": 500})

    return StreamingResponse(events(), media_type="application/x-ndjson")
