from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import BASE_DIR, DATA_DIR, settings
from app.schemas import SystemOpenFolderRequest, SystemOpenFolderResponse
from app.utils.paths import ensure_runtime_paths
from app.services.service_supervisor import service_supervisor


router = APIRouter(prefix="/system", tags=["system"])
_preferences_lock = threading.Lock()


class DesktopPreferences(BaseModel):
    lanEnabled: bool = False
    minimizeToTray: bool = False
    KOKORO_WORKING_DIR: str = Field(default="", max_length=1200)
    KOKORO_PYTHON_EXE: str = Field(default="", max_length=1200)


def desktop_preferences() -> dict:
    path = DATA_DIR / "config" / "desktop.json"
    fallback = {
        "lanEnabled": os.getenv("STORYDRIVER_LAN_ENABLED", "false") == "true",
        "minimizeToTray": os.getenv("STORYDRIVER_MINIMIZE_TO_TRAY", "false") == "true",
        "KOKORO_WORKING_DIR": settings.kokoro_working_dir or "",
        "KOKORO_PYTHON_EXE": settings.kokoro_python_exe or "",
    }
    if path.is_file():
        try:
            return DesktopPreferences.model_validate({**fallback, **json.loads(path.read_text(encoding="utf-8"))}).model_dump()
        except (OSError, ValueError):
            pass
    return fallback


@router.get("/info")
def system_info() -> dict:
    version_file = BASE_DIR / "VERSION"
    return {
        "app": "StoryDriver",
        "version": version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else "development",
        "data_root": str(DATA_DIR.resolve()),
        "desktop": os.getenv("STORYDRIVER_DESKTOP_MODE", "false") == "true",
        "preferences": desktop_preferences(),
        "active_lan_enabled": os.getenv("STORYDRIVER_LAN_ENABLED", "false") == "true",
        "active_minimize_to_tray": os.getenv("STORYDRIVER_MINIMIZE_TO_TRAY", "false") == "true",
    }


@router.put("/preferences")
def save_desktop_preferences(payload: DesktopPreferences, request: Request) -> dict:
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Change desktop startup settings on this computer.")
    for key in ("KOKORO_WORKING_DIR", "KOKORO_PYTHON_EXE"):
        value = getattr(payload, key).strip()
        if value and not Path(value).exists():
            raise HTTPException(400, f"The configured {key} path does not exist.")
    path = DATA_DIR / "config" / "desktop.json"
    with _preferences_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload.model_dump(), indent=2), encoding="utf-8")
        os.replace(temporary, path)
    return {"ok": True, "restart_required": True, "preferences": payload.model_dump()}

SAFE_FOLDERS = {
    "comfy_workflows": DATA_DIR / "comfy_workflows",
}


@router.get("/services")
def service_status() -> dict:
    return service_supervisor.status()


@router.post("/services/{service_name}/start")
def start_service(service_name: str) -> dict:
    if service_name != "kokoro":
        raise HTTPException(status_code=404, detail="Unknown supervised service.")
    return service_supervisor.ensure_kokoro_started()


@router.post("/services/{service_name}/stop")
def stop_service(service_name: str) -> dict:
    try:
        return service_supervisor.stop(service_name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/services/{service_name}/restart")
def restart_service(service_name: str) -> dict:
    try:
        service_supervisor.stop(service_name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return service_supervisor.ensure_kokoro_started(restart=True)


@router.post("/open-folder", response_model=SystemOpenFolderResponse)
def open_folder(payload: SystemOpenFolderRequest) -> SystemOpenFolderResponse:
    ensure_runtime_paths()
    target = SAFE_FOLDERS.get(payload.target)
    if target is None:
        raise HTTPException(status_code=400, detail="Unknown folder target.")

    root = DATA_DIR.resolve()
    path = target.resolve()
    if path != root and root not in path.parents:
        raise HTTPException(status_code=400, detail="Folder target is not inside StoryDriver data.")
    path.mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        return SystemOpenFolderResponse(
            ok=True,
            path=str(path),
            opened=False,
            message="Folder path is ready. Opening Explorer is only available on Windows.",
        )

    try:
        subprocess.Popen(["explorer.exe", str(path)])
    except Exception as error:
        return SystemOpenFolderResponse(
            ok=True,
            path=str(path),
            opened=False,
            message=f"Folder exists, but Windows Explorer could not be opened: {error}",
        )

    return SystemOpenFolderResponse(
        ok=True,
        path=str(path),
        opened=True,
        message="Opened workflow folder.",
    )
