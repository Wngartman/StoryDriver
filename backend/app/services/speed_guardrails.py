from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.config import DATA_DIR
from app.services.comfyui_client import ComfyUIClient
from app.services.comfyui_idle_cleanup import idle_free_status, maybe_free_comfyui_before_writing
from app.services.generation_locks import current_generation_state
from app.services.image_regenerate_window import image_regenerate_window_status
from app.services.resource_status import build_resource_status
from app.settings.store import image_generation_is_paused, image_generation_mode


LOG_DIR = DATA_DIR / "logs"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
EXPECTED_FAST_TPS_MIN = 100.0
SPEED_WARNING_TPS_MIN = 70.0
SHARED_MEMORY_WARNING_BYTES = 1024**3
GPU_MEMORY_SNAPSHOT_MAX_AGE_SECONDS = 2 * 60 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(name: str) -> dict[str, Any]:
    path = LOG_DIR / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fresh_snapshot(value: Any, max_age_seconds: int = GPU_MEMORY_SNAPSHOT_MAX_AGE_SECONDS) -> bool:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return False
    age = datetime.now(timezone.utc) - parsed
    return 0 <= age.total_seconds() <= max_age_seconds


def _direct_result(run: dict[str, Any], label: str = "direct_minimal_current") -> dict[str, Any]:
    for item in run.get("direct_results") or []:
        if item.get("label") == label:
            return item
    return {}


