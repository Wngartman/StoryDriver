from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.database import db_session
from app.schemas import WorldNotesCreate, WorldNotesRead, WorldNotesUpdate


router = APIRouter(prefix="/sessions/{session_id}/world", tags=["world"])

WORLD_FIELDS = ["setting", "tone", "rules", "locations", "factions", "conflicts", "history"]


def clean_text(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def ensure_session(db, session_id: str) -> None:
    session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")


def row_to_world_notes(row) -> WorldNotesRead:
    return WorldNotesRead(
        id=row["id"],
        session_id=row["session_id"],
        setting=row["setting"],
        tone=row["tone"],
        rules=row["rules"],
        locations=row["locations"],
        factions=row["factions"],
        conflicts=row["conflicts"],
        history=row["history"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def load_world_notes(db, session_id: str):
    return db.execute(
        """
        SELECT id, session_id, setting, tone, rules, locations, factions, conflicts,
               history, created_at, updated_at
        FROM world_notes
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()


def ensure_world_notes(db, session_id: str):
    row = load_world_notes(db, session_id)
    if row is not None:
        return row
    db.execute("INSERT INTO world_notes (id, session_id) VALUES (?, ?)", (str(uuid4()), session_id))
    return load_world_notes(db, session_id)


def update_world_notes_row(db, session_id: str, values: dict[str, str]):
    row = ensure_world_notes(db, session_id)
    if not values:
        return row
    assignments = ", ".join(f"{field} = ?" for field in values)
    db.execute(
        f"UPDATE world_notes SET {assignments} WHERE session_id = ?",
        (*values.values(), session_id),
    )
    return load_world_notes(db, session_id)


@router.get("", response_model=WorldNotesRead)
def get_world_notes(session_id: str) -> WorldNotesRead:
    with db_session() as db:
        ensure_session(db, session_id)
        row = ensure_world_notes(db, session_id)
    return row_to_world_notes(row)


@router.post("", response_model=WorldNotesRead, status_code=status.HTTP_201_CREATED)
def create_world_notes(session_id: str, payload: WorldNotesCreate) -> WorldNotesRead:
    values = {field: clean_text(getattr(payload, field)) for field in WORLD_FIELDS}
    with db_session() as db:
        ensure_session(db, session_id)
        row = update_world_notes_row(db, session_id, values)
    return row_to_world_notes(row)


@router.patch("", response_model=WorldNotesRead)
def update_world_notes(session_id: str, payload: WorldNotesUpdate) -> WorldNotesRead:
    updates = payload.model_dump(exclude_unset=True)
    values = {field: clean_text(value) for field, value in updates.items() if field in WORLD_FIELDS}
    with db_session() as db:
        ensure_session(db, session_id)
        row = update_world_notes_row(db, session_id, values)
    return row_to_world_notes(row)
