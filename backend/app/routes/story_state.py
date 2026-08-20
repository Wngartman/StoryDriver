from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas import StoryStateItemActionResponse, StoryStateItemUpdate, StoryStateOverview, StoryStateRunRead
from app.memory.engine import (
    archive_story_state_item,
    disable_story_state_item,
    get_story_state_overview,
    restore_story_state_item,
    run_story_state_extraction,
    undo_story_state_run,
    update_story_state_item,
)


router = APIRouter(tags=["story-state"])


@router.get("/sessions/{session_id}/story-state", response_model=StoryStateOverview)
def get_session_story_state(session_id: str) -> StoryStateOverview:
    return get_story_state_overview(session_id)


@router.post(
    "/sessions/{session_id}/scenes/{scene_id}/story-state/extract",
    response_model=StoryStateRunRead,
)
async def extract_scene_story_state(
    session_id: str,
    scene_id: str,
    version_id: str | None = Query(default=None),
) -> StoryStateRunRead:
    return await run_story_state_extraction(
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id,
        force=True,
    )


@router.post("/story-state/items/{item_type}/{item_id}/archive")
def archive_state_item(item_type: str, item_id: str) -> StoryStateItemActionResponse:
    return archive_story_state_item(item_type, item_id)


@router.post("/story-state/items/{item_type}/{item_id}/disable", response_model=StoryStateItemActionResponse)
def disable_state_item(item_type: str, item_id: str) -> StoryStateItemActionResponse:
    return disable_story_state_item(item_type, item_id)


@router.post("/story-state/items/{item_type}/{item_id}/restore", response_model=StoryStateItemActionResponse)
def restore_state_item(item_type: str, item_id: str) -> StoryStateItemActionResponse:
    return restore_story_state_item(item_type, item_id)


@router.patch("/story-state/items/{item_type}/{item_id}", response_model=StoryStateItemActionResponse)
def update_state_item(item_type: str, item_id: str, payload: StoryStateItemUpdate) -> StoryStateItemActionResponse:
    return update_story_state_item(item_type, item_id, payload)


@router.post("/story-state/runs/{run_id}/undo", response_model=StoryStateItemActionResponse)
def undo_extraction_run(run_id: str) -> StoryStateItemActionResponse:
    return undo_story_state_run(run_id)
