from __future__ import annotations

import copy
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import random
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status

from app.config import DATA_DIR
from app.database import db_session
from app.schemas import (
    GeneratedImageRead,
    ImageGenerateRequest,
    ImageJobStatus,
    ImagePromptGenerateRequest,
    ImagePromptGenerateResponse,
    ImageWorkflowImportRequest,
    SessionImageSettings,
    SessionImageSettingsUpdate,
    SceneImageSettings,
    SceneImageSettingsUpdate,
    ImageWorkflowConfig,
    ImageWorkflowListResponse,
    ImageWorkflowValidationResponse,
    SceneImagePromptRead,
)
from app.services.comfyui_client import (
    ComfyUIClient,
    ComfyUIError,
    ComfyUINoOutputError,
    ComfyUIOfflineError,
    ComfyUITimeoutError,
)
from app.services.generation_locks import GenerationConflictError, image_generation_job
from app.services.image_workflows import (
    WORKFLOW_DIR,
    WORKFLOW_TYPE_LABELS,
    effective_workflow_type,
    expected_time_range,
    extract_workflow_metadata,
    get_workflow,
    import_workflow_json,
    inject_workflow_values,
    load_workflow,
    save_workflow_config,
    scan_workflows,
    validate_workflow_config,
)
from app.services.character_references import references_for_generation
from app.services.comfyui_idle_cleanup import schedule_comfyui_idle_free
from app.services.image_regenerate_window import (
    begin_image_regenerate_window,
    cancel_image_regenerate_window,
    finish_image_regenerate_window,
    image_regenerate_window_status,
)
from app.services.scene_image_prompts import (
    load_cached_scene_image_prompt,
    load_scene_image_prompt_settings,
    prepare_scene_image_prompt,
    resolve_prompt_workflow,
    save_scene_image_prompt_settings,
)
from app.services.lmstudio_resource_client import LMStudioResourceClient, LMStudioResourceError
from app.generation.router import resolve_task_model_settings
from app.settings.store import image_generation_is_paused, image_generation_mode, load_image_settings


router = APIRouter(tags=["images"])

CONTENT_TYPE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}

LAST_IMAGE_EVENT: dict = {
    "workflow_id": None,
    "workflow_name": None,
    "image_path": None,
    "error": None,
    "resource_mode": "balanced",
    "resource_warnings": [],
    "resource_actions": {},
}
LAST_COMFYUI_FREE_EVENT: dict = {
    "attempted": False,
    "success": False,
    "time": None,
    "error": None,
    "result": None,
}
IMAGE_JOB_STATUS: dict = {
    "active": False,
    "stage": "ready",
    "message": "Ready",
    "percent": None,
    "prompt_id": None,
    "session_id": None,
    "scene_id": None,
    "version_id": None,
    "image_id": None,
    "resource_mode": "balanced",
    "updated_at": None,
    "warnings": [],
    "actions": {},
    "error": None,
    "elapsed_seconds": None,
    "expected_time_seconds_min": None,
    "expected_time_seconds_max": None,
    "slow_warning": None,
}

RESOURCE_MODES_THAT_BLOCK_STORY = {"image_priority", "experimental_auto_swap"}
MANUAL_RESOURCE_GUIDANCE = "For best Z-Image Turbo speed, unload/close the LM Studio model first, then generate image."
PERFORMANCE_LOG_DIR = DATA_DIR / "logs" / "image_jobs"
PERFORMANCE_REPORT_PATH = DATA_DIR / "logs" / "IMAGE_PERFORMANCE_REPORT.md"
TUNING_REPORT_PATH = DATA_DIR / "logs" / "COMFYUI_PERFORMANCE_TUNING_REPORT.md"
REGENERATE_WINDOW_REPORT_PATH = DATA_DIR / "logs" / "IMAGE_REGENERATE_WINDOW_REPORT.md"
LMS_EXE = Path.home() / ".lmstudio" / "bin" / "lms.exe"


def mode_label(mode: str) -> str:
    labels = {
        "balanced": "Balanced",
        "manual": "Manual",
        "image_priority": "Auto Image Priority",
        "experimental_auto_swap": "Advanced Auto-Orchestrate",
    }
    return labels.get(mode, mode.replace("_", " ").title())


def effective_resource_mode_for_workflow(
    requested_mode: str,
    workflow_config: ImageWorkflowConfig,
    workflow_type: str,
) -> tuple[str, list[str], dict[str, Any]]:
    workflow_preference = (workflow_config.resource_mode or "auto").strip()
    details: dict[str, Any] = {
        "requested_resource_mode": requested_mode,
        "workflow_resource_mode": workflow_preference,
        "recommended_resource_mode_applied": False,
    }
    warnings: list[str] = []
    if workflow_preference != "auto" and requested_mode == workflow_preference:
        return workflow_preference, warnings, details
    if workflow_preference != "auto":
        details["workflow_resource_mode_recommendation"] = workflow_preference
        details["workflow_resource_mode_overridden_by_user"] = True
    if workflow_type in {"z_image_turbo", "lonecat_zit", "flux_klein", "lonecat_flux_klein"} and requested_mode == "balanced":
        warnings.append(
            "Balanced mode is available, but this image workflow can be much slower while the large LM Studio model is loaded. Auto Image Priority is recommended on this PC."
        )
    return requested_mode, warnings, details


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def elapsed_since(started_monotonic: float | None) -> float | None:
    if started_monotonic is None:
        return None
    return round(max(0.0, perf_counter() - started_monotonic), 1)


def slow_generation_warning(
    elapsed_seconds: float | None,
    expected_min: int | None,
    expected_max: int | None,
    workflow_type_label: str | None = None,
) -> str | None:
    if elapsed_seconds is None or expected_max is None:
        return None
    threshold = max(expected_max * 1.5, expected_max + 15)
    if elapsed_seconds <= threshold:
        return None
    expected = f"{expected_min}-{expected_max}s" if expected_min else f"under {expected_max}s"
    label = workflow_type_label or "this workflow"
    return (
        f"Generation took {elapsed_seconds:.0f}s, expected {expected} for {label}. "
        "Check LM unload, ComfyUI backend, workflow model, or VRAM/shared memory."
    )


