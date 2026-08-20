from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.services.ui_preset_store import (
    PresetConflictError,
    PresetValidationError,
    delete_custom_preset as remove_custom_preset,
    duplicate_preset,
    export_preset,
    import_custom_preset,
    list_custom_presets,
    normalize_custom_preset,
)


router = APIRouter(prefix="/ui-presets", tags=["ui-presets"])


def _payload_and_strategy(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    preset_payload = payload.get("preset") if isinstance(payload.get("preset"), dict) else payload
    strategy = str(payload.get("conflict_strategy") or payload.get("conflictStrategy") or "error")
    return preset_payload, strategy


def _handle_validation(error: Exception) -> None:
    if isinstance(error, PresetConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, PresetValidationError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)) from error


@router.get("/custom")
def get_custom_presets() -> dict[str, Any]:
    return {"presets": list_custom_presets()}


@router.post("/validate")
def validate_preset(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        preset_payload, _strategy = _payload_and_strategy(payload)
        preset = normalize_custom_preset(preset_payload)
    except Exception as exc:
        _handle_validation(exc)
    return {"valid": True, "preset": preset}


@router.post("/import", status_code=status.HTTP_201_CREATED)
def import_preset(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        preset_payload, strategy = _payload_and_strategy(payload)
        return import_custom_preset(preset_payload, conflict_strategy=strategy)
    except Exception as exc:
        _handle_validation(exc)


@router.post("/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_ui_preset(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        preset_payload, _strategy = _payload_and_strategy(payload)
        display_name = payload.get("display_name") or payload.get("displayName")
        return duplicate_preset(preset_payload, display_name=display_name)
    except Exception as exc:
        _handle_validation(exc)


@router.post("/export")
def export_ui_preset(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        preset_payload, _strategy = _payload_and_strategy(payload)
        return export_preset(preset_payload)
    except Exception as exc:
        _handle_validation(exc)


@router.delete("/custom/{preset_id}")
def delete_ui_preset(preset_id: str) -> dict[str, Any]:
    try:
        return remove_custom_preset(preset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom preset not found.") from exc
    except Exception as exc:
        _handle_validation(exc)