def _select_gemma(instances: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = GEMMA_MODEL.lower()
    for item in instances:
        candidates = (
            item.get("identifier"),
            item.get("modelKey"),
            item.get("model_key"),
            item.get("displayName"),
            item.get("path"),
            item.get("indexedModelIdentifier"),
        )
        if any(target in str(candidate or "").lower() for candidate in candidates):
            return item
    return None


def _latest_speed_summary() -> dict[str, Any]:
    latest_speed = _load_json("LMSTUDIO_CURRENT_SPEED_TEST_LATEST.json")
    latest_vram = _load_json("VRAM_SPEED_DIAGNOSTICS_LATEST.json")
    latest_recovery = _load_json("VRAM_SPEED_RECOVERY_LATEST.json")
    direct_minimal = _direct_result(latest_speed)
    if not direct_minimal:
        direct_minimal = _direct_result(((latest_vram.get("speed") or {}).get("latest") or {}))
    if not direct_minimal:
        direct_minimal = _direct_result(((latest_recovery.get("before") or {}).get("speed") or {}).get("latest") or {})
    storydriver_stats = (latest_speed.get("storydriver_result") or {}).get("generation_stats") or {}
    vram_gemma = latest_vram.get("gemma") or {}
    recovery_final = _select_gemma(latest_recovery.get("final_lms_instances") or []) or {}
    gpu_summary = (latest_vram.get("gpu_memory") or {}).get("adapter_summary") or {}
    system = latest_vram.get("system") or {}
    classified = system.get("classified_processes") or {}
    vram_fresh = _fresh_snapshot(latest_vram.get("generated_at"))
    return {
        "last_test_at": latest_speed.get("generated_at") or latest_vram.get("generated_at"),
        "last_label": latest_speed.get("label") or latest_vram.get("label"),
        "direct_minimal_visible_tps": _safe_float(direct_minimal.get("visible_tokens_per_second_estimated")),
        "direct_minimal_hidden_reasoning_chars": _safe_int(direct_minimal.get("reasoning_chars")),
        "direct_minimal_first_visible_seconds": _safe_float(direct_minimal.get("first_content_seconds")),
        "storydriver_visible_tps": _safe_float(
            storydriver_stats.get("visible_prose_tokens_per_second") or storydriver_stats.get("tokens_per_second")
        ),
        "storydriver_first_visible_seconds": _safe_float(storydriver_stats.get("first_token_latency_seconds")),
        "context_length": _safe_int(recovery_final.get("contextLength") or vram_gemma.get("contextLength")),
        "parallel": _safe_int(recovery_final.get("parallel") or vram_gemma.get("parallel")),
        "model": direct_minimal.get("model") or latest_speed.get("model") or vram_gemma.get("modelKey"),
        "adapter_dedicated_usage_bytes": _safe_float(gpu_summary.get("dedicated_usage_bytes")) if vram_fresh else None,
        "adapter_shared_usage_bytes": _safe_float(gpu_summary.get("shared_usage_bytes")) if vram_fresh else None,
        "gpu_memory_snapshot_at": latest_vram.get("generated_at"),
        "gpu_memory_snapshot_stale": bool(latest_vram and not vram_fresh),
        "comfyui_processes": len(classified.get("comfyui") or []),
        "duplicate_comfyui_candidates": len(classified.get("duplicate_comfyui_candidates") or []),
    }


def _queue_busy(queue: dict[str, Any] | None) -> bool:
    if not isinstance(queue, dict):
        return False
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return bool((isinstance(running, list) and running) or (isinstance(pending, list) and pending))


async def _comfyui_queue(image_settings: Any) -> tuple[dict[str, Any] | None, str | None]:
    client = ComfyUIClient(getattr(image_settings, "comfyui_base_url", "http://localhost:8188"))
    try:
        return await client.queue(), None
    except Exception as error:  # noqa: BLE001
        return None, str(error)


def _background_job_snapshot(session_id: str | None = None) -> dict[str, Any]:
    from app.routes.images import image_job_status
    from app.services.scene_image_prompts import last_image_prompt_event
    from app.memory.summaries import summary_status
    from app.memory.engine import story_state_run_status
    from app.tts.presynth import tts_presynth_status

    state = current_generation_state()
    return {
        "story_generation_active": bool(state.get("story_generation_active")),
        "image_generation_active": bool(state.get("image_generation_active")),
        "active_story_session_id": state.get("active_story_session_id"),
        "active_image_job_id": state.get("active_image_job_id"),
        "image_regenerate_window": image_regenerate_window_status(),
        "image_job": image_job_status(),
        "tts_presynthesis": tts_presynth_status(),
        "story_state": story_state_run_status(session_id) if session_id else story_state_run_status(),
        "summary": summary_status(session_id) if session_id else summary_status(),
        "last_image_prompt": last_image_prompt_event(),
    }


async def build_writing_speed_preflight(
    model_settings: Any,
    image_settings: Any,
    *,
    selected_model: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    protection_mode = getattr(model_settings, "writing_speed_protection", "warn_only") or "warn_only"
    speed = _latest_speed_summary()
    resource_status = await build_resource_status()
    current_image_mode = image_generation_mode(image_settings)
    images_paused = image_generation_is_paused(image_settings)
    lm = resource_status.get("lm_studio") or {}
    loaded_instances = lm.get("loaded_instances") or []
    gemma = _select_gemma(loaded_instances)
    loaded_parallel = _safe_int((gemma or {}).get("parallel") or speed.get("parallel"))
    loaded_context = _safe_int((gemma or {}).get("contextLength") or speed.get("context_length"))
    loaded_model_count = _safe_int(lm.get("loaded_instance_count")) or len(loaded_instances)
    queue, queue_error = (None, None) if images_paused else await _comfyui_queue(image_settings)
    queue_is_busy = _queue_busy(queue)
    direct_tps = speed.get("direct_minimal_visible_tps")
    hidden_reasoning = speed.get("direct_minimal_hidden_reasoning_chars")
    shared_bytes = speed.get("adapter_shared_usage_bytes")
    duplicate_candidates = _safe_int(speed.get("duplicate_comfyui_candidates")) or 0
    warnings: list[str] = []
    recommendations: list[str] = []

    if not lm.get("openai_reachable"):
        warnings.append("LM Studio OpenAI-compatible API is not reachable.")
        recommendations.append("Start LM Studio server before writing.")
    if loaded_model_count > 1 or lm.get("multiple_loaded_models"):
        warnings.append(f"{loaded_model_count} LM Studio models are loaded; single-scene speed and image generation may suffer.")
        recommendations.append(r"Run D:\StoryDriver\scripts\verify_gemma_fast_runtime.bat to confirm only Gemma is loaded.")
    if loaded_parallel is not None and loaded_parallel > 1:
        warnings.append(f"LM Studio model is loaded with parallel={loaded_parallel}; fastest writing needs parallel=1.")
        recommendations.append(r"Run D:\StoryDriver\scripts\reload_gemma_fast_parallel1.bat --confirm or reload Gemma manually with parallel 1.")
    if direct_tps is not None and direct_tps < SPEED_WARNING_TPS_MIN:
        warnings.append(f"Last direct LM Studio speed was {direct_tps:.1f} t/s, below the fast-writing warning threshold.")
        recommendations.append(r"Run D:\StoryDriver\scripts\recover_storydriver_speed.bat --diagnose-only, then --confirm if the diagnosis is safe.")
    if hidden_reasoning is not None and hidden_reasoning > 20:
        warnings.append(f"Hidden reasoning returned in the last direct test ({hidden_reasoning} chars); the no-thinking template may not be active.")
        recommendations.append("Unload/reload Gemma in LM Studio with the StoryDriver no-thinking template.")
    if shared_bytes is not None and shared_bytes > SHARED_MEMORY_WARNING_BYTES:
        warnings.append("Windows GPU shared-memory use is elevated; LM Studio may be spilling outside dedicated VRAM.")
        recommendations.append(r"Run D:\StoryDriver\scripts\verify_gemma_fast_runtime.bat to confirm the writing model is loaded cleanly.")
    if duplicate_candidates and not images_paused:
        warnings.append(f"{duplicate_candidates} duplicate/stale ComfyUI process candidate(s) were detected in the last diagnostics run.")
        recommendations.append(r"Run D:\StoryDriver\scripts\cleanup_duplicate_comfyui.bat, then --apply only if the candidates are clearly safe.")
    if queue_is_busy and not images_paused:
        warnings.append("ComfyUI queue is busy; do not free image memory or start writing cleanup yet.")
    if not images_paused and not resource_status.get("comfyui", {}).get("reachable"):
        warnings.append("ComfyUI is offline; writing can continue, but image cleanup checks cannot run.")
    if (
        direct_tps is not None
        and direct_tps < SPEED_WARNING_TPS_MIN
        and not images_paused
        and not getattr(image_settings, "free_comfyui_before_writing", False)
    ):
        recommendations.append("Enable Free ComfyUI before writing, or set Writing Speed Protection to Auto.")

    background_jobs = _background_job_snapshot(session_id)
    if background_jobs.get("image_generation_active"):
        warnings.append("An image job is active; writing should wait or the job should finish first.")

    ok = protection_mode == "off" or not warnings
    return {
        "checked_at": utc_now(),
        "protection_mode": protection_mode,
        "ok": ok,
        "warnings": warnings,
        "recommendations": recommendations[:6],
        "selected_prose_model": selected_model or lm.get("selected_prose_model"),
        "lm_studio": {
            "openai_reachable": bool(lm.get("openai_reachable")),
            "rest_reachable": bool(lm.get("rest_reachable")),
            "loaded_instance_count": loaded_model_count,
            "selected_prose_model": lm.get("selected_prose_model") or selected_model,
            "loaded_model": (gemma or {}).get("modelKey") or (gemma or {}).get("identifier") or speed.get("model"),
            "context_length": loaded_context,
            "parallel": loaded_parallel,
            "direct_minimal_visible_tps": direct_tps,
            "storydriver_visible_tps": speed.get("storydriver_visible_tps"),
            "hidden_reasoning_chars": hidden_reasoning,
            "runtime_metadata": lm.get("runtime_metadata") or {},
        },
        "comfyui": {
            "reachable": bool(resource_status.get("comfyui", {}).get("reachable")),
            "paused": images_paused,
            "image_generation_mode": current_image_mode,
            "queue_busy": queue_is_busy,
            "queue_error": queue_error,
            "last_idle_free": idle_free_status(),
            "adapter_dedicated_usage_bytes": speed.get("adapter_dedicated_usage_bytes"),
            "adapter_shared_usage_bytes": shared_bytes,
            "gpu_memory_snapshot_at": speed.get("gpu_memory_snapshot_at"),
            "gpu_memory_snapshot_stale": speed.get("gpu_memory_snapshot_stale"),
            "duplicate_comfyui_candidates": duplicate_candidates,
        },
        "background_jobs": background_jobs,
        "session_id": session_id,
        "scripts": {
            "verify": r"D:\StoryDriver\scripts\verify_gemma_fast_runtime.bat",
            "recover": r"D:\StoryDriver\scripts\recover_storydriver_speed.bat --confirm",
            "diagnose_recovery": r"D:\StoryDriver\scripts\recover_storydriver_speed.bat --diagnose-only",
            "hard_sleep": r"D:\StoryDriver\scripts\comfyui_hard_sleep.bat",
            "cleanup_duplicates": r"D:\StoryDriver\scripts\cleanup_duplicate_comfyui.bat --apply",
            "manual_comfyui_closed_test": r"D:\StoryDriver\scripts\manual_comfyui_closed_speed_test.bat --confirm-close-comfyui",
        },
    }


async def run_writing_speed_auto_cleanup(preflight: dict[str, Any], image_settings: Any) -> dict[str, Any]:
    if image_generation_is_paused(image_settings):
        return {"attempted": False, "skipped": True, "skip_reason": "image_generation_paused"}
    if preflight.get("protection_mode") != "auto":
        return {"attempted": False, "skipped": True, "skip_reason": "protection_not_auto"}
    comfyui = preflight.get("comfyui") or {}
    if not comfyui.get("reachable"):
        return {"attempted": False, "skipped": True, "skip_reason": "comfyui_unreachable"}
    if comfyui.get("queue_busy"):
        return {"attempted": False, "skipped": True, "skip_reason": "comfyui_queue_busy"}
    warning_text = " ".join(preflight.get("warnings") or []).lower()
    should_free = (
        "shared-memory" in warning_text
        or "direct lm studio speed" in warning_text
        or "comfyui" in warning_text
        or bool(getattr(image_settings, "free_comfyui_before_writing", False))
    )
    if not should_free:
        return {"attempted": False, "skipped": True, "skip_reason": "no_cleanup_needed"}
    if hasattr(image_settings, "model_dump"):
        settings_data = image_settings.model_dump()
    elif isinstance(image_settings, dict):
        settings_data = dict(image_settings)
    else:
        settings_data = dict(getattr(image_settings, "__dict__", {}))
    cleanup_settings = SimpleNamespace(**settings_data)
    cleanup_settings.free_comfyui_before_writing = True
    if getattr(cleanup_settings, "comfyui_idle_cleanup_mode", "after_image") in {"off", "manual"}:
        cleanup_settings.comfyui_idle_cleanup_mode = "after_image"
    return await maybe_free_comfyui_before_writing(cleanup_settings, reason="writing_speed_protection_auto")
