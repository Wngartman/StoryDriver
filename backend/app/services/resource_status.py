from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.comfyui_idle_cleanup import idle_free_status
from app.services.comfyui_client import ComfyUIClient
from app.services.image_regenerate_window import image_regenerate_window_status
from app.services.image_workflows import get_workflow
from app.generation.model_provider import LMStudioClient, model_client_for_settings
from app.services.lmstudio_resource_client import LMStudioResourceClient, LMStudioResourceError
from app.generation.router import resolve_task_model_settings
from app.settings.store import image_generation_is_paused, image_generation_mode, load_image_settings, load_model_settings


def _first_string(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower().replace("\\", "/")


def _model_key(model: dict[str, Any]) -> str | None:
    return _first_string(
        model.get("model_key"),
        model.get("modelKey"),
        model.get("key"),
        model.get("id"),
        model.get("path"),
    )


def _model_display_name(model: dict[str, Any]) -> str | None:
    return _first_string(
        model.get("display_name"),
        model.get("displayName"),
        model.get("name"),
        model.get("label"),
        _model_key(model),
    )


def _model_matches_selected(model: dict[str, Any], selected_model: str | None) -> bool:
    selected = _normalize(selected_model)
    if not selected:
        return False
    candidates = {_normalize(_model_key(model)), _normalize(_model_display_name(model))}
    candidates = {candidate for candidate in candidates if candidate}
    candidates.update(candidate.rsplit("/", 1)[-1] for candidate in list(candidates))
    return selected in candidates


def _runtime_metadata_from_models(models: list[dict[str, Any]]) -> dict[str, Any]:
    keywords = (
        "gpu",
        "offload",
        "vram",
        "context",
        "ctx",
        "runtime",
        "reasoning",
        "thinking",
        "backend",
        "device",
        "flash",
        "kv",
        "load",
        "quant",
    )
    found: list[dict[str, Any]] = []

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if depth > 5 or len(found) >= 40:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if any(word in str(key).lower() for word in keywords) and not isinstance(child, (dict, list)):
                    found.append({"path": next_path, "value": child})
                visit(child, next_path, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:8]):
                visit(child, f"{path}[{index}]", depth + 1)

    for index, model in enumerate(models[:12]):
        visit(model, f"models[{index}]")

    paths = [item["path"].lower() for item in found]
    return {
        "metadata_fields": found[:24],
        "gpu_offload_visible": any("gpu" in path or "offload" in path for path in paths),
        "context_length_visible": any("context" in path or "ctx" in path for path in paths),
        "runtime_info_visible": bool(found),
        "api_runtime_controls_exposed": False,
        "runtime_control_note": "GPU/offload/context load settings are managed in LM Studio unless the local API exposes documented load controls.",
    }


def _plan_text(
    *,
    mode: str,
    unload_policy: str,
    loaded_count: int,
    reload_policy: str,
    reload_target: str | None,
    fast_regenerate_window: bool = False,
    window_seconds: int = 0,
) -> str:
    if mode == "manual" or unload_policy == "manual":
        return "Manual: StoryDriver will not unload or reload LM Studio automatically."
    if mode == "balanced":
        return "Balanced: keep LM Studio loaded and reuse ComfyUI."
    if unload_policy == "all":
        unload_text = f"unload {loaded_count} LM Studio model(s)"
    else:
        unload_text = "unload the selected prose model"
    if reload_policy == "preserve_manual":
        reload_text = "not auto-reload; preserve the manual LM Studio load"
    elif reload_policy == "none":
        reload_text = "not reload LM Studio"
    elif reload_policy == "all_previous":
        reload_text = "reload all previously loaded models"
    elif reload_policy == "previous_selected":
        reload_text = "reload the previously selected model"
    else:
        reload_text = f"reload {reload_target}" if reload_target else "reload the prose model"
    if fast_regenerate_window and mode in {"image_priority", "experimental_auto_swap"}:
        return (
            f"Will {unload_text}, run ComfyUI, keep it warm for {window_seconds}s for Regenerate Image, "
            f"then free ComfyUI memory and {reload_text} if idle."
        )
    return f"Will {unload_text}, run ComfyUI, free ComfyUI memory, then {reload_text}."


