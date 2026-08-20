from __future__ import annotations

import os
import subprocess

from fastapi import APIRouter, HTTPException

from app.config import DATA_DIR
from app.schemas import SystemOpenFolderRequest, SystemOpenFolderResponse
from app.utils.paths import ensure_runtime_paths
from app.services.service_supervisor import service_supervisor


router = APIRouter(prefix="/system", tags=["system"])

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
