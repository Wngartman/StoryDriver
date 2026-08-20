from fastapi import APIRouter, HTTPException

from app.database import db_session
from app.schemas import SessionSummaryRead, StoryMemoryRead
from app.memory.summaries import latest_summary, maybe_update_session_summary, summary_status


router = APIRouter(prefix="/sessions/{session_id}", tags=["memory"])


def ensure_session(db, session_id: str) -> None:
    session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")


def row_to_memory(row) -> StoryMemoryRead:
    return StoryMemoryRead(
        id=row["id"],
        session_id=row["session_id"],
        character_id=row["character_id"],
        memory_type=row["memory_type"],
        title=row["title"],
        content=row["content"],
        importance=row["importance"],
        keywords_json=row["keywords_json"],
        source_scene_id=row["source_scene_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_summary(row) -> SessionSummaryRead:
    return SessionSummaryRead(
        id=row["id"],
        session_id=row["session_id"],
        summary_text=row["summary_text"],
        from_scene_id=row["from_scene_id"],
        to_scene_id=row["to_scene_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/memories", response_model=list[StoryMemoryRead])
def list_memories(session_id: str) -> list[StoryMemoryRead]:
    with db_session() as db:
        ensure_session(db, session_id)
        rows = db.execute(
            """
            SELECT id, session_id, character_id, memory_type, title, content, importance,
                   keywords_json, source_scene_id, created_at, updated_at
            FROM story_memories
            WHERE session_id = ?
            ORDER BY importance DESC, updated_at DESC, created_at DESC
            """,
            (session_id,),
        ).fetchall()
    return [row_to_memory(row) for row in rows]


@router.get("/summary", response_model=SessionSummaryRead | None)
def get_summary(session_id: str) -> SessionSummaryRead | None:
    with db_session() as db:
        ensure_session(db, session_id)
        row = db.execute(
            """
            SELECT id, session_id, summary_text, from_scene_id, to_scene_id, created_at, updated_at
            FROM session_summaries
            WHERE session_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    return row_to_summary(row) if row else None


@router.post("/summary/refresh")
async def refresh_summary(session_id: str) -> dict:
    with db_session() as db:
        ensure_session(db, session_id)
    result = await maybe_update_session_summary(session_id, force=True)
    summary = latest_summary(session_id)
    return {
        "ok": result.get("status") in {"completed", "current", "skipped"},
        "result": result,
        "summary": summary,
        "summary_status": summary_status(session_id),
    }