async def build_resource_status() -> dict:
    model_settings = load_model_settings(resolve_active_preset=True)
    image_settings = load_image_settings()
    current_image_mode = image_generation_mode(image_settings)
    images_paused = image_generation_is_paused(image_settings)
    paused_message = "Image generation paused. Existing images remain available."
    _, prose_model = resolve_task_model_settings("prose_generation")
    selected_model = model_settings.model.strip() or None
    selected_prose_model = prose_model.model.strip() or selected_model
    unload_policy = image_settings.lm_unload_policy
    reload_policy = image_settings.lm_reload_policy
    reload_fast_profile = {
        "enabled": image_settings.lm_reload_use_fast_profile,
        "prefer_cli": image_settings.lm_reload_prefer_cli,
        "parallel": image_settings.lm_reload_parallel,
        "context_length": image_settings.lm_reload_context_length,
        "gpu_offload": image_settings.lm_reload_gpu_offload,
    }
    fast_regenerate_window = bool(image_settings.fast_regenerate_window_enabled)
    window_seconds = int(image_settings.regenerate_warm_window_seconds or image_settings.comfyui_regenerate_grace_seconds or 0)
    warnings: list[str] = []

    lm_studio: dict[str, Any] = {
        "openai_reachable": False,
        "rest_reachable": False,
        "selected_model": selected_model,
        "selected_prose_model": selected_prose_model,
        "reload_target_model": selected_prose_model,
        "lm_unload_policy": unload_policy,
        "lm_reload_policy": reload_policy,
        "lm_reload_fast_profile": reload_fast_profile,
        "loaded_instances": [],
        "runtime_metadata": {
            "metadata_fields": [],
            "gpu_offload_visible": False,
            "context_length_visible": False,
            "runtime_info_visible": False,
            "api_runtime_controls_exposed": False,
            "runtime_control_note": "GPU/offload/context load settings are managed in LM Studio unless the local API exposes documented load controls.",
        },
        "loaded_instance_count": 0,
        "multiple_loaded_models": False,
        "selected_loaded_instance": None,
        "planned_image_action": paused_message if images_paused else _plan_text(
            mode=image_settings.image_resource_mode,
            unload_policy=unload_policy,
            loaded_count=0,
            reload_policy=reload_policy,
            reload_target=selected_prose_model,
            fast_regenerate_window=fast_regenerate_window,
            window_seconds=window_seconds,
        ),
        "can_unload": False,
        "can_reload_selected": False,
        "can_reload_prose": False,
        "error": None,
    }

    openai_client = model_client_for_settings(model_settings)
    try:
        await openai_client.list_models()
        lm_studio["openai_reachable"] = True
    except Exception as error:
        lm_studio["error"] = str(error)

    rest_client = LMStudioResourceClient(settings.lm_studio_rest_base_url, settings.lm_studio_api_token)
    rest_models: list[dict[str, Any]] = []
    try:
        rest_models = await rest_client.get_lmstudio_models()
        loaded_instances = await rest_client.get_loaded_lmstudio_instances()
        selected_instance = await rest_client.find_loaded_instance_for_selected_model(selected_prose_model)
        loaded_count = len(loaded_instances)
        can_unload = False
        if not images_paused and image_settings.image_resource_mode in {"image_priority", "experimental_auto_swap"}:
            if unload_policy == "all":
                can_unload = bool(loaded_instances)
            elif unload_policy == "selected":
                can_unload = bool(selected_instance)
        lm_studio.update(
            {
                "rest_reachable": True,
                "rest_base_url": rest_client.base_url,
                "loaded_instances": loaded_instances,
                "runtime_metadata": _runtime_metadata_from_models(rest_models),
                "loaded_instance_count": loaded_count,
                "multiple_loaded_models": loaded_count > 1,
                "selected_loaded_instance": selected_instance,
                "planned_image_action": paused_message if images_paused else _plan_text(
                    mode=image_settings.image_resource_mode,
                    unload_policy=unload_policy,
                    loaded_count=loaded_count,
                    reload_policy=reload_policy,
                    reload_target=selected_prose_model,
                    fast_regenerate_window=fast_regenerate_window,
                    window_seconds=window_seconds,
                ),
                "can_unload": can_unload,
                "can_reload_selected": bool(
                    selected_model and any(_model_matches_selected(model, selected_model) for model in rest_models)
                ),
                "can_reload_prose": bool(
                    selected_prose_model
                    and any(_model_matches_selected(model, selected_prose_model) for model in rest_models)
                ),
            }
        )
    except LMStudioResourceError as error:
        lm_studio["rest_base_url"] = rest_client.base_url
        lm_studio["error"] = str(error)
        if not images_paused:
            warnings.append("LM Studio REST API unavailable. Auto Image Priority cannot unload or reload the LLM automatically.")

    if lm_studio["loaded_instances"]:
        if lm_studio["multiple_loaded_models"]:
            warnings.append(
                "Multiple LM Studio models loaded. Writing speed may suffer."
                if images_paused
                else "Multiple LM Studio models loaded. Image generation may be slow."
            )
        elif not images_paused and image_settings.image_resource_mode == "balanced":
            warnings.append("LM Studio has a model loaded. Z-Image Turbo may run slower.")
        if not images_paused and unload_policy == "selected" and selected_prose_model and not lm_studio["can_unload"]:
            warnings.append("Selected prose model has no loaded instance. Nothing to unload.")

    selected_workflow = None
    selected_workflow_id = image_settings.selected_workflow_id
    selected_workflow_metadata: dict[str, Any] = {}
    if image_settings.selected_workflow_id:
        workflow = get_workflow(image_settings.selected_workflow_id)
        selected_workflow = workflow.name if workflow else image_settings.selected_workflow_id
        selected_workflow_metadata = workflow.metadata if workflow else {}

    start_command = settings.comfyui_start_command.strip()
    start_command_lower = start_command.lower()
    stable_launcher_configured = "launch_comfyui_stable_full.bat" in start_command_lower
    backend_command_configured = stable_launcher_configured or "backend" in start_command_lower or "launch_stable_comfyui.py" in start_command_lower
    comfyui_mode = (
        "Stable backend launcher"
        if stable_launcher_configured
        else "Backend service"
        if backend_command_configured
        else ("Desktop" if start_command else "Manual/Desktop")
    )
    if images_paused:
        comfyui = {
            "reachable": False,
            "paused": True,
            "optional": True,
            "status": "paused",
            "message": paused_message,
            "base_url": image_settings.comfyui_base_url.rstrip("/"),
            "selected_workflow_id": selected_workflow_id,
            "selected_workflow": selected_workflow,
            "selected_workflow_type": selected_workflow_metadata.get("workflow_type_label")
            or selected_workflow_metadata.get("workflow_type"),
            "selected_workflow_complexity": selected_workflow_metadata.get("workflow_complexity"),
            "selected_workflow_models": selected_workflow_metadata.get("diffusion_model_names")
            or selected_workflow_metadata.get("model_names")
            or [],
            "mode": "Paused / Coming Soon",
            "start_command": start_command,
            "stable_launcher_configured": stable_launcher_configured,
            "backend_command_configured": backend_command_configured,
            "working_dir_configured": bool(settings.comfyui_working_dir.strip()),
            "working_dir": settings.comfyui_working_dir,
            "keep_backend_alive_between_jobs": True,
            "cleanup_strategy": "paused; no ComfyUI polling or /free calls",
            "idle_cleanup_mode": image_settings.comfyui_idle_cleanup_mode,
            "free_before_writing": False,
            "free_throttle_seconds": image_settings.comfyui_free_throttle_seconds,
            "last_idle_free": idle_free_status(),
            "fast_regenerate_window": image_regenerate_window_status(),
            "fast_regenerate_window_enabled": False,
            "regenerate_warm_window_seconds": 0,
            "free_after_regenerate_window": False,
            "reload_lm_after_regenerate_window": False,
            "can_free_memory": False,
            "error": None,
        }
    else:
        comfy_client = ComfyUIClient(image_settings.comfyui_base_url)
        comfy_reachable, comfy_error = await comfy_client.is_reachable()
        comfyui = {
            "reachable": comfy_reachable,
            "base_url": comfy_client.base_url,
            "selected_workflow_id": selected_workflow_id,
            "selected_workflow": selected_workflow,
            "selected_workflow_type": selected_workflow_metadata.get("workflow_type_label")
            or selected_workflow_metadata.get("workflow_type"),
            "selected_workflow_complexity": selected_workflow_metadata.get("workflow_complexity"),
            "selected_workflow_models": selected_workflow_metadata.get("diffusion_model_names")
            or selected_workflow_metadata.get("model_names")
            or [],
            "mode": comfyui_mode,
            "start_command": start_command,
            "stable_launcher_configured": stable_launcher_configured,
            "backend_command_configured": backend_command_configured,
            "working_dir_configured": bool(settings.comfyui_working_dir.strip()),
            "working_dir": settings.comfyui_working_dir,
            "keep_backend_alive_between_jobs": True,
            "cleanup_strategy": "reuse backend, call /free for memory cleanup when enabled",
            "idle_cleanup_mode": image_settings.comfyui_idle_cleanup_mode,
            "free_before_writing": image_settings.free_comfyui_before_writing,
            "free_throttle_seconds": image_settings.comfyui_free_throttle_seconds,
            "last_idle_free": idle_free_status(),
            "fast_regenerate_window": image_regenerate_window_status(),
            "fast_regenerate_window_enabled": image_settings.fast_regenerate_window_enabled,
            "regenerate_warm_window_seconds": image_settings.regenerate_warm_window_seconds,
            "free_after_regenerate_window": image_settings.free_comfyui_after_regenerate_window,
            "reload_lm_after_regenerate_window": image_settings.reload_lm_after_regenerate_window,
            "can_free_memory": bool(comfy_reachable),
            "error": comfy_error if not comfy_reachable else None,
        }
        if not comfy_reachable:
            warnings.append("ComfyUI offline.")

    return {
        "lm_studio": lm_studio,
        "comfyui": comfyui,
        "image_generation_mode": current_image_mode,
        "image_generation_paused": images_paused,
        "image_generation_message": paused_message if images_paused else "",
        "image_resource_mode": image_settings.image_resource_mode,
        "lm_unload_policy": image_settings.lm_unload_policy,
        "lm_reload_policy": image_settings.lm_reload_policy,
        "lm_reload_fast_profile": reload_fast_profile,
        "auto_reload_lm_after_image": image_settings.auto_reload_lm_after_image,
        "free_comfyui_memory_after_generation": image_settings.free_comfyui_memory_after_generation,
        "comfyui_idle_cleanup_mode": image_settings.comfyui_idle_cleanup_mode,
        "fast_regenerate_window_enabled": image_settings.fast_regenerate_window_enabled,
        "regenerate_warm_window_seconds": image_settings.regenerate_warm_window_seconds,
        "free_comfyui_after_regenerate_window": image_settings.free_comfyui_after_regenerate_window,
        "reload_lm_after_regenerate_window": image_settings.reload_lm_after_regenerate_window,
        "free_comfyui_before_writing": image_settings.free_comfyui_before_writing,
        "generate_image_while_narrating": image_settings.generate_image_while_narrating,
        "auto_image_on_narrate": image_settings.auto_image_on_narrate,
        "warnings": warnings,
    }
