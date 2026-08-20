from __future__ import annotations

from typing import Any

from app.schemas import ModelSettings, ResolvedTaskModelSettings
from app.settings.store import (
    TASK_MODEL_DEFINITIONS,
    TASK_MODEL_OVERRIDE_FIELDS,
    TASK_MODEL_TYPES,
    load_model_settings,
    load_task_model_profiles,
    validate_task_model_type,
)


def is_configured_override(field: str, value: Any) -> bool:
    if value is None:
        return False
    if field in {"provider", "provider_url", "lm_studio_url", "model"}:
        return bool(str(value).strip())
    if field in {"inference_backend", "reasoning_mode"}:
        return bool(str(value).strip())
    return True


def resolve_task_model_settings(task_type: str) -> tuple[ModelSettings, ResolvedTaskModelSettings]:
    normalized = validate_task_model_type(task_type)
    global_settings = load_model_settings(resolve_active_preset=True)
    profiles = load_task_model_profiles()
    profile = profiles[normalized]
    data = global_settings.model_dump()
    override_fields: list[str] = []

    for field in TASK_MODEL_OVERRIDE_FIELDS:
        value = getattr(profile, field)
        if not is_configured_override(field, value):
            continue
        data[field] = str(value).strip() if field in {"provider", "provider_url", "lm_studio_url", "model"} else value
        override_fields.append(field)

    if "lm_studio_url" in override_fields and "provider_url" not in override_fields:
        data["provider_url"] = data["lm_studio_url"]

    resolved_settings = ModelSettings(**data)
    timeout_seconds = float(
        profile.timeout_seconds
        or TASK_MODEL_DEFINITIONS[normalized]["timeout_seconds"]
        or 120
    )
    resolved = ResolvedTaskModelSettings(
        task_type=normalized,
        label=TASK_MODEL_DEFINITIONS[normalized]["label"],
        provider=resolved_settings.provider,
        provider_url=resolved_settings.provider_url,
        lm_studio_url=resolved_settings.lm_studio_url,
        model=resolved_settings.model,
        temperature=resolved_settings.temperature,
        top_p=resolved_settings.top_p,
        max_tokens=resolved_settings.max_tokens,
        seed=resolved_settings.seed,
        top_k=resolved_settings.top_k,
        min_p=resolved_settings.min_p,
        repeat_penalty=resolved_settings.repeat_penalty,
        presence_penalty=resolved_settings.presence_penalty,
        frequency_penalty=resolved_settings.frequency_penalty,
        timeout_seconds=timeout_seconds,
        streaming=resolved_settings.streaming,
        inference_backend=resolved_settings.inference_backend,
        reasoning_mode=resolved_settings.reasoning_mode,
        context_length=resolved_settings.context_length,
        fallback_to_openai_compatible=resolved_settings.fallback_to_openai_compatible,
        uses_global_model=not {"provider", "provider_url", "model", "lm_studio_url"}.intersection(override_fields),
        override_fields=[
            *override_fields,
            *(
                ["timeout_seconds"]
                if profile.timeout_seconds != TASK_MODEL_DEFINITIONS[normalized]["timeout_seconds"]
                else []
            ),
        ],
        notes=profile.notes or "",
    )
    return resolved_settings, resolved


def resolve_all_task_model_settings() -> dict[str, ResolvedTaskModelSettings]:
    return {
        task_type: resolve_task_model_settings(task_type)[1]
        for task_type in TASK_MODEL_TYPES
    }


def task_parameters(
    resolved: ResolvedTaskModelSettings,
    defaults: dict[str, Any],
    *,
    max_tokens_min: int | None = None,
    max_tokens_max: int | None = None,
) -> dict[str, Any]:
    params = dict(defaults)
    override_set = set(resolved.override_fields)
    for field in (
        "temperature",
        "top_p",
        "max_tokens",
        "seed",
        "top_k",
        "min_p",
        "repeat_penalty",
        "presence_penalty",
        "frequency_penalty",
    ):
        if field in override_set:
            params[field] = getattr(resolved, field)

    if "max_tokens" in params and params["max_tokens"] is not None:
        tokens = int(params["max_tokens"])
        if max_tokens_min is not None:
            tokens = max(max_tokens_min, tokens)
        if max_tokens_max is not None:
            tokens = min(max_tokens_max, tokens)
        params["max_tokens"] = tokens
    return params
