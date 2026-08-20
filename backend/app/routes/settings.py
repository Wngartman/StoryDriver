import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.config import settings
from app.database import db_session
from app.schemas import (
    ModelPresetCreate,
    ModelPresetRead,
    ModelPresetUpdate,
    ModelSettings,
    StoryStateSettings,
    TaskModelProfilesResponse,
    TaskModelProfileUpdate,
    TTSSettings,
    UISettings,
)
from app.generation.router import resolve_all_task_model_settings
from app.settings.store import (
    DEFAULT_MODEL_SETTINGS,
    DEFAULT_SYSTEM_PROMPT,
    load_model_settings,
    load_story_state_settings,
    load_task_model_profiles,
    load_tts_settings,
    load_ui_settings,
    merged_model_settings,
    reset_task_model_profile as persist_reset_task_model_profile,
    save_model_settings as persist_model_settings,
    save_story_state_settings as persist_story_state_settings,
    save_task_model_profile as persist_task_model_profile,
    save_tts_settings as persist_tts_settings,
    save_ui_settings as persist_ui_settings,
    task_model_type_options,
    validate_task_model_type,
)


router = APIRouter(prefix="/settings", tags=["settings"])
RETIRED_TASK_TYPES = {"image_prompt_generation"}


def row_to_preset(row) -> ModelPresetRead:
    return ModelPresetRead(
        id=row["id"],
        name=row["name"],
        system_prompt=row["system_prompt"],
        settings=json.loads(row["settings_json"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

@router.get("")
def get_settings() -> dict[str, str]:
    return {
        "app_name": settings.app_name,
        "lm_studio_base_url": settings.lm_studio_base_url,
        "lm_studio_rest_base_url": settings.lm_studio_rest_base_url,
        "kokoro_base_url": settings.kokoro_base_url,
    }


@router.get("/model", response_model=ModelSettings)
def get_model_settings() -> ModelSettings:
    return load_model_settings(resolve_active_preset=False)


@router.put("/model", response_model=ModelSettings)
def save_model_settings(payload: ModelSettings) -> ModelSettings:
    return persist_model_settings(payload)


@router.get("/tts", response_model=TTSSettings)
def get_tts_settings() -> TTSSettings:
    return load_tts_settings()


@router.put("/tts", response_model=TTSSettings)
def save_tts_settings(payload: TTSSettings) -> TTSSettings:
    return persist_tts_settings(payload)


@router.get("/story-state", response_model=StoryStateSettings)
def get_story_state_settings() -> StoryStateSettings:
    return load_story_state_settings()


@router.put("/story-state", response_model=StoryStateSettings)
def save_story_state_settings(payload: StoryStateSettings) -> StoryStateSettings:
    return persist_story_state_settings(payload)


@router.get("/ui", response_model=UISettings)
def get_ui_settings() -> UISettings:
    return load_ui_settings()


@router.put("/ui", response_model=UISettings)
def save_ui_settings(payload: UISettings) -> UISettings:
    return persist_ui_settings(payload)


def task_model_profiles_response() -> TaskModelProfilesResponse:
    profiles = load_task_model_profiles()
    resolved = resolve_all_task_model_settings()
    return TaskModelProfilesResponse(
        task_types=[item for item in task_model_type_options() if item["id"] not in RETIRED_TASK_TYPES],
        profiles={key: value for key, value in profiles.items() if key not in RETIRED_TASK_TYPES},
        resolved={key: value for key, value in resolved.items() if key not in RETIRED_TASK_TYPES},
    )


@router.get("/task-model-profiles", response_model=TaskModelProfilesResponse)
def get_task_model_profiles() -> TaskModelProfilesResponse:
    return task_model_profiles_response()


@router.put("/task-model-profiles/{task_type}", response_model=TaskModelProfilesResponse)
def save_task_model_profile(task_type: str, payload: TaskModelProfileUpdate) -> TaskModelProfilesResponse:
    if task_type in RETIRED_TASK_TYPES:
        raise HTTPException(status_code=404, detail="This retired task is not part of StoryDriver's active product.")
    try:
        persist_task_model_profile(task_type, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task_model_profiles_response()


@router.delete("/task-model-profiles/{task_type}", response_model=TaskModelProfilesResponse)
def reset_task_model_profile(task_type: str) -> TaskModelProfilesResponse:
    if task_type in RETIRED_TASK_TYPES:
        raise HTTPException(status_code=404, detail="This retired task is not part of StoryDriver's active product.")
    try:
        validate_task_model_type(task_type)
        persist_reset_task_model_profile(task_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return task_model_profiles_response()


@router.get("/model-presets", response_model=list[ModelPresetRead])
def list_model_presets() -> list[ModelPresetRead]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT id, name, system_prompt, settings_json, created_at, updated_at
            FROM model_presets
            ORDER BY updated_at DESC, created_at DESC
            """
        ).fetchall()

    return [row_to_preset(row) for row in rows]


@router.post("/model-presets", response_model=ModelPresetRead, status_code=status.HTTP_201_CREATED)
def create_model_preset(payload: ModelPresetCreate) -> ModelPresetRead:
    preset_id = str(uuid4())
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Preset name cannot be empty")

    with db_session() as db:
        db.execute(
            """
            INSERT INTO model_presets (id, name, system_prompt, settings_json)
            VALUES (?, ?, ?, ?)
            """,
            (preset_id, name, payload.system_prompt, json.dumps(payload.settings)),
        )
        row = db.execute(
            """
            SELECT id, name, system_prompt, settings_json, created_at, updated_at
            FROM model_presets
            WHERE id = ?
            """,
            (preset_id,),
        ).fetchone()

    return row_to_preset(row)


@router.patch("/model-presets/{preset_id}", response_model=ModelPresetRead)
def update_model_preset(preset_id: str, payload: ModelPresetUpdate) -> ModelPresetRead:
    with db_session() as db:
        row = db.execute(
            """
            SELECT id, name, system_prompt, settings_json, created_at, updated_at
            FROM model_presets
            WHERE id = ?
            """,
            (preset_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Preset not found")

        name = payload.name.strip() if payload.name is not None else row["name"]
        system_prompt = payload.system_prompt if payload.system_prompt is not None else row["system_prompt"]
        preset_settings = payload.settings if payload.settings is not None else json.loads(row["settings_json"] or "{}")
        if not name:
            raise HTTPException(status_code=400, detail="Preset name cannot be empty")

        db.execute(
            """
            UPDATE model_presets
            SET name = ?, system_prompt = ?, settings_json = ?
            WHERE id = ?
            """,
            (name, system_prompt, json.dumps(preset_settings), preset_id),
        )
        updated = db.execute(
            """
            SELECT id, name, system_prompt, settings_json, created_at, updated_at
            FROM model_presets
            WHERE id = ?
            """,
            (preset_id,),
        ).fetchone()

    return row_to_preset(updated)


@router.delete("/model-presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_preset(preset_id: str) -> None:
    with db_session() as db:
        existing = db.execute(
            "SELECT id FROM model_presets WHERE id = ?",
            (preset_id,),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Preset not found")
        db.execute("DELETE FROM model_presets WHERE id = ?", (preset_id,))
