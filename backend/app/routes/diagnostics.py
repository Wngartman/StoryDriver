from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from app.config import DATA_DIR, settings
from app.diagnostics.privacy import privacy_status
from app.generation.model_provider import LMStudioClient, get_last_native_chat_status, native_base_url_from_openai_base
from app.generation.router import resolve_all_task_model_settings
from app.memory.engine import (
    active_prompt_state_count,
    archived_state_count,
    disabled_state_count,
    manual_override_count,
    story_state_run_status,
)
from app.memory.summaries import summary_status
from app.settings.store import load_model_settings, load_story_state_settings, load_tts_settings
from app.tts.presynth import tts_presynth_status
from app.tts.service import TTSService
from app.utils.paths import ensure_runtime_paths


router = APIRouter(tags=["diagnostics"])


def load_latest_local_json(filename: str) -> dict[str, Any]:
    path = DATA_DIR / "logs" / filename
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def speed_snapshot() -> dict[str, Any]:
    latest = load_latest_local_json("LMSTUDIO_CURRENT_SPEED_TEST_LATEST.json")
    direct = next(
        (item for item in latest.get("direct_results") or [] if item.get("label") == "direct_minimal_current"),
        {},
    )
    story_stats = (latest.get("storydriver_result") or {}).get("generation_stats") or {}
    return {
        "last_test_at": latest.get("generated_at"),
        "model": latest.get("model"),
        "direct_visible_tps": direct.get("visible_tokens_per_second_estimated"),
        "direct_first_visible_seconds": direct.get("first_content_seconds"),
        "storydriver_visible_tps": story_stats.get("visible_prose_tokens_per_second") or story_stats.get("tokens_per_second"),
    }


async def lm_studio_snapshot(base_url: str, selected_model: str) -> dict[str, Any]:
    client = LMStudioClient(base_url)
    try:
        models = await client.list_models()
    except Exception as error:
        return {"reachable": False, "base_url": client.base_url, "selected_model": selected_model or None, "error": str(error)}
    return {
        "reachable": True,
        "base_url": client.base_url,
        "selected_model": selected_model or None,
        "models_count": len(models),
        "models": [str(item.get("id") or "") for item in models if item.get("id")],
    }


@router.get("/diagnostics")
async def diagnostics() -> dict[str, Any]:
    ensure_runtime_paths()
    model_settings = load_model_settings(resolve_active_preset=True)
    tts_settings = load_tts_settings()
    story_state_settings = load_story_state_settings()
    lm_studio = await lm_studio_snapshot(model_settings.lm_studio_url, model_settings.model.strip())
    tts = await TTSService().status()
    model_routing = {
        task_type: resolved.model_dump()
        for task_type, resolved in resolve_all_task_model_settings().items()
        if task_type != "image_prompt_generation"
    }
    return {
        "backend": "ok",
        "privacy": privacy_status(
            {
                "backend": settings.storydriver_backend_url,
                "frontend": settings.storydriver_frontend_url,
                "lm_studio_openai": model_settings.lm_studio_url,
                "lm_studio_rest": settings.lm_studio_rest_base_url,
                "kokoro": tts_settings.kokoro_base_url,
                "qwen_experimental": settings.qwen_tts_base_url,
            }
        ),
        "launcher": {
            "backend_url": settings.storydriver_backend_url,
            "frontend_url": settings.storydriver_frontend_url,
            "open_browser_after_start": settings.open_browser_after_start.lower() in {"1", "true", "yes", "y"},
            "restart_existing_services": settings.restart_existing_services.lower() in {"1", "true", "yes", "y"},
            "kokoro_start_configured": bool(settings.kokoro_start_command.strip()),
            "lm_studio_start_configured": bool(settings.lm_studio_start_command.strip()),
        },
        "lm_studio": {
            **lm_studio,
            "native_chat": {
                "base_url": native_base_url_from_openai_base(model_settings.lm_studio_url),
                "configured_backend": model_settings.inference_backend,
                "reasoning_mode": model_settings.reasoning_mode,
                "last_status": get_last_native_chat_status(),
            },
        },
        "model_settings": {
            "model": model_settings.model,
            "system_prompt_chars": len(model_settings.system_prompt or ""),
            "writing_length_mode": model_settings.writing_length_mode,
            "writing_path": model_settings.writing_path,
            "writing_process_mode": model_settings.writing_process_mode,
            "app_planning_enabled": model_settings.app_planning_enabled,
            "inference_backend": model_settings.inference_backend,
            "reasoning_mode": model_settings.reasoning_mode,
            "context_length": model_settings.context_length,
            "fallback_to_openai_compatible": model_settings.fallback_to_openai_compatible,
        },
        "model_routing": model_routing,
        "kokoro": tts.get("kokoro") or {},
        "tts": {
            "provider": tts_settings.tts_provider,
            "voice": tts_settings.tts_voice or "Profile default",
            "chunked_narration_mode": tts_settings.chunked_narration_mode,
            "tts_chunk_size": tts_settings.tts_chunk_size,
            "tts_prebuffer_chunks": tts_settings.tts_prebuffer_chunks,
            "tts_follow_mode": tts_settings.tts_follow_mode,
            "provider_registry": tts.get("provider_registry") or {},
            "qwen_experimental": tts.get("qwen_experimental") or {},
            "last_presynthesis": tts_presynth_status(),
        },
        "story_state": {
            "automatic_story_state": story_state_settings.automatic_story_state,
            "run_state_extraction_in_background": story_state_settings.run_state_extraction_in_background,
            "state_extraction_timeout_seconds": story_state_settings.state_extraction_timeout_seconds,
            "last_extraction": story_state_run_status(),
            "prompt_state_items_total": active_prompt_state_count(),
            "archived_state_items_total": archived_state_count(),
            "disabled_state_items_total": disabled_state_count(),
            "manual_override_items_total": manual_override_count(),
            "summary": summary_status(),
        },
        "system_speed": speed_snapshot(),
        "paths": {
            "database": str(settings.db_path),
            "generated_audio": str(DATA_DIR / "generated_audio"),
            "logs": str(DATA_DIR / "logs"),
        },
    }
