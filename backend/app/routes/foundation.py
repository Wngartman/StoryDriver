from fastapi import APIRouter, HTTPException

from app.database import db_session
from app.schemas import StoryFoundationRead, StoryFoundationRefreshRequest, StoryFoundationUpdate
from app.memory.foundation import (
    ensure_story_foundation,
    get_story_foundation,
    update_story_foundation,
)


router = APIRouter(prefix="/sessions/{session_id}/foundation", tags=["foundation"])


def ensure_session(session_id: str) -> None:
    with db_session() as db:
        row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("", response_model=StoryFoundationRead | None)
def read_story_foundation(session_id: str) -> StoryFoundationRead | None:
    ensure_session(session_id)
    foundation = get_story_foundation(session_id)
    return StoryFoundationRead(**foundation) if foundation else None


@router.post("/ensure", response_model=StoryFoundationRead)
async def ensure_foundation(session_id: str, payload: StoryFoundationRefreshRequest) -> StoryFoundationRead:
    ensure_session(session_id)
    result = await ensure_story_foundation(
        session_id=session_id,
        director_note=payload.director_note,
        force_refresh=False,
        apply_to_cards=payload.apply_to_cards,
    )
    return StoryFoundationRead(**result["foundation"])


@router.post("/refresh", response_model=StoryFoundationRead)
async def refresh_foundation(session_id: str, payload: StoryFoundationRefreshRequest) -> StoryFoundationRead:
    ensure_session(session_id)
    result = await ensure_story_foundation(
        session_id=session_id,
        director_note=payload.director_note,
        force_refresh=payload.force,
        apply_to_cards=payload.apply_to_cards,
    )
    return StoryFoundationRead(**result["foundation"])


@router.patch("", response_model=StoryFoundationRead)
def patch_foundation(session_id: str, payload: StoryFoundationUpdate) -> StoryFoundationRead:
    ensure_session(session_id)
    result = update_story_foundation(
        session_id=session_id,
        foundation=payload.foundation,
        locked_paths=payload.locked_paths,
        apply_to_cards=payload.apply_to_cards,
    )
    return StoryFoundationRead(**result["foundation"])