class ImagePerformanceTimer:
    def __init__(
        self,
        *,
        workflow_id: str,
        workflow_name: str,
        workflow_type: str,
        workflow_type_label: str,
        expected_min: int | None,
        expected_max: int | None,
        resource_mode: str,
    ) -> None:
        self.job_id = uuid4().hex
        self.started = perf_counter()
        self.started_at = utc_now()
        self.workflow_id = workflow_id
        self.workflow_name = workflow_name
        self.workflow_type = workflow_type
        self.workflow_type_label = workflow_type_label
        self.expected_min = expected_min
        self.expected_max = expected_max
        self.resource_mode = resource_mode
        self.events: list[dict[str, Any]] = []
        self.timings: dict[str, float] = {}
        self.comfyui_stats: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.error: str | None = None
        self.image_id: str | None = None
        self.event("image_generation_request_received")

    def event(self, name: str, **data: Any) -> None:
        payload: dict[str, Any] = {
            "name": name,
            "at": utc_now(),
            "elapsed_seconds": round(perf_counter() - self.started, 3),
        }
        if data:
            payload.update(data)
        self.events.append(payload)

    def start(self, label: str, **data: Any) -> float:
        self.event(f"{label}_start", **data)
        return perf_counter()

    def end(self, label: str, started: float, **data: Any) -> float:
        duration = round(perf_counter() - started, 3)
        self.timings[label] = duration
        self.event(f"{label}_end", duration_seconds=duration, **data)
        return duration

    def set_stats(self, label: str, stats: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.comfyui_stats[label] = {"ok": error is None, "data": stats, "error": error, "at": utc_now()}

    def total_seconds(self) -> float:
        return round(perf_counter() - self.started, 3)

    def as_dict(self, *, resource_actions: dict[str, Any] | None = None) -> dict[str, Any]:
        total = self.total_seconds()
        warning = slow_generation_warning(total, self.expected_min, self.expected_max, self.workflow_type_label)
        warnings = list(self.warnings)
        if warning and warning not in warnings:
            warnings.append(warning)
        return {
            "job_id": self.job_id,
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "total_seconds": total,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "workflow_type": self.workflow_type,
            "workflow_type_label": self.workflow_type_label,
            "expected_time_seconds_min": self.expected_min,
            "expected_time_seconds_max": self.expected_max,
            "resource_mode": self.resource_mode,
            "image_id": self.image_id,
            "timings": self.timings,
            "events": self.events,
            "comfyui_stats": self.comfyui_stats,
            "resource_actions": resource_actions or {},
            "warnings": warnings,
            "error": self.error,
        }


def write_image_performance_log(timer: ImagePerformanceTimer, *, resource_actions: dict[str, Any]) -> str:
    PERFORMANCE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.joinpath("logs").mkdir(parents=True, exist_ok=True)
    data = timer.as_dict(resource_actions=resource_actions)
    log_path = PERFORMANCE_LOG_DIR / f"{timer.started_at.replace(':', '').replace('-', '')}_{timer.job_id}.json"
    log_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    warnings = data.get("warnings") or []
    timings = data.get("timings") or {}
    actions = data.get("resource_actions") or {}
    model_names = actions.get("workflow_model_names") or []
    diffusion_names = actions.get("workflow_diffusion_model_names") or []
    upscale_names = actions.get("workflow_upscale_model_names") or []
    auxiliary_names = actions.get("workflow_auxiliary_model_names") or []
    remaining_lm_instances = actions.get("loaded_instances_remaining_before_comfyui") or actions.get("loaded_instances_after_unload") or []
    planned_unload_instances = actions.get("planned_unload_instances") or []
    unload_results = actions.get("unload_results") or []
    reload_results = actions.get("reload_results") or []
    size_values = []
    for item in actions.get("workflow_sizes") or []:
        if isinstance(item, dict) and item.get("width") and item.get("height"):
            size_values.append(f"{item.get('width')}x{item.get('height')}")
    bottleneck = "unknown"
    if timings:
        bottleneck_label, bottleneck_seconds = max(timings.items(), key=lambda item: item[1] or 0)
        bottleneck = f"{bottleneck_label} ({bottleneck_seconds}s)"

    def bytes_label(value: Any) -> str:
        if not isinstance(value, (int, float)):
            return "unknown"
        amount = float(value)
        for suffix in ("B", "KB", "MB", "GB"):
            if amount < 1024 or suffix == "GB":
                return f"{amount:.1f} {suffix}" if suffix != "B" else f"{int(amount)} B"
            amount /= 1024
        return "unknown"

    def first_device(stats: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(stats, dict):
            return None
        devices = stats.get("devices")
        if isinstance(devices, list) and devices and isinstance(devices[0], dict):
            return devices[0]
        return None

    def stats_line(label: str, stats: dict[str, Any] | None) -> str:
        device = first_device(stats)
        if not device:
            return f"- {label}: unavailable"
        return (
            f"- {label}: VRAM free {bytes_label(device.get('vram_free'))} / {bytes_label(device.get('vram_total'))}; "
            f"torch free {bytes_label(device.get('torch_vram_free'))} / {bytes_label(device.get('torch_vram_total'))}"
        )

    def unload_results_label() -> str:
        if not unload_results:
            return "not recorded"
        parts = []
        for item in unload_results:
            name = item.get("display_name") or item.get("model_key") or item.get("id") or "model"
            status_text = "ok" if item.get("success") else "failed"
            parts.append(f"{name}: {status_text} ({item.get('duration_seconds')}s)")
        return ", ".join(parts)

    def reload_results_label() -> str:
        if not reload_results:
            return "not recorded"
        parts = []
        for item in reload_results:
            status_text = "ok" if item.get("success") else "failed"
            parts.append(f"{item.get('model_key') or 'model'}: {status_text} ({item.get('duration_seconds')}s)")
        return ", ".join(parts)

    lines = [
        "# StoryDriver Image Performance Report",
        "",
        f"Updated: {utc_now()}",
        "",
        "## Latest Image Job",
        "",
        f"- Workflow: {data.get('workflow_name')} (`{data.get('workflow_id')}`)",
        f"- Workflow type: {data.get('workflow_type_label')} (`{data.get('workflow_type')}`)",
        f"- Expected range: {data.get('expected_time_seconds_min') or 'unknown'}-{data.get('expected_time_seconds_max') or 'unknown'} seconds",
        f"- Resource mode: {data.get('resource_mode')}",
        f"- Image ID: {data.get('image_id') or 'none'}",
        f"- Total time: {data.get('total_seconds')} seconds",
        f"- Detailed JSON log: `{log_path}`",
        "",
        "## Detected Workflow Metadata",
        "",
        f"- Workflow complexity: {actions.get('workflow_complexity') or 'unknown'}",
        f"- Workflow speed note: {actions.get('workflow_speed_note') or 'none'}",
        f"- Model names: {', '.join(model_names) if model_names else 'unknown'}",
        f"- Diffusion models: {', '.join(diffusion_names) if diffusion_names else 'unknown'}",
        f"- Upscale models: {', '.join(upscale_names) if upscale_names else 'none detected'}",
        f"- Auxiliary models: {', '.join(auxiliary_names) if auxiliary_names else 'none detected'}",
        f"- Text encoders: {', '.join(actions.get('workflow_text_encoder_names') or []) or 'unknown'}",
        f"- VAE names: {', '.join(actions.get('workflow_vae_names') or []) or 'unknown'}",
        f"- Steps: {', '.join(str(value) for value in actions.get('workflow_steps') or []) or 'unknown'}",
        f"- Sizes: {', '.join(size_values) or 'unknown'}",
        f"- Output node: {actions.get('workflow_output_node_id') or 'auto'}",
        f"- ComfyUI kept alive between jobs: {actions.get('comfyui_backend_kept_alive_between_jobs', True)}",
        "",
        "## Timings",
        "",
    ]
    if timings:
        for label, seconds in timings.items():
            lines.append(f"- {label}: {seconds}s")
    else:
        lines.append("- No stage timings were recorded.")
    reload_targets_label = ", ".join(actions.get("reload_targets") or []) or "none"
    if actions.get("lm_reload_delayed_for_regenerate"):
        reload_status_label = "deferred for fast regenerate window"
    elif actions.get("reload_attempted"):
        reload_status_label = f"{actions.get('reload_duration_seconds', 'not recorded')}s"
    else:
        reload_status_label = "not attempted"
    if actions.get("comfyui_free_delayed_for_regenerate"):
        comfy_free_status_label = "deferred for fast regenerate window"
        comfy_after_free_label = "kept warm"
    elif actions.get("comfyui_free_attempted"):
        comfy_free_status_label = str(actions.get("comfyui_free_succeeded", False))
        comfy_after_free_label = str(actions.get("comfyui_alive_after_free", False))
    else:
        comfy_free_status_label = "not attempted"
        comfy_after_free_label = "not checked"

    lines.extend(
        [
            "",
            "## Resource Checks",
            "",
            f"- LM unload policy: {actions.get('lm_unload_policy') or 'unknown'}",
            f"- LM reload policy: {actions.get('lm_reload_policy') or 'unknown'}",
            f"- LM reload fast profile: {json.dumps(actions.get('lm_reload_fast_profile') or {}, ensure_ascii=False)}",
            f"- Prose reload target: {actions.get('reload_target_model') or actions.get('selected_prose_model') or 'unknown'}",
            f"- Models loaded before image job: {actions.get('loaded_model_count_before', len(actions.get('loaded_instances_before_unload') or []))}",
            f"- Planned unload count: {actions.get('planned_unload_count', len(planned_unload_instances))}",
            f"- Models remaining before ComfyUI: {len(remaining_lm_instances)}",
            f"- LM unload attempted: {actions.get('unload_attempted', False)}",
            f"- LM unload confirmed: {actions.get('unload_confirmed', False)}",
            f"- LM unload API time: {actions.get('unload_duration_seconds', 'not recorded')}s",
            f"- LM unload wait time: {actions.get('unload_wait_seconds', 'not recorded')}s",
            f"- LM unload per model: {unload_results_label()}",
            f"- LM reload targets: {reload_targets_label}",
            f"- LM reload attempted: {actions.get('reload_attempted', False)}",
            f"- LM reload succeeded: {actions.get('reload_succeeded', False)}",
            f"- LM reload status: {reload_status_label}",
            f"- LM reload per model: {reload_results_label()}",
            f"- ComfyUI free attempted: {actions.get('comfyui_free_attempted', False)}",
            f"- ComfyUI free status: {comfy_free_status_label}",
            f"- ComfyUI free time: {actions.get('comfyui_free_duration_seconds', 'not recorded')}s",
            f"- ComfyUI status after image: {comfy_after_free_label}",
            "",
            "## ComfyUI Memory Snapshots",
            "",
            stats_line("Before generation", actions.get("comfyui_stats_before_generation")),
            stats_line("After generation", actions.get("comfyui_stats_after_generation")),
            stats_line("After /free", actions.get("comfyui_stats_after_free")),
            "",
            "## Likely Bottleneck",
            "",
            f"- {bottleneck}",
            "",
            "## Warnings",
            "",
        ]
    )
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- None.")
    if data.get("error"):
        lines.extend(["", "## Error", "", f"- {data['error']}"])
    report_text = "\n".join(lines) + "\n"
    PERFORMANCE_REPORT_PATH.write_text(report_text, encoding="utf-8")
    TUNING_REPORT_PATH.write_text(
        report_text.replace("# StoryDriver Image Performance Report", "# ComfyUI Performance Tuning Report", 1),
        encoding="utf-8",
    )
    return str(log_path)


def finalize_image_performance(
    timer: ImagePerformanceTimer | None,
    *,
    resource_actions: dict[str, Any],
    resource_warnings: list[str],
    image_id: str | None = None,
    error: str | None = None,
) -> None:
    if not timer:
        return
    if image_id:
        timer.image_id = image_id
    if error:
        timer.error = error
    for warning in resource_warnings:
        if warning not in timer.warnings:
            timer.warnings.append(warning)
    warning = slow_generation_warning(timer.total_seconds(), timer.expected_min, timer.expected_max, timer.workflow_type_label)
    if warning and warning not in resource_warnings:
        resource_warnings.append(warning)
    if warning and warning not in timer.warnings:
        timer.warnings.append(warning)
    log_path = write_image_performance_log(timer, resource_actions=resource_actions)
    data = timer.as_dict(resource_actions=resource_actions)
    resource_actions["performance_log"] = log_path
    resource_actions["performance_total_seconds"] = data["total_seconds"]
    resource_actions["performance_timings"] = data["timings"]
    resource_actions["performance_warnings"] = data["warnings"]


def update_image_job_status(**patch) -> None:
    IMAGE_JOB_STATUS.update(patch)
    manual_elapsed_supplied = "elapsed_seconds" in patch
    manual_slow_warning_supplied = "slow_warning" in patch
    started = IMAGE_JOB_STATUS.get("started_monotonic")
    elapsed = elapsed_since(started if isinstance(started, (int, float)) else None)
    if elapsed is not None and not manual_elapsed_supplied:
        IMAGE_JOB_STATUS["elapsed_seconds"] = elapsed
        IMAGE_JOB_STATUS["slow_warning"] = slow_generation_warning(
            elapsed,
            IMAGE_JOB_STATUS.get("expected_time_seconds_min"),
            IMAGE_JOB_STATUS.get("expected_time_seconds_max"),
            IMAGE_JOB_STATUS.get("workflow_type_label"),
        )
    elif manual_elapsed_supplied and not manual_slow_warning_supplied:
        IMAGE_JOB_STATUS["slow_warning"] = slow_generation_warning(
            IMAGE_JOB_STATUS.get("elapsed_seconds"),
            IMAGE_JOB_STATUS.get("expected_time_seconds_min"),
            IMAGE_JOB_STATUS.get("expected_time_seconds_max"),
            IMAGE_JOB_STATUS.get("workflow_type_label"),
        )
    if patch.get("active") is False and patch.get("stage") in {"completed", "failed", "ready"}:
        IMAGE_JOB_STATUS["started_monotonic"] = None
    IMAGE_JOB_STATUS["updated_at"] = utc_now()


def image_job_status() -> dict:
    status = dict(IMAGE_JOB_STATUS)
    status.pop("started_monotonic", None)
    status.pop("workflow_type_label", None)
    status["fast_regenerate_window"] = image_regenerate_window_status()
    return status


def update_last_image_event(
    *,
    workflow_id: str,
    workflow_name: str,
    image_path: str | None,
    error: str | None,
    resource_mode: str,
    resource_warnings: list[str],
    resource_actions: dict,
) -> None:
    LAST_IMAGE_EVENT.update(
        {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "image_path": image_path,
            "error": error,
            "resource_mode": resource_mode,
            "resource_warnings": resource_warnings,
            "resource_actions": resource_actions,
        }
    )


def append_regenerate_window_report(title: str, details: dict[str, Any]) -> None:
    REGENERATE_WINDOW_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not REGENERATE_WINDOW_REPORT_PATH.exists():
        REGENERATE_WINDOW_REPORT_PATH.write_text(
            "# Image Regenerate Window Report\n\n"
            "StoryDriver keeps ComfyUI warm briefly after an image so Regenerate Image can reuse the loaded workflow, "
            "then frees ComfyUI memory and reloads LM Studio when the window expires or writing resumes.\n",
            encoding="utf-8",
        )
    safe_details = json.dumps(details, indent=2, sort_keys=True, default=str)
    with REGENERATE_WINDOW_REPORT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## {title} - {utc_now()}\n\n```json\n{safe_details}\n```\n")


def _regenerate_window_seconds(image_settings: Any) -> int:
    configured = getattr(image_settings, "regenerate_warm_window_seconds", None)
    if configured is None:
        configured = getattr(image_settings, "comfyui_regenerate_grace_seconds", 90)
    try:
        return max(0, int(configured or 0))
    except (TypeError, ValueError):
        return 90


def _fast_regenerate_window_enabled(image_settings: Any, resource_mode: str) -> bool:
    if resource_mode not in RESOURCE_MODES_THAT_BLOCK_STORY:
        return False
    if not bool(getattr(image_settings, "fast_regenerate_window_enabled", False)):
        return False
    return _regenerate_window_seconds(image_settings) > 0


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_model_text(value: str | None) -> str:
    return (value or "").strip().lower().replace("\\", "/")


def _instance_id(instance: dict[str, Any]) -> str | None:
    return _first_text(instance.get("id"), instance.get("instance_id"), instance.get("instanceId"))


def _instance_model_key(instance: dict[str, Any]) -> str | None:
    return _first_text(instance.get("model_key"), instance.get("modelKey"), instance.get("key"), instance.get("model"))


def _instance_display_name(instance: dict[str, Any]) -> str | None:
    return _first_text(instance.get("display_name"), instance.get("displayName"), instance.get("name"), _instance_model_key(instance))


def _instance_label(instance: dict[str, Any]) -> str:
    label = _instance_display_name(instance) or _instance_model_key(instance) or "model"
    instance_id = _instance_id(instance)
    return f"{label} ({instance_id})" if instance_id else label


def _instance_matches_model(instance: dict[str, Any], selected_model: str | None) -> bool:
    selected = _normalize_model_text(selected_model)
    if not selected:
        return False
    candidates = {
        _normalize_model_text(_instance_id(instance)),
        _normalize_model_text(_instance_model_key(instance)),
        _normalize_model_text(_instance_display_name(instance)),
    }
    candidates = {candidate for candidate in candidates if candidate}
    candidates.update(candidate.rsplit("/", 1)[-1] for candidate in list(candidates))
    return selected in candidates


def _public_instance(instance: dict[str, Any]) -> dict[str, str | None]:
    return {
        "id": _instance_id(instance),
        "model_key": _instance_model_key(instance),
        "display_name": _instance_display_name(instance),
    }


def _unique_instances(instances: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    unique: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for instance in instances:
        public = _public_instance(instance)
        instance_id = public.get("id")
        if not instance_id or instance_id in seen:
            continue
        unique.append(public)
        seen.add(instance_id)
    return unique


def _find_instance_for_model(instances: list[dict[str, Any]], selected_model: str | None) -> dict[str, str | None] | None:
    if not selected_model:
        return _public_instance(instances[0]) if len(instances) == 1 else None
    for instance in instances:
        if _instance_matches_model(instance, selected_model):
            return _public_instance(instance)
    return None


def _unique_model_keys(instances: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for instance in instances:
        key = _instance_model_key(instance)
        if not key:
            continue
        normalized = _normalize_model_text(key)
        if normalized in seen:
            continue
        keys.append(key)
        seen.add(normalized)
    return keys


def _reload_targets_for_policy(actions: dict[str, Any], policy: str) -> list[str]:
    if policy in {"none", "preserve_manual"}:
        return []
    if policy == "all_previous":
        return _unique_model_keys(actions.get("loaded_instances_before_unload") or [])
    if policy == "previous_selected":
        target = actions.get("selected_loaded_model_key") or actions.get("unloaded_model_key") or actions.get("selected_model")
        return [str(target)] if target else []
    target = actions.get("reload_target_model") or actions.get("selected_prose_model") or actions.get("selected_model")
    return [str(target)] if target else []


def _lm_reload_fast_profile(image_settings: Any | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if profile:
        return dict(profile)
    image_settings = image_settings or load_image_settings()
    parallel = getattr(image_settings, "lm_reload_parallel", 1) or 1
    context_length = getattr(image_settings, "lm_reload_context_length", 50749)
    try:
        parallel = max(1, min(16, int(parallel)))
    except (TypeError, ValueError):
        parallel = 1
    try:
        context_length = int(context_length) if context_length else None
    except (TypeError, ValueError):
        context_length = None
    return {
        "enabled": bool(getattr(image_settings, "lm_reload_use_fast_profile", True)),
        "prefer_cli": bool(getattr(image_settings, "lm_reload_prefer_cli", True)),
        "parallel": parallel,
        "context_length": context_length,
        "gpu_offload": getattr(image_settings, "lm_reload_gpu_offload", "max") or "max",
    }


def _lms_load_command(model_key: str, profile: dict[str, Any]) -> list[str]:
    args = ["load", model_key]
    gpu = str(profile.get("gpu_offload") or "max").strip()
    if gpu and gpu != "default":
        args.extend(["--gpu", gpu])
    context_length = profile.get("context_length")
    if context_length:
        args.extend(["--context-length", str(context_length)])
    parallel = profile.get("parallel") or 1
    args.extend(["--parallel", str(parallel), "--identifier", model_key, "-y"])
    return args


async def _run_lms_cli(args: list[str], *, timeout_seconds: float = 900.0) -> dict[str, Any]:
    if not LMS_EXE.exists():
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"lms.exe not found at {LMS_EXE}",
            "command": [str(LMS_EXE), *args],
        }
    process = await asyncio.create_subprocess_exec(
        str(LMS_EXE),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path(__file__).resolve().parents[3]),
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"lms command timed out after {timeout_seconds:.0f}s",
            "command": [str(LMS_EXE), *args],
        }
    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "command": [str(LMS_EXE), *args],
    }


async def _reload_lmstudio_model(
    client: LMStudioResourceClient,
    model_key: str,
    *,
    image_settings: Any | None,
    reload_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = _lm_reload_fast_profile(image_settings, reload_profile)
    if profile.get("enabled") and profile.get("prefer_cli"):
        command_args = _lms_load_command(model_key, profile)
        cli_result = await _run_lms_cli(command_args)
        return {
            "method": "lms_cli_fast_profile",
            "profile": profile,
            "success": bool(cli_result.get("ok")),
            "response": cli_result,
            "warning": None
            if cli_result.get("ok")
            else "Fast LM reload profile could not run through lms CLI. StoryDriver did not fall back to REST reload because that can reload with slow parallel defaults.",
        }
    response = await client.load_lmstudio_model(str(model_key))
    return {
        "method": "lmstudio_rest_default",
        "profile": profile,
        "success": True,
        "response": response,
        "warning": "Reload used LM Studio REST default load. If speed regresses, use CLI fast profile or Preserve manual LM Studio load.",
    }


def _planned_resource_action(mode: str, unload_count: int, reload_target: str | None, reload_policy: str) -> str:
    if mode == "manual":
        return "Manual mode: StoryDriver will not unload or reload LM Studio automatically."
    if mode == "balanced":
        return "Balanced mode: StoryDriver will keep LM Studio loaded and reuse ComfyUI."
    reload_text = "not reload LM Studio"
    if reload_policy == "preserve_manual":
        reload_text = "preserve the manual LM Studio load; StoryDriver will not auto-reload"
    elif reload_policy == "all_previous":
        reload_text = "reload all previously loaded LM Studio models"
    elif reload_policy == "previous_selected":
        reload_text = "reload the previously selected LM Studio model"
    elif reload_policy == "prose":
        reload_text = f"reload {reload_target}" if reload_target else "reload the prose model"
    if unload_count:
        return f"Will unload {unload_count} LM Studio model(s), run ComfyUI, free ComfyUI memory, then {reload_text}."
    return f"Will run ComfyUI with no LM Studio unload needed, free ComfyUI memory, then {reload_text} if needed."


async def apply_pre_image_resource_mode(mode: str, *, image_settings: Any | None = None) -> tuple[list[str], dict]:
    warnings: list[str] = []
    image_settings = image_settings or load_image_settings()
    _, prose_model = resolve_task_model_settings("prose_generation")
    selected_model = prose_model.model.strip() or None
    unload_policy = getattr(image_settings, "lm_unload_policy", "all") or "all"
    reload_policy = getattr(image_settings, "lm_reload_policy", "prose") or "prose"
    fast_window = _fast_regenerate_window_enabled(image_settings, mode)
    window_seconds = _regenerate_window_seconds(image_settings) if fast_window else 0
    reload_fast_profile = _lm_reload_fast_profile(image_settings)
    actions: dict = {
        "mode": mode,
        "mode_label": mode_label(mode),
        "selected_model": selected_model,
        "selected_prose_model": selected_model,
        "reload_target_model": selected_model,
        "lm_unload_policy": unload_policy,
        "lm_reload_policy": reload_policy,
        "lm_reload_fast_profile": reload_fast_profile,
        "orchestration_plan": _planned_resource_action(mode, 0, selected_model, reload_policy),
        "lmstudio_rest_reachable": False,
        "loaded_instances": [],
        "loaded_instances_before_unload": [],
        "loaded_instances_after_unload": [],
        "loaded_model_count_before": 0,
        "loaded_model_count_after_unload": 0,
        "planned_unload_instances": [],
        "planned_unload_count": 0,
        "unload_results": [],
        "unload_attempted": False,
        "unload_succeeded": False,
        "unload_confirmed": False,
        "unload_wait_seconds": None,
        "unloaded_instance_id": None,
        "unloaded_instance_ids": [],
        "reload_attempted": False,
        "reload_succeeded": False,
        "fast_regenerate_window_enabled": fast_window,
        "regenerate_warm_window_seconds": window_seconds,
        "comfyui_kept_warm_for_regenerate": False,
    }

    if mode == "manual":
        warnings.append(MANUAL_RESOURCE_GUIDANCE)

    client = LMStudioResourceClient()
    try:
        loaded_instances = _unique_instances(await client.get_loaded_lmstudio_instances())
        selected_instance = _find_instance_for_model(loaded_instances, selected_model)
        actions["lmstudio_rest_reachable"] = True
        actions["loaded_instances"] = loaded_instances
        actions["loaded_instances_before_unload"] = loaded_instances
        actions["loaded_model_count_before"] = len(loaded_instances)
        actions["selected_loaded_instance"] = selected_instance
        actions["selected_loaded_model_key"] = selected_instance.get("model_key") if selected_instance else None
    except LMStudioResourceError as error:
        actions["lmstudio_rest_error"] = str(error)
        if mode in RESOURCE_MODES_THAT_BLOCK_STORY:
            warnings.append(f"LM Studio REST API unavailable. Auto resource management skipped. {MANUAL_RESOURCE_GUIDANCE}")
        return warnings, actions

    if len(loaded_instances) > 1:
        warnings.append("Multiple LM Studio models loaded. Image generation may be slow unless Auto Image Priority unloads them first.")
    elif mode == "balanced" and loaded_instances:
        warnings.append("LM Studio has a model loaded. Z-Image Turbo may run slower.")

    if mode not in RESOURCE_MODES_THAT_BLOCK_STORY:
        return warnings, actions

    if unload_policy == "manual":
        warnings.append(f"LM unload policy is Manual. {MANUAL_RESOURCE_GUIDANCE}")
        actions["orchestration_plan"] = _planned_resource_action("manual", 0, selected_model, reload_policy)
        return warnings, actions

    if not loaded_instances:
        warnings.append("No LM Studio loaded instance was found. Nothing to unload before ComfyUI.")
        return warnings, actions

    if unload_policy == "all":
        target_instances = loaded_instances
    else:
        target_instances = [selected_instance] if selected_instance else []
        if not target_instances:
            if selected_model:
                warnings.append("Selected prose model has no loaded instance. LM Studio unload skipped.")
            else:
                warnings.append("LM Studio has loaded instances, but no prose model could be matched safely. Use Unload all loaded LM Studio models or Manual mode.")
            return warnings, actions
        if len(loaded_instances) > len(target_instances):
            warnings.append("Selected-only unload will leave other LM Studio models loaded before ComfyUI. Image generation may remain slow.")

    target_instances = [instance for instance in target_instances if instance and instance.get("id")]
    if not target_instances:
        warnings.append("No unloadable LM Studio instance IDs were reported.")
        return warnings, actions

    actions["planned_unload_instances"] = target_instances
    actions["planned_unload_count"] = len(target_instances)
    actions["orchestration_plan"] = _planned_resource_action(mode, len(target_instances), selected_model, reload_policy)
    if fast_window:
        reload_text = "not reload LM Studio"
        if reload_policy == "preserve_manual":
            reload_text = "preserve the manual LM Studio load"
        elif reload_policy == "all_previous":
            reload_text = "reload all previously loaded models"
        elif reload_policy == "previous_selected":
            reload_text = "reload the previously selected model"
        elif reload_policy == "prose":
            reload_text = f"reload {selected_model}" if selected_model else "reload the prose model"
        actions["orchestration_plan"] = (
            f"Will unload {len(target_instances)} LM Studio model(s), run ComfyUI, keep ComfyUI warm for "
            f"{window_seconds}s for Regenerate Image, then free ComfyUI memory and {reload_text} if idle."
        )
    actions["unload_attempted"] = True
    actions["unloaded_instance_id"] = target_instances[0].get("id")
    actions["unloaded_instance_ids"] = [str(instance.get("id")) for instance in target_instances if instance.get("id")]
    actions["unloaded_model_key"] = target_instances[0].get("model_key")
    update_image_job_status(
        stage="unloading_lm",
        message=f"Unloading {len(target_instances)} LM Studio model(s)...",
        percent=18,
        actions=actions,
    )
    try:
        unload_started = perf_counter()
        unload_results: list[dict[str, Any]] = []
        for instance in target_instances:
            instance_id = str(instance.get("id"))
            result: dict[str, Any] = {
                "id": instance_id,
                "model_key": instance.get("model_key"),
                "display_name": instance.get("display_name"),
                "success": False,
                "duration_seconds": None,
                "error": None,
            }
            span = perf_counter()
            try:
                result["response"] = await client.unload_lmstudio_instance(instance_id)
                result["success"] = True
            except LMStudioResourceError as unload_error:
                result["error"] = str(unload_error)
            result["duration_seconds"] = round(perf_counter() - span, 3)
            unload_results.append(result)
        actions["unload_results"] = unload_results
        actions["unload_duration_seconds"] = round(perf_counter() - unload_started, 3)
        actions["unload_succeeded"] = all(result.get("success") for result in unload_results)
        wait_started = perf_counter()
        last_instances: list[dict[str, Any]] = loaded_instances
        target_ids = {str(instance.get("id")) for instance in target_instances if instance.get("id")}
        update_image_job_status(stage="unloading_lm", message="Waiting for LM Studio to release models...", percent=22, actions=actions)
        while perf_counter() - wait_started < 45:
            await asyncio.sleep(1.0)
            try:
                last_instances = _unique_instances(await client.get_loaded_lmstudio_instances())
            except LMStudioResourceError as verify_error:
                actions["unload_verify_error"] = str(verify_error)
                break
            remaining_target_ids = {str(item.get("id")) for item in last_instances if item.get("id")} & target_ids
            if not remaining_target_ids:
                actions["unload_confirmed"] = True
                break
        actions["loaded_instances_after_unload"] = last_instances
        actions["loaded_model_count_after_unload"] = len(last_instances)
        actions["loaded_instances_remaining_before_comfyui"] = last_instances
        actions["unload_wait_seconds"] = round(perf_counter() - wait_started, 3)
        if not actions["unload_confirmed"]:
            warnings.append("LM Studio unload was requested, but not every target instance was confirmed gone before ComfyUI generation.")
        if last_instances:
            remaining = ", ".join(_instance_label(instance) for instance in last_instances[:4])
            warnings.append(f"LM Studio still reports loaded model(s) before ComfyUI: {remaining}. Image generation may be slow.")
        if not actions["unload_succeeded"]:
            failed = [result for result in unload_results if not result.get("success")]
            details = "; ".join(result.get("error") or result.get("id") or "unknown failure" for result in failed[:3])
            warnings.append(f"One or more LM Studio unload requests failed. {details or MANUAL_RESOURCE_GUIDANCE}")
    except LMStudioResourceError as error:
        actions["unload_error"] = str(error)
        warnings.append(f"LM Studio unload failed. {MANUAL_RESOURCE_GUIDANCE}")
    return warnings, actions


async def apply_post_image_resource_mode(
    mode: str,
    warnings: list[str],
    actions: dict,
    *,
    auto_reload: bool,
    reload_policy: str,
    force_reload: bool = False,
    image_settings: Any | None = None,
    reload_profile: dict[str, Any] | None = None,
) -> None:
    if mode not in RESOURCE_MODES_THAT_BLOCK_STORY:
        return
    if not auto_reload:
        actions["reload_skipped"] = "Auto reload is disabled in Image Settings."
        return
    if not actions.get("unload_attempted") and not force_reload:
        return

    actions["lm_reload_policy"] = reload_policy
    profile = _lm_reload_fast_profile(image_settings, reload_profile or actions.get("lm_reload_fast_profile"))
    actions["lm_reload_fast_profile"] = profile
    reload_targets = _reload_targets_for_policy(actions, reload_policy)
    actions["reload_targets"] = reload_targets
    if not reload_targets:
        if reload_policy == "preserve_manual":
            actions["reload_skipped"] = "Preserve manual LM Studio load is enabled; StoryDriver will not auto-reload the model."
            warnings.append(
                "LM Studio was not auto-reloaded. Reload the prose model manually in LM Studio if you need to preserve a hand-tuned GPU/offload load."
            )
        else:
            actions["reload_skipped"] = "Reload policy has no target model."
        if reload_policy not in {"none", "preserve_manual"}:
            warnings.append("LM Studio reload skipped because no reload target model key was available.")
        return

    actions["reload_attempted"] = True
    update_image_job_status(stage="reloading_lm", message="Reloading LM Studio...", percent=95, actions=actions)
    client = LMStudioResourceClient()
    try:
        reload_started = perf_counter()
        reload_results: list[dict[str, Any]] = []
        for model_key in reload_targets:
            result: dict[str, Any] = {
                "model_key": model_key,
                "success": False,
                "duration_seconds": None,
                "error": None,
            }
            span = perf_counter()
            try:
                reload_result = await _reload_lmstudio_model(
                    client,
                    str(model_key),
                    image_settings=image_settings,
                    reload_profile=profile,
                )
                result["method"] = reload_result.get("method")
                result["profile"] = reload_result.get("profile")
                result["response"] = reload_result.get("response")
                result["success"] = bool(reload_result.get("success"))
                if reload_result.get("warning"):
                    result["warning"] = reload_result["warning"]
                    warnings.append(str(reload_result["warning"]))
            except LMStudioResourceError as reload_error:
                result["error"] = str(reload_error)
            result["duration_seconds"] = round(perf_counter() - span, 3)
            reload_results.append(result)
        actions["reload_duration_seconds"] = round(perf_counter() - reload_started, 3)
        actions["reload_results"] = reload_results
        actions["reload_succeeded"] = all(result.get("success") for result in reload_results)
        try:
            actions["loaded_instances_after_reload"] = _unique_instances(await client.get_loaded_lmstudio_instances())
        except LMStudioResourceError as verify_error:
            actions["reload_verify_error"] = str(verify_error)
        if not actions["reload_succeeded"]:
            failed = [result for result in reload_results if not result.get("success")]
            details = "; ".join(result.get("error") or result.get("model_key") or "unknown failure" for result in failed[:3])
            warnings.append(f"LM Studio reload failed after image generation: {details}")
    except LMStudioResourceError as error:
        actions["reload_error"] = str(error)
        warnings.append(f"LM Studio reload failed after image generation: {error}")


def compact_comfyui_stats(stats: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    system = stats.get("system")
    if isinstance(system, dict):
        compact["os"] = system.get("os")
        compact["python_version"] = system.get("python_version")
        compact["pytorch_version"] = system.get("pytorch_version") or system.get("torch_version")
        compact["ram_total"] = system.get("ram_total")
        compact["ram_free"] = system.get("ram_free")
    devices = stats.get("devices")
    if isinstance(devices, list):
        compact["devices"] = []
        for device in devices[:4]:
            if not isinstance(device, dict):
                continue
            compact["devices"].append(
                {
                    "name": device.get("name"),
                    "type": device.get("type"),
                    "vram_total": device.get("vram_total"),
                    "vram_free": device.get("vram_free"),
                    "torch_vram_total": device.get("torch_vram_total"),
                    "torch_vram_free": device.get("torch_vram_free"),
                }
            )
    return compact


async def capture_comfyui_stats(
    comfy_client: ComfyUIClient,
    label: str,
    *,
    timer: ImagePerformanceTimer | None,
    actions: dict,
) -> bool:
    try:
        stats = await comfy_client.system_stats()
        compact = compact_comfyui_stats(stats)
        actions[f"comfyui_stats_{label}"] = compact
        if timer:
            timer.set_stats(label, stats)
        return True
    except ComfyUIError as error:
        actions[f"comfyui_stats_{label}_error"] = str(error)
        if timer:
            timer.set_stats(label, error=str(error))
        return False


async def free_comfyui_after_generation(
    comfy_client: ComfyUIClient,
    *,
    enabled: bool,
    warnings: list[str],
    actions: dict,
    timer: ImagePerformanceTimer | None = None,
) -> None:
    if not enabled:
        actions["comfyui_free_skipped"] = True
        return
    LAST_COMFYUI_FREE_EVENT.update(
        {"attempted": True, "success": False, "time": utc_now(), "error": None, "result": None}
    )
    actions["comfyui_free_attempted"] = True
    actions["comfyui_free_payload"] = {"unload_models": True, "free_memory": True}
    update_image_job_status(stage="freeing_comfyui", message="Freeing ComfyUI memory...", percent=92, actions=actions)
    span = timer.start("comfyui_free") if timer else perf_counter()
    try:
        result = await comfy_client.free_memory(unload_models=True, free_memory=True)
        duration = round(perf_counter() - span, 3)
        actions["comfyui_free_duration_seconds"] = duration
        actions["comfyui_free_succeeded"] = True
        actions["comfyui_free_result"] = result
        if timer:
            timer.end("comfyui_free", span, success=True)
        LAST_COMFYUI_FREE_EVENT.update({"success": True, "result": result, "error": None})
    except ComfyUIError as error:
        message = str(error)
        actions["comfyui_free_duration_seconds"] = round(perf_counter() - span, 3)
        actions["comfyui_free_succeeded"] = False
        actions["comfyui_free_error"] = message
        if timer:
            timer.end("comfyui_free", span, success=False, error=message)
        LAST_COMFYUI_FREE_EVENT.update({"success": False, "error": message, "result": None})
        warnings.append(f"ComfyUI memory cleanup failed: {message}")
    actions["comfyui_alive_after_free"] = await capture_comfyui_stats(comfy_client, "after_free", timer=timer, actions=actions)


async def cleanup_image_resources(
    comfy_client: ComfyUIClient | None,
    *,
    resource_mode: str,
    resource_warnings: list[str],
    resource_actions: dict,
    should_free_comfyui: bool,
    auto_reload_lm: bool,
    lm_reload_policy: str = "prose",
    timer: ImagePerformanceTimer | None = None,
    image_settings: Any | None = None,
) -> None:
    if comfy_client is None:
        return
    await free_comfyui_after_generation(
        comfy_client,
        enabled=should_free_comfyui,
        warnings=resource_warnings,
        actions=resource_actions,
        timer=timer,
    )
    await apply_post_image_resource_mode(
        resource_mode,
        resource_warnings,
        resource_actions,
        auto_reload=auto_reload_lm,
        reload_policy=lm_reload_policy,
        image_settings=image_settings,
    )


async def cleanup_regenerate_window_resources(context: dict[str, Any], reason: str) -> dict[str, Any]:
    resource_actions = context.get("resource_actions") or {}
    resource_warnings = context.get("resource_warnings") or []
    free_after_window = bool(context.get("free_after_window"))
    reload_after_window = bool(context.get("reload_lm_after_window"))
    resource_actions.update(
        {
            "fast_regenerate_window_active": False,
            "fast_regenerate_cleanup_reason": reason,
            "fast_regenerate_cleanup_started_at": utc_now(),
            "comfyui_free_delayed_for_regenerate": free_after_window,
            "lm_reload_delayed_for_regenerate": reload_after_window,
        }
    )
    comfy_client = ComfyUIClient(context.get("comfyui_base_url") or "http://localhost:8188")
    await cleanup_image_resources(
        comfy_client,
        resource_mode=context.get("resource_mode") or "balanced",
        resource_warnings=resource_warnings,
        resource_actions=resource_actions,
        should_free_comfyui=free_after_window,
        auto_reload_lm=False,
        lm_reload_policy=context.get("lm_reload_policy") or "prose",
        timer=None,
        image_settings=None,
    )
    await apply_post_image_resource_mode(
        context.get("resource_mode") or "balanced",
        resource_warnings,
        resource_actions,
        auto_reload=reload_after_window,
        reload_policy=context.get("lm_reload_policy") or "prose",
        force_reload=reload_after_window,
        reload_profile=context.get("lm_reload_fast_profile"),
    )
    resource_actions["fast_regenerate_cleanup_completed_at"] = utc_now()
    status_message = "Image system ready."
    if reason == "writing_requested":
        status_message = "ComfyUI memory freed and LM Studio prepared for writing."
    update_image_job_status(
        active=False,
        stage="ready",
        message=status_message,
        percent=None,
        session_id=context.get("session_id"),
        scene_id=context.get("scene_id"),
        version_id=context.get("version_id"),
        image_id=context.get("image_id"),
        resource_mode=context.get("resource_mode") or "balanced",
        warnings=resource_warnings,
        actions=resource_actions,
        error=None,
    )
    update_last_image_event(
        workflow_id=context.get("workflow_id") or "",
        workflow_name=context.get("workflow_name") or "",
        image_path=context.get("image_path"),
        error=None,
        resource_mode=context.get("resource_mode") or "balanced",
        resource_warnings=resource_warnings,
        resource_actions=resource_actions,
    )
    append_regenerate_window_report(
        "cleanup",
        {
            "reason": reason,
            "session_id": context.get("session_id"),
            "scene_id": context.get("scene_id"),
            "version_id": context.get("version_id"),
            "image_id": context.get("image_id"),
            "free_after_window": free_after_window,
            "reload_lm_after_window": reload_after_window,
            "actions": {
                "comfyui_free_attempted": resource_actions.get("comfyui_free_attempted"),
                "comfyui_free_succeeded": resource_actions.get("comfyui_free_succeeded"),
                "reload_attempted": resource_actions.get("reload_attempted"),
                "reload_succeeded": resource_actions.get("reload_succeeded"),
                "reload_skipped": resource_actions.get("reload_skipped"),
            },
            "warnings": resource_warnings,
        },
    )
    return {"attempted": True, "success": True, "resource_actions": resource_actions, "resource_warnings": resource_warnings}


def row_to_image(row) -> GeneratedImageRead:
    def json_column(name: str, fallback: Any) -> Any:
        if name not in row.keys():
            return fallback
        raw = row[name]
        if not raw:
            return fallback
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return fallback

    return GeneratedImageRead(
        id=row["id"],
        session_id=row["session_id"],
        scene_id=row["scene_id"],
        version_id=row["version_id"],
        workflow_id=row["workflow_id"],
        workflow_name=row["workflow_name"],
        prompt=row["prompt"],
        negative_prompt=row["negative_prompt"],
        seed=row["seed"],
        image_path=row["image_path"],
        image_url=row["image_url"],
        status=row["status"],
        is_primary=bool(row["is_primary"]) if "is_primary" in row.keys() else False,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        characters_included=json_column("characters_included_json", []),
        continuity_used=json_column("continuity_used_json", {}),
        reference_images_used=json_column("reference_images_used_json", []),
        live_state_used=json_column("live_state_used_json", {}),
    )


def row_to_session_image_settings(row, session_id: str) -> SessionImageSettings:
    if row is None:
        return SessionImageSettings(session_id=session_id)
    keys = set(row.keys())
    return SessionImageSettings(
        session_id=session_id,
        selected_workflow_id=row["selected_workflow_id"],
        image_resource_mode=row["image_resource_mode"] if "image_resource_mode" in keys else None,
        image_prompt_style=row["image_prompt_style"] or "",
        preferred_visual_tone=row["preferred_visual_tone"] or "",
        realism_notes=row["realism_notes"] or "",
        lighting_camera_notes=row["lighting_camera_notes"] or "",
        auto_open_preview=bool(row["auto_open_preview"]),
        auto_attach_generated=bool(row["auto_attach_generated"]),
        generate_image_while_narrating=(
            bool(row["generate_image_while_narrating"])
            if "generate_image_while_narrating" in keys and row["generate_image_while_narrating"] is not None
            else None
        ),
        auto_image_on_narrate=(
            bool(row["auto_image_on_narrate"])
            if "auto_image_on_narrate" in keys and row["auto_image_on_narrate"] is not None
            else None
        ),
        default_negative_prompt=row["default_negative_prompt"] or "",
        updated_at=row["updated_at"],
    )


def load_session_image_settings(session_id: str) -> SessionImageSettings:
    with db_session() as db:
        session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        row = db.execute(
            """
            SELECT session_id, selected_workflow_id, image_resource_mode, image_prompt_style, auto_open_preview,
                   auto_attach_generated, default_negative_prompt, preferred_visual_tone,
                   realism_notes, lighting_camera_notes, generate_image_while_narrating,
                   auto_image_on_narrate, updated_at
            FROM session_image_settings
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    return row_to_session_image_settings(row, session_id)


def selected_workflow_for_session(session_id: str | None) -> str | None:
    if session_id:
        session_settings = load_session_image_settings(session_id)
        if session_settings.selected_workflow_id:
            return session_settings.selected_workflow_id
    return load_image_settings().selected_workflow_id


def selected_workflow_response(session_id: str | None = None) -> ImageWorkflowListResponse:
    image_settings = load_image_settings()
    selected_workflow_id = selected_workflow_for_session(session_id) if session_id else image_settings.selected_workflow_id
    return ImageWorkflowListResponse(
        workflows=scan_workflows(),
        selected_workflow_id=selected_workflow_id,
        workflow_folder=str(WORKFLOW_DIR),
        image_settings=image_settings,
    )


def load_scene_version(session_id: str, scene_id: str, version_id: str | None) -> tuple[str, str | None]:
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


def create_pending_generated_image(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    workflow_id: str,
    workflow_name: str,
    prompt: str,
    negative_prompt: str,
    seed: int | None,
    characters_included: list[str] | None = None,
    continuity_used: dict[str, Any] | None = None,
    reference_images_used: list[dict[str, Any]] | None = None,
    live_state_used: dict[str, Any] | None = None,
) -> GeneratedImageRead:
    image_id = str(uuid4())
    with db_session() as db:
        db.execute(
            """
            INSERT INTO generated_images (
                id, session_id, scene_id, version_id, workflow_id, workflow_name,
                prompt, negative_prompt, seed, image_path, image_url, status, is_primary,
                characters_included_json, continuity_used_json, reference_images_used_json,
                live_state_used_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', 'pending', 0, ?, ?, ?, ?)
            """,
            (
                image_id,
                session_id,
                scene_id,
                version_id,
                workflow_id,
                workflow_name,
                prompt,
                negative_prompt,
                seed,
                json.dumps(characters_included or []),
                json.dumps(continuity_used or {}),
                json.dumps(reference_images_used or []),
                json.dumps(live_state_used or {}),
            ),
        )
        row = db.execute(
            """
            SELECT *
            FROM generated_images
            WHERE id = ?
            """,
            (image_id,),
        ).fetchone()
    return row_to_image(row)


def complete_generated_image(image_id: str, image_path: Path, image_url: str) -> GeneratedImageRead:
    with db_session() as db:
        db.execute(
            """
            UPDATE generated_images
            SET image_path = ?, image_url = ?, status = 'pending'
            WHERE id = ?
            """,
            (str(image_path), image_url, image_id),
        )
        row = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Generated image not found.")
    return row_to_image(row)


def mark_image_failed(image_id: str | None, message: str) -> None:
    if not image_id:
        return
    with db_session() as db:
        db.execute(
            """
            UPDATE generated_images
            SET status = 'failed', is_primary = 0
            WHERE id = ?
            """,
            (image_id,),
        )


def load_generated_image(image_id: str) -> GeneratedImageRead:
    with db_session() as db:
        row = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Generated image not found.")
    return row_to_image(row)


@router.get("/image-workflows", response_model=ImageWorkflowListResponse)
def list_image_workflows(session_id: str | None = Query(default=None)) -> ImageWorkflowListResponse:
    return selected_workflow_response(session_id)


@router.post("/image-workflows/refresh", response_model=ImageWorkflowListResponse)
def refresh_image_workflows(session_id: str | None = Query(default=None)) -> ImageWorkflowListResponse:
    return selected_workflow_response(session_id)


@router.post("/image-workflows/import", response_model=ImageWorkflowListResponse)
def import_image_workflow(payload: ImageWorkflowImportRequest, session_id: str | None = Query(default=None)) -> ImageWorkflowListResponse:
    try:
        imported = import_workflow_json(payload.filename, payload.content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response = selected_workflow_response(session_id)
    if session_id:
        save_session_image_settings(session_id, SessionImageSettingsUpdate(selected_workflow_id=imported.id))
        return selected_workflow_response(session_id)
    return response


@router.patch("/image-workflows/{workflow_id}/config", response_model=ImageWorkflowListResponse)
def update_workflow_config(workflow_id: str, payload: ImageWorkflowConfig) -> ImageWorkflowListResponse:
    save_workflow_config(workflow_id, payload)
    return selected_workflow_response()


@router.post("/image-workflows/{workflow_id}/validate", response_model=ImageWorkflowValidationResponse)
def validate_workflow(workflow_id: str) -> ImageWorkflowValidationResponse:
    workflow = get_workflow(workflow_id)
    if workflow is None:
        return ImageWorkflowValidationResponse(
            workflow_id=workflow_id,
            valid=False,
            errors=["Workflow file was not found."],
        )
    errors, warnings = validate_workflow_config(workflow.config)
    return ImageWorkflowValidationResponse(
        workflow_id=workflow_id,
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


@router.get("/sessions/{session_id}/image-settings", response_model=SessionImageSettings)
def get_session_image_settings(session_id: str) -> SessionImageSettings:
    return load_session_image_settings(session_id)


@router.put("/sessions/{session_id}/image-settings", response_model=SessionImageSettings)
def save_session_image_settings(session_id: str, payload: SessionImageSettingsUpdate) -> SessionImageSettings:
    existing = load_session_image_settings(session_id)
    fields_set = set(
        getattr(payload, "model_fields_set", None)
        or getattr(payload, "__fields_set__", set())
    )
    data = {
        "selected_workflow_id": payload.selected_workflow_id
        if "selected_workflow_id" in fields_set
        else existing.selected_workflow_id,
        "image_resource_mode": payload.image_resource_mode
        if "image_resource_mode" in fields_set
        else existing.image_resource_mode,
        "image_prompt_style": payload.image_prompt_style
        if "image_prompt_style" in fields_set
        else existing.image_prompt_style,
        "preferred_visual_tone": payload.preferred_visual_tone
        if "preferred_visual_tone" in fields_set
        else existing.preferred_visual_tone,
        "realism_notes": payload.realism_notes
        if "realism_notes" in fields_set
        else existing.realism_notes,
        "lighting_camera_notes": payload.lighting_camera_notes
        if "lighting_camera_notes" in fields_set
        else existing.lighting_camera_notes,
        "auto_open_preview": int(
            payload.auto_open_preview
            if "auto_open_preview" in fields_set
            else existing.auto_open_preview
        ),
        "auto_attach_generated": int(
            payload.auto_attach_generated
            if "auto_attach_generated" in fields_set
            else existing.auto_attach_generated
        ),
        "generate_image_while_narrating": (
            int(payload.generate_image_while_narrating)
            if "generate_image_while_narrating" in fields_set and payload.generate_image_while_narrating is not None
            else None
            if "generate_image_while_narrating" in fields_set
            else existing.generate_image_while_narrating
        ),
        "auto_image_on_narrate": (
            int(payload.auto_image_on_narrate)
            if "auto_image_on_narrate" in fields_set and payload.auto_image_on_narrate is not None
            else None
            if "auto_image_on_narrate" in fields_set
            else existing.auto_image_on_narrate
        ),
        "default_negative_prompt": payload.default_negative_prompt
        if "default_negative_prompt" in fields_set
        else existing.default_negative_prompt,
    }
    with db_session() as db:
        db.execute(
            """
            INSERT INTO session_image_settings (
                session_id, selected_workflow_id, image_resource_mode, image_prompt_style,
                preferred_visual_tone, realism_notes, lighting_camera_notes,
                auto_open_preview, auto_attach_generated, generate_image_while_narrating,
                auto_image_on_narrate, default_negative_prompt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                selected_workflow_id = excluded.selected_workflow_id,
                image_resource_mode = excluded.image_resource_mode,
                image_prompt_style = excluded.image_prompt_style,
                preferred_visual_tone = excluded.preferred_visual_tone,
                realism_notes = excluded.realism_notes,
                lighting_camera_notes = excluded.lighting_camera_notes,
                auto_open_preview = excluded.auto_open_preview,
                auto_attach_generated = excluded.auto_attach_generated,
                generate_image_while_narrating = excluded.generate_image_while_narrating,
                auto_image_on_narrate = excluded.auto_image_on_narrate,
                default_negative_prompt = excluded.default_negative_prompt
            """,
            (
                session_id,
                data["selected_workflow_id"],
                data["image_resource_mode"],
                data["image_prompt_style"],
                data["preferred_visual_tone"],
                data["realism_notes"],
                data["lighting_camera_notes"],
                data["auto_open_preview"],
                data["auto_attach_generated"],
                data["generate_image_while_narrating"],
                data["auto_image_on_narrate"],
                data["default_negative_prompt"],
            ),
        )
    return load_session_image_settings(session_id)


@router.get("/sessions/{session_id}/scenes/{scene_id}/image-settings", response_model=SceneImageSettings)
def get_scene_image_settings(
    session_id: str,
    scene_id: str,
    version_id: str | None = Query(default=None),
) -> SceneImageSettings:
    return SceneImageSettings(**load_scene_image_prompt_settings(session_id, scene_id, version_id))


@router.put("/sessions/{session_id}/scenes/{scene_id}/image-settings", response_model=SceneImageSettings)
def save_scene_image_settings(
    session_id: str,
    scene_id: str,
    payload: SceneImageSettingsUpdate,
) -> SceneImageSettings:
    return SceneImageSettings(
        **save_scene_image_prompt_settings(
            session_id,
            scene_id,
            payload.version_id,
            payload.selected_workflow_id,
        )
    )


@router.post("/images/prompt", response_model=ImagePromptGenerateResponse)
async def generate_prompt_preview(payload: ImagePromptGenerateRequest) -> ImagePromptGenerateResponse:
    negative_prompt = payload.negative_prompt if payload.negative_prompt is not None else None
    prompt_record = await prepare_scene_image_prompt(
        session_id=payload.session_id,
        scene_id=payload.scene_id,
        version_id=payload.version_id,
        workflow_id=payload.workflow_id,
        negative_prompt=negative_prompt,
        force_refresh=True,
    )
    workflow_info = get_workflow(prompt_record.workflow_id) if prompt_record.workflow_id else None
    seed = (
        random.randint(0, 2**32 - 1)
        if workflow_info and workflow_info.config and workflow_info.config.seed_node_id.strip()
        else None
    )
    return ImagePromptGenerateResponse(
        visual_beat=prompt_record.visual_beat,
        prompt=prompt_record.prompt,
        negative_prompt=prompt_record.negative_prompt,
        characters_included=prompt_record.characters_included,
        continuity_used=prompt_record.continuity_used,
        style_used=prompt_record.style_used,
        quality_warnings=prompt_record.quality_warnings,
        confidence=prompt_record.confidence,
        workflow_id=prompt_record.workflow_id or None,
        workflow_name=prompt_record.workflow_name,
        seed=seed,
    )


@router.get("/sessions/{session_id}/scenes/{scene_id}/image-prompt", response_model=SceneImagePromptRead | None)
def get_scene_image_prompt(
    session_id: str,
    scene_id: str,
    version_id: str | None = Query(default=None),
    workflow_id: str | None = Query(default=None),
) -> SceneImagePromptRead | None:
    _, resolved_version_id = load_scene_version(session_id, scene_id, version_id)
    workflow_info, session_settings = resolve_prompt_workflow(
        session_id,
        workflow_id,
        scene_id=scene_id,
        version_id=resolved_version_id,
    )
    resolved_workflow_id = workflow_info.id if workflow_info else (workflow_id or session_settings["selected_workflow_id"] or "")
    return load_cached_scene_image_prompt(
        session_id=session_id,
        scene_id=scene_id,
        version_id=resolved_version_id,
        workflow_id=resolved_workflow_id,
    )


@router.post("/sessions/{session_id}/scenes/{scene_id}/image-prompt", response_model=SceneImagePromptRead)
async def refresh_scene_image_prompt(
    session_id: str,
    scene_id: str,
    payload: ImagePromptGenerateRequest,
) -> SceneImagePromptRead:
    if payload.session_id != session_id or payload.scene_id != scene_id:
        raise HTTPException(status_code=400, detail="Image prompt target does not match the request path.")
    return await prepare_scene_image_prompt(
        session_id=session_id,
        scene_id=scene_id,
        version_id=payload.version_id,
        workflow_id=payload.workflow_id,
        negative_prompt=payload.negative_prompt,
        force_refresh=True,
    )


@router.post("/images/generate", response_model=GeneratedImageRead, status_code=status.HTTP_201_CREATED)
async def generate_image(payload: ImageGenerateRequest) -> GeneratedImageRead:
    image_settings = load_image_settings()
    if image_generation_is_paused(image_settings):
        actions = {
            "image_generation_mode": image_generation_mode(image_settings),
            "paused": True,
            "message": "Image generation paused. Existing images remain available.",
        }
        update_image_job_status(
            active=False,
            stage="paused",
            message="Image generation paused. Existing images remain available.",
            percent=None,
            prompt_id=None,
            session_id=payload.session_id,
            scene_id=payload.scene_id,
            version_id=payload.version_id,
            image_id=None,
            resource_mode=image_settings.image_resource_mode,
            warnings=[],
            actions=actions,
            error=None,
            elapsed_seconds=None,
            expected_time_seconds_min=None,
            expected_time_seconds_max=None,
            slow_warning=None,
        )
        raise HTTPException(
            status_code=409,
            detail="Image generation is paused for now. Existing images remain available.",
        )

    session_image_settings = load_session_image_settings(payload.session_id)
    requested_resource_mode = session_image_settings.image_resource_mode or image_settings.image_resource_mode
    resource_warnings: list[str] = []
    resource_actions: dict = {
        "mode": requested_resource_mode,
        "mode_label": mode_label(requested_resource_mode),
    }
    workflow_info_for_selection, _ = resolve_prompt_workflow(
        payload.session_id,
        payload.workflow_id,
        scene_id=payload.scene_id,
        version_id=payload.version_id,
    )
    workflow_id = (
        workflow_info_for_selection.id
        if workflow_info_for_selection
        else payload.workflow_id or session_image_settings.selected_workflow_id or image_settings.selected_workflow_id
    )
    pending_image_id: str | None = None
    comfy_client: ComfyUIClient | None = None
    if not workflow_id:
        raise HTTPException(status_code=400, detail="Select an image workflow before generating.")

    workflow_info = get_workflow(workflow_id)
    if workflow_info is None:
        raise HTTPException(status_code=404, detail="Selected workflow file was not found.")
    if workflow_info.config is None:
        raise HTTPException(status_code=400, detail="Workflow config is missing. Open Image Settings and add prompt node IDs.")
    workflow_metadata = workflow_info.metadata or extract_workflow_metadata(workflow_info.config.workflow_file)
    workflow_type = effective_workflow_type(workflow_info.config, workflow_metadata)
    workflow_type_label = WORKFLOW_TYPE_LABELS.get(workflow_type, "Unknown")
    expected_min, expected_max = expected_time_range(workflow_info.config, workflow_metadata)
    workflow_speed_note = workflow_metadata.get("workflow_speed_note")
    resource_mode, mode_warnings, mode_details = effective_resource_mode_for_workflow(
        requested_resource_mode,
        workflow_info.config,
        workflow_type,
    )
    resource_warnings.extend(mode_warnings)
    cleanup_mode = image_settings.comfyui_idle_cleanup_mode or "after_image"
    regenerate_window_seconds = _regenerate_window_seconds(image_settings)
    fast_regenerate_window = _fast_regenerate_window_enabled(image_settings, resource_mode)
    free_after_regenerate_window = bool(getattr(image_settings, "free_comfyui_after_regenerate_window", True))
    reload_after_regenerate_window = bool(getattr(image_settings, "reload_lm_after_regenerate_window", True))
    window_status_before_generation = image_regenerate_window_status()
    if window_status_before_generation.get("cleanup_running"):
        raise HTTPException(
            status_code=409,
            detail="Image cleanup is finishing. Try Regenerate Image again in a moment.",
        )
    regenerate_used_warm_comfyui = bool(
        window_status_before_generation.get("active")
        and window_status_before_generation.get("workflow_id") == workflow_id
    )
    if window_status_before_generation.get("active"):
        await cancel_image_regenerate_window("image_generation_started")
    should_free_comfyui = (
        image_settings.free_comfyui_memory_after_generation
        or cleanup_mode == "after_image"
    ) and not fast_regenerate_window
    should_free_comfyui_on_failure = should_free_comfyui or (
        resource_mode in RESOURCE_MODES_THAT_BLOCK_STORY and cleanup_mode == "idle_delay"
    ) or (
        fast_regenerate_window
        and free_after_regenerate_window
    )
    should_schedule_idle_free = (
        cleanup_mode == "idle_delay"
        and not image_settings.free_comfyui_memory_after_generation
        and not fast_regenerate_window
    )
    resource_actions.update(
        {
            "mode": resource_mode,
            "mode_label": mode_label(resource_mode),
            **mode_details,
            "workflow_type": workflow_type,
            "workflow_type_label": workflow_type_label,
            "workflow_expected_time_seconds_min": expected_min,
            "workflow_expected_time_seconds_max": expected_max,
            "workflow_speed_note": workflow_speed_note,
            "workflow_model_names": workflow_metadata.get("model_names") or [],
            "workflow_text_encoder_names": workflow_metadata.get("text_encoder_names") or [],
            "workflow_vae_names": workflow_metadata.get("vae_names") or [],
            "workflow_steps": workflow_metadata.get("steps") or [],
            "workflow_sizes": workflow_metadata.get("sizes") or [],
            "workflow_output_node_id": workflow_info.config.output_node_id.strip() or None,
            "comfyui_cleanup_mode": cleanup_mode,
            "fast_regenerate_window_enabled": fast_regenerate_window,
            "regenerate_warm_window_seconds": regenerate_window_seconds if fast_regenerate_window else 0,
            "free_comfyui_after_regenerate_window": free_after_regenerate_window,
            "reload_lm_after_regenerate_window": reload_after_regenerate_window,
            "reload_lm_immediately_after_image": (
                bool(image_settings.auto_reload_lm_after_image) and not fast_regenerate_window
            ),
            "regenerate_used_warm_comfyui": regenerate_used_warm_comfyui,
            "comfyui_idle_free_delay_seconds": (
                image_settings.comfyui_regenerate_grace_seconds if should_schedule_idle_free else None
            ),
        }
    )
    timer = ImagePerformanceTimer(
        workflow_id=workflow_id,
        workflow_name=workflow_info.name,
        workflow_type=workflow_type,
        workflow_type_label=workflow_type_label,
        expected_min=expected_min,
        expected_max=expected_max,
        resource_mode=resource_mode,
    )
    errors, warnings = validate_workflow_config(workflow_info.config)
    if errors:
        finalize_image_performance(
            timer,
            resource_actions=resource_actions,
            resource_warnings=resource_warnings,
            error=f"Workflow validation failed: {'; '.join(errors)}",
        )
        raise HTTPException(status_code=400, detail=f"Workflow validation failed: {'; '.join(errors)}")

    load_scene_started = timer.start("selected_scene_version_load")
    scene_text, version_id = load_scene_version(payload.session_id, payload.scene_id, payload.version_id)
    timer.end("selected_scene_version_load", load_scene_started)
    if not scene_text.strip():
        finalize_image_performance(
            timer,
            resource_actions=resource_actions,
            resource_warnings=resource_warnings,
            error="Selected scene version has no prose to image.",
        )
        raise HTTPException(status_code=400, detail="Selected scene version has no prose to image.")

    update_image_job_status(
        active=True,
        stage="preparing_prompt",
        message="Preparing image prompt...",
        percent=8,
        prompt_id=None,
        session_id=payload.session_id,
        scene_id=payload.scene_id,
        version_id=version_id,
        image_id=None,
        resource_mode=resource_mode,
        warnings=resource_warnings,
        actions=resource_actions,
        error=None,
        elapsed_seconds=0,
        expected_time_seconds_min=expected_min,
        expected_time_seconds_max=expected_max,
        slow_warning=None,
        started_monotonic=timer.started,
        workflow_type_label=workflow_type_label,
    )

    try:
        async with image_generation_job(
            resource_mode,
            blocks_story=resource_mode in RESOURCE_MODES_THAT_BLOCK_STORY,
        ):
            comfy_client = ComfyUIClient(image_settings.comfyui_base_url)
            reachable_started = timer.start("comfyui_reachability_check", base_url=comfy_client.base_url)
            comfy_reachable, comfy_error = await comfy_client.is_reachable()
            timer.end("comfyui_reachability_check", reachable_started, reachable=comfy_reachable, error=comfy_error)
            if not comfy_reachable:
                raise ComfyUIOfflineError(comfy_error or f"ComfyUI is not reachable at {comfy_client.base_url}.")
            await capture_comfyui_stats(comfy_client, "before_generation", timer=timer, actions=resource_actions)

            prompt_started = timer.start("image_prompt_generation_or_cache")
            characters_included: list[str] = []
            continuity_used: dict[str, Any] = {}
            live_state_used: dict[str, Any] = {}
            if payload.prompt_override and payload.prompt_override.strip():
                prompt = payload.prompt_override.strip()
                negative_prompt = (
                    payload.negative_prompt
                    if payload.negative_prompt is not None
                    else session_image_settings.default_negative_prompt
                ).strip()
                resource_actions["reference_image_note"] = "Prompt override supplied; reference selection used prompt metadata only if available."
            else:
                prompt_record = await prepare_scene_image_prompt(
                    session_id=payload.session_id,
                    scene_id=payload.scene_id,
                    version_id=payload.version_id,
                    workflow_id=workflow_id,
                    negative_prompt=payload.negative_prompt,
                    force_refresh=False,
                )
                prompt = prompt_record.prompt.strip()
                negative_prompt = (
                    payload.negative_prompt
                    if payload.negative_prompt is not None
                    else prompt_record.negative_prompt or session_image_settings.default_negative_prompt
                ).strip()
                characters_included = prompt_record.characters_included
                continuity_used = prompt_record.continuity_used
                live_state_used = {
                    "continuity_used": prompt_record.continuity_used,
                    "visual_beat": prompt_record.visual_beat,
                    "style_used": prompt_record.style_used,
                }
            timer.end("image_prompt_generation_or_cache", prompt_started, prompt_override=bool(payload.prompt_override and payload.prompt_override.strip()))
            reference_started = timer.start("reference_image_selection")
            with db_session() as db:
                reference_images_used, reference_notes = references_for_generation(
                    db,
                    session_id=payload.session_id,
                    characters_included=characters_included,
                    config=workflow_info.config,
                )
            timer.end("reference_image_selection", reference_started, reference_count=len(reference_images_used), notes=reference_notes)
            reference_image_path = reference_images_used[0]["image_path"] if reference_images_used else None
            if reference_images_used:
                resource_actions["reference_images_used"] = [
                    {
                        "id": reference.get("id"),
                        "character_id": reference.get("character_id"),
                        "character_name": reference.get("character_name"),
                        "image_url": reference.get("image_url"),
                        "used_as": reference.get("used_as"),
                    }
                    for reference in reference_images_used
                ]
            if reference_notes:
                resource_actions["reference_image_notes"] = reference_notes
            update_image_job_status(stage="prompt_ready", message="Image prompt ready.", percent=15)
            seed = payload.seed
            if seed is None and workflow_info.config.seed_node_id.strip():
                seed = random.randint(0, 2**32 - 1)

            update_image_job_status(stage="preparing_workflow", message="Preparing ComfyUI workflow...", percent=18)
            workflow_started = timer.start("workflow_payload_prepare")
            workflow_json = load_workflow(workflow_info.config.workflow_file)
            workflow_payload = inject_workflow_values(
                copy.deepcopy(workflow_json),
                workflow_info.config,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                reference_image_path=reference_image_path,
            )
            timer.end("workflow_payload_prepare", workflow_started, seed=seed)
            if resource_mode in RESOURCE_MODES_THAT_BLOCK_STORY:
                update_image_job_status(stage="unloading_lm", message="Unloading LM Studio...", percent=20)
            resource_started = timer.start("lmstudio_pre_image_resource_mode")
            existing_resource_actions = dict(resource_actions)
            resource_warnings, pre_resource_actions = await apply_pre_image_resource_mode(
                resource_mode,
                image_settings=image_settings,
            )
            resource_actions = {**existing_resource_actions, **pre_resource_actions}
            timer.end(
                "lmstudio_pre_image_resource_mode",
                resource_started,
                unload_attempted=resource_actions.get("unload_attempted"),
                unload_confirmed=resource_actions.get("unload_confirmed"),
            )
            resource_actions.update(
                {
                    "workflow_type": workflow_type,
                    "workflow_type_label": workflow_type_label,
                    "workflow_expected_time_seconds_min": expected_min,
                    "workflow_expected_time_seconds_max": expected_max,
                    "workflow_speed_note": workflow_speed_note,
                    "workflow_model_names": workflow_metadata.get("model_names") or [],
                    "workflow_diffusion_model_names": workflow_metadata.get("diffusion_model_names") or [],
                    "workflow_upscale_model_names": workflow_metadata.get("upscale_model_names") or [],
                    "workflow_auxiliary_model_names": workflow_metadata.get("auxiliary_model_names") or [],
                    "workflow_text_encoder_names": workflow_metadata.get("text_encoder_names") or [],
                    "workflow_vae_names": workflow_metadata.get("vae_names") or [],
                    "workflow_steps": workflow_metadata.get("steps") or [],
                    "workflow_sizes": workflow_metadata.get("sizes") or [],
                    "workflow_complexity": workflow_metadata.get("workflow_complexity") or "unknown",
                    "workflow_output_node_id": workflow_info.config.output_node_id.strip() or None,
                    "comfyui_backend_kept_alive_between_jobs": True,
                }
            )
            update_image_job_status(warnings=resource_warnings, actions=resource_actions)
            pending_started = timer.start("pending_image_record_create")
            pending = create_pending_generated_image(
                session_id=payload.session_id,
                scene_id=payload.scene_id,
                version_id=version_id,
                workflow_id=workflow_id,
                workflow_name=workflow_info.name,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                characters_included=characters_included,
                continuity_used=continuity_used,
                reference_images_used=reference_images_used,
                live_state_used=live_state_used,
            )
            pending_image_id = pending.id
            timer.image_id = pending_image_id
            timer.end("pending_image_record_create", pending_started, image_id=pending_image_id)
            update_image_job_status(
                stage="queueing",
                message="Queueing ComfyUI workflow...",
                percent=24,
                image_id=pending_image_id,
            )
            comfy_started = timer.start("comfyui_queue_to_image_retrieved")
            comfy_stage_spans: dict[str, float] = {
                "comfyui_queue_request": timer.start("comfyui_queue_request")
            }

            def start_stage_span(label: str) -> None:
                if label not in comfy_stage_spans:
                    comfy_stage_spans[label] = timer.start(label)

            def close_stage_span(label: str, **data: Any) -> None:
                span = comfy_stage_spans.pop(label, None)
                if span is not None:
                    timer.end(label, span, **data)

            def record_progress(progress: dict[str, Any]) -> None:
                stage = progress.get("stage") or "generating"
                if stage in {"queued", "waiting"}:
                    close_stage_span("comfyui_queue_request", prompt_id=progress.get("prompt_id"))
                    start_stage_span("comfyui_queue_wait")
                elif stage == "generating":
                    close_stage_span("comfyui_queue_request", prompt_id=progress.get("prompt_id"))
                    close_stage_span("comfyui_queue_wait", prompt_id=progress.get("prompt_id"))
                    start_stage_span("comfyui_execution_wait")
                elif stage == "completed":
                    close_stage_span("comfyui_queue_request", prompt_id=progress.get("prompt_id"))
                    close_stage_span("comfyui_queue_wait", prompt_id=progress.get("prompt_id"))
                    close_stage_span("comfyui_execution_wait", prompt_id=progress.get("prompt_id"))
                elif stage == "retrieving":
                    close_stage_span("comfyui_execution_wait", prompt_id=progress.get("prompt_id"))
                    start_stage_span("comfyui_image_retrieve")
                timer.event(
                    "comfyui_progress",
                    stage=stage,
                    percent=progress.get("percent"),
                    prompt_id=progress.get("prompt_id"),
                )
                status_messages = {
                    "queueing": "Queueing ComfyUI workflow...",
                    "queued": "Queued in ComfyUI...",
                    "waiting": "Waiting for ComfyUI...",
                    "generating": "Generating image in ComfyUI...",
                    "completed": "ComfyUI generation complete.",
                    "retrieving": "Retrieving image from ComfyUI...",
                }
                update_image_job_status(
                    stage=stage,
                    message=status_messages.get(stage, f"ComfyUI stage: {stage}"),
                    percent=progress.get("percent"),
                    prompt_id=progress.get("prompt_id") or IMAGE_JOB_STATUS.get("prompt_id"),
                )

            try:
                image = await comfy_client.generate_image(
                    workflow_payload,
                    output_node_id=workflow_info.config.output_node_id.strip() or None,
                    timeout_seconds=max(300, (expected_max or 300) + 180),
                    progress_callback=record_progress,
                )
            except Exception as error:
                for label in list(comfy_stage_spans):
                    close_stage_span(label, success=False, error=str(error))
                timer.end("comfyui_queue_to_image_retrieved", comfy_started, success=False, error=str(error))
                raise
            close_stage_span("comfyui_queue_request", success=True)
            close_stage_span("comfyui_queue_wait", success=True)
            close_stage_span("comfyui_execution_wait", success=True)
            close_stage_span("comfyui_image_retrieve", success=True)
            timer.end("comfyui_queue_to_image_retrieved", comfy_started, success=True, content_type=image.content_type, filename=image.filename)
            await capture_comfyui_stats(comfy_client, "after_generation", timer=timer, actions=resource_actions)
    except GenerationConflictError as error:
        update_image_job_status(active=False, stage="failed", message=str(error), percent=None, error=str(error))
        finalize_image_performance(
            timer,
            resource_actions=resource_actions,
            resource_warnings=resource_warnings,
            error=str(error),
        )
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ComfyUIOfflineError as error:
        mark_image_failed(pending_image_id, str(error))
        await cleanup_image_resources(
            comfy_client,
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
            should_free_comfyui=should_free_comfyui_on_failure,
            auto_reload_lm=image_settings.auto_reload_lm_after_image,
            lm_reload_policy=image_settings.lm_reload_policy,
            timer=timer,
            image_settings=image_settings,
        )
        finalize_image_performance(
            timer,
            resource_actions=resource_actions,
            resource_warnings=resource_warnings,
            image_id=pending_image_id,
            error=str(error),
        )
        update_image_job_status(active=False, stage="failed", message=str(error), percent=None, error=str(error))
        update_last_image_event(
            workflow_id=workflow_id,
            workflow_name=workflow_info.name,
            image_path=None,
            error=str(error),
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
        )
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ComfyUITimeoutError as error:
        mark_image_failed(pending_image_id, str(error))
        await cleanup_image_resources(
            comfy_client,
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
            should_free_comfyui=should_free_comfyui_on_failure,
            auto_reload_lm=image_settings.auto_reload_lm_after_image,
            lm_reload_policy=image_settings.lm_reload_policy,
            timer=timer,
            image_settings=image_settings,
        )
        finalize_image_performance(
            timer,
            resource_actions=resource_actions,
            resource_warnings=resource_warnings,
            image_id=pending_image_id,
            error=str(error),
        )
        update_image_job_status(active=False, stage="failed", message=str(error), percent=None, error=str(error))
        update_last_image_event(
            workflow_id=workflow_id,
            workflow_name=workflow_info.name,
            image_path=None,
            error=str(error),
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
        )
        raise HTTPException(status_code=504, detail=str(error)) from error
    except (ComfyUINoOutputError, ComfyUIError, KeyError) as error:
        message = str(error) if not isinstance(error, KeyError) else f"Workflow node/input missing: {error}"
        mark_image_failed(pending_image_id, message)
        await cleanup_image_resources(
            comfy_client,
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
            should_free_comfyui=should_free_comfyui_on_failure,
            auto_reload_lm=image_settings.auto_reload_lm_after_image,
            lm_reload_policy=image_settings.lm_reload_policy,
            timer=timer,
            image_settings=image_settings,
        )
        finalize_image_performance(
            timer,
            resource_actions=resource_actions,
            resource_warnings=resource_warnings,
            image_id=pending_image_id,
            error=message,
        )
        update_image_job_status(active=False, stage="failed", message=message, percent=None, error=message)
        update_last_image_event(
            workflow_id=workflow_id,
            workflow_name=workflow_info.name,
            image_path=None,
            error=message,
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
        )
        raise HTTPException(status_code=502, detail=message) from error
    except Exception as error:
        message = f"Image generation failed: {error}"
        mark_image_failed(pending_image_id, message)
        await cleanup_image_resources(
            comfy_client,
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
            should_free_comfyui=should_free_comfyui_on_failure,
            auto_reload_lm=image_settings.auto_reload_lm_after_image,
            lm_reload_policy=image_settings.lm_reload_policy,
            timer=timer,
            image_settings=image_settings,
        )
        finalize_image_performance(
            timer,
            resource_actions=resource_actions,
            resource_warnings=resource_warnings,
            image_id=pending_image_id,
            error=message,
        )
        update_image_job_status(active=False, stage="failed", message=message, percent=None, error=message)
        update_last_image_event(
            workflow_id=workflow_id,
            workflow_name=workflow_info.name,
            image_path=None,
            error=message,
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
        )
        raise HTTPException(status_code=500, detail=message) from error

    save_started = timer.start("image_file_save_and_primary_attach")
    extension = CONTENT_TYPE_EXTENSIONS.get(image.content_type, Path(image.filename).suffix or ".png")
    filename = f"{uuid4().hex}{extension}"
    output_dir = DATA_DIR / "generated_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    output_path.write_bytes(image.content)
    image_url = f"/generated-images/{filename}"
    generated = complete_generated_image(pending_image_id, output_path, image_url)
    if session_image_settings.auto_attach_generated:
        generated = accept_image(generated.id)
    timer.end("image_file_save_and_primary_attach", save_started, image_id=generated.id, image_path=str(output_path))
    if fast_regenerate_window:
        resource_actions.update(
            {
                "fast_regenerate_window_active": True,
                "fast_regenerate_window_started_at": utc_now(),
                "fast_regenerate_window_seconds": regenerate_window_seconds,
                "comfyui_kept_warm_for_regenerate": True,
                "comfyui_free_delayed_for_regenerate": free_after_regenerate_window,
                "lm_reload_delayed_for_regenerate": reload_after_regenerate_window,
                "reload_skipped": "LM Studio reload delayed for quick Regenerate Image window.",
            }
        )
        window_state = await begin_image_regenerate_window(
            delay_seconds=float(regenerate_window_seconds),
            context={
                "session_id": payload.session_id,
                "scene_id": payload.scene_id,
                "version_id": version_id,
                "image_id": generated.id,
                "image_path": str(output_path),
                "workflow_id": workflow_id,
                "workflow_name": workflow_info.name,
                "resource_mode": resource_mode,
                "resource_warnings": resource_warnings,
                "resource_actions": resource_actions,
                "comfyui_base_url": image_settings.comfyui_base_url,
                "free_after_window": free_after_regenerate_window,
                "reload_lm_after_window": reload_after_regenerate_window,
                "lm_reload_policy": image_settings.lm_reload_policy,
                "lm_reload_fast_profile": _lm_reload_fast_profile(image_settings),
            },
            cleanup=cleanup_regenerate_window_resources,
        )
        resource_actions["fast_regenerate_window"] = window_state
        append_regenerate_window_report(
            "started",
            {
                "session_id": payload.session_id,
                "scene_id": payload.scene_id,
                "version_id": version_id,
                "image_id": generated.id,
                "workflow_id": workflow_id,
                "workflow_name": workflow_info.name,
                "regenerate_warm_window_seconds": regenerate_window_seconds,
                "regenerate_used_warm_comfyui": regenerate_used_warm_comfyui,
                "free_comfyui_after_window": free_after_regenerate_window,
                "reload_lm_after_window": reload_after_regenerate_window,
            },
        )
    else:
        await cleanup_image_resources(
            comfy_client,
            resource_mode=resource_mode,
            resource_warnings=resource_warnings,
            resource_actions=resource_actions,
            should_free_comfyui=should_free_comfyui,
            auto_reload_lm=image_settings.auto_reload_lm_after_image,
            lm_reload_policy=image_settings.lm_reload_policy,
            timer=timer,
            image_settings=image_settings,
        )
    if should_schedule_idle_free:
        delay_seconds = float(image_settings.comfyui_regenerate_grace_seconds or 0)
        resource_actions["comfyui_idle_free_scheduled"] = True
        resource_actions["comfyui_idle_free_delay_seconds"] = delay_seconds
        schedule_comfyui_idle_free(
            image_settings,
            delay_seconds=delay_seconds,
            reason="after_image_regenerate_grace",
        )
    finalize_image_performance(
        timer,
        resource_actions=resource_actions,
        resource_warnings=resource_warnings,
        image_id=generated.id,
    )
    update_image_job_status(
        active=False,
        stage="completed",
        message="Image ready.",
        percent=100,
        image_id=generated.id,
        warnings=resource_warnings,
        actions=resource_actions,
        error=None,
        elapsed_seconds=timer.total_seconds(),
        slow_warning=slow_generation_warning(timer.total_seconds(), expected_min, expected_max, workflow_type_label),
    )
    update_last_image_event(
        workflow_id=workflow_id,
        workflow_name=workflow_info.name,
        image_path=str(output_path),
        error=None,
        resource_mode=resource_mode,
        resource_warnings=resource_warnings,
        resource_actions=resource_actions,
    )
    return generated.model_copy(
        update={
            "resource_mode": resource_mode,
            "resource_warnings": resource_warnings,
            "resource_actions": resource_actions,
        }
    )


@router.get("/images/job-status", response_model=ImageJobStatus)
def get_image_job_status() -> ImageJobStatus:
    return ImageJobStatus(**image_job_status())


@router.get("/images/{image_id}", response_model=GeneratedImageRead)
def get_image(image_id: str) -> GeneratedImageRead:
    return load_generated_image(image_id)


@router.post("/images/{image_id}/accept", response_model=GeneratedImageRead)
def accept_image(image_id: str) -> GeneratedImageRead:
    with db_session() as db:
        existing = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Generated image not found.")
        if not existing["image_url"]:
            raise HTTPException(status_code=400, detail="Only completed images can be accepted.")
        db.execute(
            """
            UPDATE generated_images
            SET is_primary = 0
            WHERE session_id = ?
              AND scene_id = ?
              AND (version_id = ? OR (version_id IS NULL AND ? IS NULL))
              AND status = 'accepted'
            """,
            (
                existing["session_id"],
                existing["scene_id"],
                existing["version_id"],
                existing["version_id"],
            ),
        )
        db.execute("UPDATE generated_images SET status = 'accepted', is_primary = 1 WHERE id = ?", (image_id,))
        row = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
    return row_to_image(row)


@router.post("/images/{image_id}/reject", response_model=GeneratedImageRead)
def reject_image(image_id: str) -> GeneratedImageRead:
    with db_session() as db:
        existing = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Generated image not found.")
        db.execute("UPDATE generated_images SET status = 'rejected', is_primary = 0 WHERE id = ?", (image_id,))
        row = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
    return row_to_image(row)


@router.post("/images/{image_id}/discard", response_model=GeneratedImageRead)
def discard_image(image_id: str) -> GeneratedImageRead:
    with db_session() as db:
        existing = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Generated image not found.")
        db.execute("UPDATE generated_images SET status = 'discarded', is_primary = 0 WHERE id = ?", (image_id,))
        row = db.execute("SELECT * FROM generated_images WHERE id = ?", (image_id,)).fetchone()
    return row_to_image(row)


@router.delete("/images/{image_id}", response_model=GeneratedImageRead)
def delete_image(image_id: str) -> GeneratedImageRead:
    return discard_image(image_id)


@router.get("/sessions/{session_id}/scenes/{scene_id}/images", response_model=list[GeneratedImageRead])
def list_scene_images(
    session_id: str,
    scene_id: str,
    version_id: str | None = Query(default=None),
) -> list[GeneratedImageRead]:
    params: list[str] = [session_id, scene_id]
    version_clause = ""
    if version_id:
        version_clause = "AND version_id = ?"
        params.append(version_id)
    with db_session() as db:
        rows = db.execute(
            f"""
            SELECT *
            FROM generated_images
            WHERE session_id = ? AND scene_id = ?
            {version_clause}
            ORDER BY created_at DESC
            """,
            params,
        ).fetchall()
    return [row_to_image(row) for row in rows]


def last_image_event() -> dict:
    return dict(LAST_IMAGE_EVENT)
