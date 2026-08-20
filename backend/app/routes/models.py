from time import perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException

from app.generation.model_provider import LMStudioError, LMStudioOfflineError
from app.generation.provider_runtime import PROVIDER_IDS, model_library, provider_registry
from app.schemas import ModelRead
from app.services.lmstudio_resource_client import LMStudioResourceClient, LMStudioResourceError
from app.settings.store import load_model_settings


router = APIRouter(tags=["models"])


@router.get("/models", response_model=list[ModelRead])
async def list_models() -> list[ModelRead]:
    model_settings = load_model_settings(resolve_active_preset=True)
    provider = provider_registry.get(model_settings.provider, model_settings.provider_url)
    try:
        models = await provider.discover_models()
    except LMStudioOfflineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LMStudioError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    loaded_keys: set[str] = set()
    if model_settings.provider == "lm_studio":
        try:
            for instance in await LMStudioResourceClient().get_loaded_lmstudio_instances():
                for value in (instance.get("id"), instance.get("model_key"), instance.get("display_name")):
                    if value:
                        loaded_keys.add(str(value).strip().lower().rsplit("/", 1)[-1])
        except LMStudioResourceError:
            pass

    return [
        ModelRead(
            id=str(model.get("id")),
            name=model.get("name") or model.get("id"),
            loaded=(
                str(model.get("id") or "").strip().lower().rsplit("/", 1)[-1] in loaded_keys
                if loaded_keys
                else None
            ),
            state=(
                "loaded"
                if str(model.get("id") or "").strip().lower().rsplit("/", 1)[-1] in loaded_keys
                else "available"
            ),
            source=model_settings.provider,
        )
        for model in models
        if model.get("id")
    ]


@router.get("/models/providers")
async def list_model_providers() -> dict[str, Any]:
    model_settings = load_model_settings(resolve_active_preset=True)
    rows: list[dict[str, Any]] = []
    for provider_id in PROVIDER_IDS:
        endpoint = model_settings.provider_url if provider_id == model_settings.provider else (
            "http://127.0.0.1:12345/v1" if provider_id == "llama_cpp" else model_settings.lm_studio_url
        )
        provider = provider_registry.get(provider_id, endpoint)
        rows.append(
            {
                "id": provider_id,
                "name": provider.display_name,
                "selected": provider_id == model_settings.provider,
                "endpoint": endpoint,
                "capabilities": await provider.get_capabilities(),
            }
        )
    return {"providers": rows}


@router.post("/models/provider/diagnostics")
async def model_provider_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return await provider_registry.diagnostics(
            str(payload.get("provider") or ""),
            str(payload.get("endpoint") or ""),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/models/provider/load")
async def load_provider_model(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        provider = provider_registry.get(
            str(payload.get("provider") or ""),
            str(payload.get("endpoint") or ""),
        )
        return await provider.load_model(str(payload.get("model") or ""), payload.get("options") or {})
    except (ValueError, RuntimeError, TimeoutError, LMStudioError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/models/provider/unload")
async def unload_provider_model(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        provider = provider_registry.get(
            str(payload.get("provider") or ""),
            str(payload.get("endpoint") or ""),
        )
        return await provider.unload_model(str(payload.get("model") or "") or None)
    except (ValueError, RuntimeError, LMStudioError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/models/provider/test")
async def test_provider_model(payload: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(payload.get("provider") or "")
    endpoint = str(payload.get("endpoint") or "")
    model = str(payload.get("model") or "")
    if not model:
        raise HTTPException(status_code=400, detail="Select a model before running the local test.")
    started = perf_counter()
    try:
        result = await provider_registry.get(provider_id, endpoint).generate(
            model=model,
            system_prompt="Return only the requested text.",
            user_prompt="Reply with exactly: StoryDriver local provider ready",
            parameters={"temperature": 0, "max_tokens": 24},
            timeout=float(payload.get("timeout_seconds") or 30),
            inference_backend="openai_compatible",
            reasoning_mode="off",
            fallback_to_openai_compatible=False,
        )
    except (ValueError, RuntimeError, LMStudioError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ok": True,
        "provider": provider_id,
        "model": model,
        "latency_ms": round((perf_counter() - started) * 1000, 2),
        "text": str(result.get("text") or "")[:200],
    }


@router.post("/models/provider/cancel")
async def cancel_provider_generation(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        provider = provider_registry.get(
            str(payload.get("provider") or ""),
            str(payload.get("endpoint") or ""),
        )
        return await provider.cancel()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/models/library")
def get_model_library() -> dict[str, Any]:
    return {"models": model_library.list()}


@router.post("/models/library/gguf")
def add_library_gguf(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"model": model_library.add_gguf(str(payload.get("path") or ""))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/models/library/scan")
def scan_model_library(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        models = model_library.scan(str(payload.get("directory") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"models": models, "count": len(models)}


@router.post("/models/library/remove")
def remove_library_model(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "removed": model_library.remove(str(payload.get("id") or "")),
        "deleted_model_file": False,
    }
