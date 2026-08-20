import logging
from pathlib import Path
import sys

from app.utils.openssl_dlls import add_openssl_dll_directory

add_openssl_dll_directory()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, BASE_DIR, DATA_DIR, settings as app_settings
from app.database import init_db
from app.routes import backgrounds, characters, diagnostics, foundation, health, memory, models, quality, sessions, settings, storage, story_state, system, tts, ui_presets, world
from app.settings.store import install_storydriver_prose_v3_defaults
from app.services.story_delete_jobs import recover_stale_delete_jobs_on_startup
from app.services.service_supervisor import service_supervisor


app = FastAPI(title=APP_NAME)
logging.basicConfig(level=logging.INFO)

LOCAL_DEV_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|\[::1\]|"
    r"10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|"
    r"[A-Za-z0-9-]+|[A-Za-z0-9.-]+\.local"
    r")(?::\d{1,5})?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=LOCAL_DEV_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def resolve_frontend_dist() -> Path | None:
    candidates = [
        Path(app_settings.frontend_dist) if app_settings.frontend_dist else None,
        Path(getattr(sys, "_MEIPASS", "")) / "frontend_dist" if getattr(sys, "_MEIPASS", "") else None,
        Path(sys.executable).resolve().parent / "frontend_dist",
        BASE_DIR / "frontend" / "dist",
    ]
    for candidate in candidates:
        if candidate and (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


FRONTEND_DIST = resolve_frontend_dist()


@app.on_event("startup")
def startup() -> None:
    init_db()
    install_storydriver_prose_v3_defaults()
    recover_stale_delete_jobs_on_startup()
    service_supervisor.start_background()


@app.on_event("shutdown")
def shutdown() -> None:
    service_supervisor.shutdown()


@app.get("/", tags=["health"])
def get_backend_root(request: Request):
    if FRONTEND_DIST:
        return FileResponse(FRONTEND_DIST / "index.html")
    host = request.headers.get("host", "localhost:8001")
    hostname = host.rsplit(":", 1)[0] if ":" in host and not host.startswith("[") else host
    if hostname in {"0.0.0.0", ""}:
        hostname = "localhost"
    frontend = f"{request.url.scheme}://{host}"
    return {
        "app": "StoryDriver",
        "role": "backend",
        "health": "/health",
        "frontend": frontend,
        "note": "Production frontend assets are not present; build frontend/dist for the one-port app.",
    }


app.include_router(health.router)
app.include_router(backgrounds.router)
app.include_router(diagnostics.router)
app.include_router(models.router)
app.include_router(sessions.router)
app.include_router(foundation.router)
app.include_router(characters.router)
app.include_router(world.router)
app.include_router(memory.router)
app.include_router(settings.router)
app.include_router(storage.router)
app.include_router(story_state.router)
app.include_router(system.router)
app.include_router(tts.router)
app.include_router(quality.router)
app.include_router(ui_presets.router)

(DATA_DIR / "generated_audio").mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=DATA_DIR / "generated_audio"), name="audio")

if FRONTEND_DIST and (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/{frontend_path:path}", include_in_schema=False)
def get_frontend_route(frontend_path: str):
    if not FRONTEND_DIST:
        return {"detail": "StoryDriver frontend build is not installed."}
    requested = (FRONTEND_DIST / frontend_path).resolve()
    if requested != FRONTEND_DIST and FRONTEND_DIST not in requested.parents:
        return FileResponse(FRONTEND_DIST / "index.html")
    if requested.is_file():
        return FileResponse(requested)
    return FileResponse(FRONTEND_DIST / "index.html")
