from sqlite3 import IntegrityError
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.database import db_session
from app.schemas import (
    CharacterCreate,
    CharacterReferenceImageCreate,
    CharacterReferenceImageRead,
    CharacterReferenceImageUpdate,
    CharacterRead,
    CharacterUpdate,
    CharacterVisualRepairRequest,
    CharacterVisualRepairResponse,
    CharacterVisualProfileRead,
    CharacterVisualProfileUpdate,
    SessionCharacterAttach,
    SessionCharacterRead,
    SessionCharacterUpdate,
)
from app.services.character_references import (
    list_character_references,
    row_to_reference,
    store_character_reference,
    update_character_reference,
)
from app.services.character_visual_repair import repair_character_visual_profile


router = APIRouter(tags=["characters"])

CHARACTER_FIELDS = [
    "name",
    "role",
    "personality",
    "appearance",
    "relationships",
    "current_state",
    "voice",
    "image_prompt",
    "lora_trigger",
    "private_notes",
]

PROFILE_FIELDS = [
    "base_visual_description",
    "face_description",
    "hair",
    "body_build",
    "age_marker",
    "default_outfit",
    "distinctive_marks",
    "color_palette",
    "negative_prompt",
    "z_image_lora_trigger",
    "alternate_lora_triggers",
    "preferred_voice",
    "visual_consistency_notes",
    "used_in_image_prompts",
]


def clean_text(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def row_to_visual_profile(row) -> CharacterVisualProfileRead | None:
    if row is None:
        return None
    return CharacterVisualProfileRead(
        character_id=row["character_id"],
        base_visual_description=row["base_visual_description"] or "",
        face_description=row["face_description"] or "",
        hair=row["hair"] or "",
        body_build=row["body_build"] or "",
        age_marker=row["age_marker"] or "",
        default_outfit=row["default_outfit"] or "",
        distinctive_marks=row["distinctive_marks"] or "",
        color_palette=row["color_palette"] or "",
        negative_prompt=row["negative_prompt"] or "",
        z_image_lora_trigger=row["z_image_lora_trigger"] or "",
        alternate_lora_triggers=row["alternate_lora_triggers"] or "",
        preferred_voice=row["preferred_voice"] or "",
        visual_consistency_notes=row["visual_consistency_notes"] or "",
        used_in_image_prompts=bool(row["used_in_image_prompts"]),
        auto_created_confidence=float(row["auto_created_confidence"] or 0)
        if "auto_created_confidence" in row.keys()
        else 0.0,
        source_scene_id=row["source_scene_id"] if "source_scene_id" in row.keys() else None,
        source_version_id=row["source_version_id"] if "source_version_id" in row.keys() else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_character(row, prefix: str = "", visual_profile=None, reference_images=None) -> CharacterRead:
    return CharacterRead(
        id=row[f"{prefix}id"],
        name=row[f"{prefix}name"],
        role=row[f"{prefix}role"],
        personality=row[f"{prefix}personality"],
        appearance=row[f"{prefix}appearance"],
        relationships=row[f"{prefix}relationships"],
        current_state=row[f"{prefix}current_state"],
        voice=row[f"{prefix}voice"],
        image_prompt=row[f"{prefix}image_prompt"],
        lora_trigger=row[f"{prefix}lora_trigger"],
        private_notes=row[f"{prefix}private_notes"],
        auto_created=bool(row[f"{prefix}auto_created"]) if f"{prefix}auto_created" in row.keys() else False,
        visual_profile=visual_profile,
        reference_images=reference_images or [],
        created_at=row[f"{prefix}created_at"],
        updated_at=row[f"{prefix}updated_at"],
    )


def row_to_session_character(row, visual_profile=None, reference_images=None) -> SessionCharacterRead:
    return SessionCharacterRead(
        id=row["link_id"],
        session_id=row["session_id"],
        character_id=row["character_id"],
        is_active=bool(row["is_active"]),
        created_at=row["link_created_at"],
        updated_at=row["link_updated_at"],
        character=row_to_character(
            row,
            "character_",
            visual_profile=visual_profile,
            reference_images=reference_images,
        ),
    )


def ensure_session(db, session_id: str) -> None:
    session = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")


def ensure_character(db, character_id: str) -> None:
    character = db.execute("SELECT id FROM characters WHERE id = ?", (character_id,)).fetchone()
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found")


def load_character(db, character_id: str):
    return db.execute(
        """
        SELECT id, name, role, personality, appearance, relationships, current_state,
               voice, image_prompt, lora_trigger, private_notes, auto_created, created_at, updated_at
        FROM characters
        WHERE id = ?
        """,
        (character_id,),
    ).fetchone()


def load_visual_profile(db, character_id: str):
    return db.execute(
        """
        SELECT *
        FROM character_visual_profiles
        WHERE character_id = ?
        """,
        (character_id,),
    ).fetchone()


def load_visual_profiles(db, character_ids: list[str]) -> dict[str, CharacterVisualProfileRead]:
    if not character_ids:
        return {}
    placeholders = ",".join("?" for _ in character_ids)
    rows = db.execute(
        f"""
        SELECT *
        FROM character_visual_profiles
        WHERE character_id IN ({placeholders})
        """,
        character_ids,
    ).fetchall()
    return {
        profile.character_id: profile
        for profile in (row_to_visual_profile(row) for row in rows)
        if profile is not None
    }


def load_reference_images_by_character(
    db,
    character_ids: list[str],
    *,
    include_archived: bool = False,
) -> dict[str, list[CharacterReferenceImageRead]]:
    if not character_ids:
        return {}
    placeholders = ",".join("?" for _ in character_ids)
    archived_clause = "" if include_archived else "AND archived = 0"
    rows = db.execute(
        f"""
        SELECT *
        FROM character_reference_images
        WHERE character_id IN ({placeholders})
          {archived_clause}
        ORDER BY is_primary DESC, archived ASC, updated_at DESC, created_at DESC
        """,
        character_ids,
    ).fetchall()
    references: dict[str, list[CharacterReferenceImageRead]] = {}
    for row in rows:
        references.setdefault(row["character_id"], []).append(row_to_reference(row))
    return references


def clean_visual_profile(payload: CharacterVisualProfileUpdate) -> tuple[dict[str, str | int], bool]:
    fields_set = set(
        getattr(payload, "model_fields_set", None)
        or getattr(payload, "__fields_set__", set())
    )
    cleaned: dict[str, str | int] = {}
    for field in PROFILE_FIELDS:
        if field not in fields_set:
            continue
        value = getattr(payload, field)
        if field == "used_in_image_prompts":
            cleaned[field] = 1 if value is not False else 0
        else:
            cleaned[field] = clean_text(value)
    return cleaned, bool(fields_set)


def upsert_visual_profile(db, character_id: str, payload: CharacterVisualProfileUpdate | None) -> None:
    if payload is None:
        return
    cleaned, was_provided = clean_visual_profile(payload)
    if not was_provided:
        return
    data = {field: "" for field in PROFILE_FIELDS}
    data["used_in_image_prompts"] = 1
    existing = load_visual_profile(db, character_id)
    if existing:
        for field in PROFILE_FIELDS:
            data[field] = existing[field]
    data.update(cleaned)
    db.execute(
        """
        INSERT INTO character_visual_profiles (
            character_id, base_visual_description, face_description, hair, body_build,
            age_marker, default_outfit, distinctive_marks, color_palette,
            negative_prompt, z_image_lora_trigger, alternate_lora_triggers,
            preferred_voice, visual_consistency_notes, used_in_image_prompts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(character_id) DO UPDATE SET
            base_visual_description = excluded.base_visual_description,
            face_description = excluded.face_description,
            hair = excluded.hair,
            body_build = excluded.body_build,
            age_marker = excluded.age_marker,
            default_outfit = excluded.default_outfit,
            distinctive_marks = excluded.distinctive_marks,
            color_palette = excluded.color_palette,
            negative_prompt = excluded.negative_prompt,
            z_image_lora_trigger = excluded.z_image_lora_trigger,
            alternate_lora_triggers = excluded.alternate_lora_triggers,
            preferred_voice = excluded.preferred_voice,
            visual_consistency_notes = excluded.visual_consistency_notes,
            used_in_image_prompts = excluded.used_in_image_prompts
        """,
        (
            character_id,
            data["base_visual_description"],
            data["face_description"],
            data["hair"],
            data["body_build"],
            data["age_marker"],
            data["default_outfit"],
            data["distinctive_marks"],
            data["color_palette"],
            data["negative_prompt"],
            data["z_image_lora_trigger"],
            data["alternate_lora_triggers"],
            data["preferred_voice"],
            data["visual_consistency_notes"],
            data["used_in_image_prompts"],
        ),
    )


def load_session_character(db, session_id: str, character_id: str):
    return db.execute(
        """
        SELECT
            sc.id AS link_id,
            sc.session_id,
            sc.character_id,
            sc.is_active,
            sc.created_at AS link_created_at,
            sc.updated_at AS link_updated_at,
            c.id AS character_id,
            c.name AS character_name,
            c.role AS character_role,
            c.personality AS character_personality,
            c.appearance AS character_appearance,
            c.relationships AS character_relationships,
            c.current_state AS character_current_state,
            c.voice AS character_voice,
            c.image_prompt AS character_image_prompt,
            c.lora_trigger AS character_lora_trigger,
            c.private_notes AS character_private_notes,
            c.auto_created AS character_auto_created,
            c.created_at AS character_created_at,
            c.updated_at AS character_updated_at
        FROM session_characters sc
        JOIN characters c ON c.id = sc.character_id
        WHERE sc.session_id = ? AND sc.character_id = ?
        """,
        (session_id, character_id),
    ).fetchone()


def character_insert_values(payload: CharacterCreate) -> dict[str, str]:
    values = {field: clean_text(getattr(payload, field)) for field in CHARACTER_FIELDS}
    if not values["name"]:
        raise HTTPException(status_code=400, detail="Character name cannot be empty")
    return values


@router.get("/characters", response_model=list[CharacterRead])
def list_characters() -> list[CharacterRead]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT id, name, role, personality, appearance, relationships, current_state,
                   voice, image_prompt, lora_trigger, private_notes, auto_created, created_at, updated_at
            FROM characters
            ORDER BY lower(name) ASC, updated_at DESC
            """
        ).fetchall()
        profiles = load_visual_profiles(db, [row["id"] for row in rows])
        references = load_reference_images_by_character(db, [row["id"] for row in rows])
    return [
        row_to_character(
            row,
            visual_profile=profiles.get(row["id"]),
            reference_images=references.get(row["id"], []),
        )
        for row in rows
    ]


@router.post("/characters", response_model=CharacterRead, status_code=status.HTTP_201_CREATED)
def create_character(payload: CharacterCreate) -> CharacterRead:
    character_id = str(uuid4())
    values = character_insert_values(payload)

    with db_session() as db:
        if payload.attach_to_session:
            if not payload.session_id:
                raise HTTPException(status_code=400, detail="session_id is required when attaching a character")
            ensure_session(db, payload.session_id)

        db.execute(
            """
            INSERT INTO characters (
                id, name, role, personality, appearance, relationships, current_state,
                voice, image_prompt, lora_trigger, private_notes, auto_created
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character_id,
                values["name"],
                values["role"],
                values["personality"],
                values["appearance"],
                values["relationships"],
                values["current_state"],
                values["voice"],
                values["image_prompt"],
                values["lora_trigger"],
                values["private_notes"],
                1 if payload.auto_created else 0,
            ),
        )
        upsert_visual_profile(db, character_id, payload.visual_profile)

        if payload.attach_to_session and payload.session_id:
            db.execute(
                """
                INSERT INTO session_characters (id, session_id, character_id, is_active)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid4()), payload.session_id, character_id, 1 if payload.is_active else 0),
            )

        row = load_character(db, character_id)
        profile = row_to_visual_profile(load_visual_profile(db, character_id))
        references = list_character_references(db, character_id)

    return row_to_character(row, visual_profile=profile, reference_images=references)


@router.get("/characters/{character_id}", response_model=CharacterRead)
def get_character(character_id: str) -> CharacterRead:
    with db_session() as db:
        row = load_character(db, character_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Character not found")
        profile = row_to_visual_profile(load_visual_profile(db, character_id))
        references = list_character_references(db, character_id)
    return row_to_character(row, visual_profile=profile, reference_images=references)


@router.patch("/characters/{character_id}", response_model=CharacterRead)
def update_character(character_id: str, payload: CharacterUpdate) -> CharacterRead:
    updates = payload.model_dump(exclude_unset=True)
    cleaned = {
        field: clean_text(value)
        for field, value in updates.items()
        if field in CHARACTER_FIELDS
    }
    if "name" in cleaned and not cleaned["name"]:
        raise HTTPException(status_code=400, detail="Character name cannot be empty")

    with db_session() as db:
        ensure_character(db, character_id)
        if cleaned:
            assignments = ", ".join(f"{field} = ?" for field in cleaned)
            db.execute(
                f"UPDATE characters SET {assignments} WHERE id = ?",
                (*cleaned.values(), character_id),
            )
        if "auto_created" in updates and updates["auto_created"] is not None:
            db.execute(
                "UPDATE characters SET auto_created = ? WHERE id = ?",
                (1 if updates["auto_created"] else 0, character_id),
            )
        upsert_visual_profile(db, character_id, payload.visual_profile)
        row = load_character(db, character_id)
        profile = row_to_visual_profile(load_visual_profile(db, character_id))
        references = list_character_references(db, character_id)

    return row_to_character(row, visual_profile=profile, reference_images=references)


@router.post("/characters/{character_id}/visual-profile/repair", response_model=CharacterVisualRepairResponse)
def repair_visual_profile(character_id: str, payload: CharacterVisualRepairRequest) -> CharacterVisualRepairResponse:
    try:
        result = repair_character_visual_profile(
            character_id=character_id,
            session_id=payload.session_id,
            scene_id=payload.scene_id,
            version_id=payload.version_id,
            mode=payload.mode,
            apply_changes=payload.apply_changes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    with db_session() as db:
        row = load_character(db, character_id)
        profile = row_to_visual_profile(load_visual_profile(db, character_id))
        references = list_character_references(db, character_id)

    return CharacterVisualRepairResponse(
        character=row_to_character(row, visual_profile=profile, reference_images=references),
        changes=result["changes"],
        warnings=result["warnings"],
        backup_path=result["backup_path"],
        source_scene_id=result.get("source_scene_id"),
        source_version_id=result.get("source_version_id"),
        applied=result["applied"],
    )


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(character_id: str) -> None:
    with db_session() as db:
        ensure_character(db, character_id)
        db.execute("DELETE FROM characters WHERE id = ?", (character_id,))


@router.get("/characters/{character_id}/reference-images", response_model=list[CharacterReferenceImageRead])
def list_reference_images(character_id: str, include_archived: bool = False) -> list[CharacterReferenceImageRead]:
    with db_session() as db:
        ensure_character(db, character_id)
        return list_character_references(db, character_id, include_archived=include_archived)


@router.post(
    "/characters/{character_id}/reference-images",
    response_model=CharacterReferenceImageRead,
    status_code=status.HTTP_201_CREATED,
)
def add_reference_image(
    character_id: str,
    payload: CharacterReferenceImageCreate,
) -> CharacterReferenceImageRead:
    with db_session() as db:
        ensure_character(db, character_id)
        try:
            return store_character_reference(
                db,
                character_id=character_id,
                filename=payload.filename,
                content_base64=payload.content_base64,
                source=payload.source,
                notes=payload.notes,
                is_primary=payload.is_primary,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch(
    "/characters/{character_id}/reference-images/{reference_id}",
    response_model=CharacterReferenceImageRead,
)
def update_reference_image(
    character_id: str,
    reference_id: str,
    payload: CharacterReferenceImageUpdate,
) -> CharacterReferenceImageRead:
    with db_session() as db:
        ensure_character(db, character_id)
        reference = update_character_reference(
            db,
            character_id=character_id,
            reference_id=reference_id,
            source=payload.source,
            notes=payload.notes,
            is_primary=payload.is_primary,
            archived=payload.archived,
        )
        if reference is None:
            raise HTTPException(status_code=404, detail="Reference image not found")
        return reference


@router.post(
    "/characters/{character_id}/reference-images/{reference_id}/primary",
    response_model=CharacterReferenceImageRead,
)
def set_primary_reference_image(character_id: str, reference_id: str) -> CharacterReferenceImageRead:
    with db_session() as db:
        ensure_character(db, character_id)
        reference = update_character_reference(
            db,
            character_id=character_id,
            reference_id=reference_id,
            is_primary=True,
            archived=False,
        )
        if reference is None:
            raise HTTPException(status_code=404, detail="Reference image not found")
        return reference


@router.delete(
    "/characters/{character_id}/reference-images/{reference_id}",
    response_model=CharacterReferenceImageRead,
)
def archive_reference_image(character_id: str, reference_id: str) -> CharacterReferenceImageRead:
    with db_session() as db:
        ensure_character(db, character_id)
        reference = update_character_reference(
            db,
            character_id=character_id,
            reference_id=reference_id,
            archived=True,
            is_primary=False,
        )
        if reference is None:
            raise HTTPException(status_code=404, detail="Reference image not found")
        return reference


@router.get("/sessions/{session_id}/characters", response_model=list[SessionCharacterRead])
def list_session_characters(session_id: str) -> list[SessionCharacterRead]:
    with db_session() as db:
        ensure_session(db, session_id)
        rows = db.execute(
            """
            SELECT
                sc.id AS link_id,
                sc.session_id,
                sc.character_id,
                sc.is_active,
                sc.created_at AS link_created_at,
                sc.updated_at AS link_updated_at,
                c.id AS character_id,
                c.name AS character_name,
                c.role AS character_role,
                c.personality AS character_personality,
                c.appearance AS character_appearance,
                c.relationships AS character_relationships,
                c.current_state AS character_current_state,
                c.voice AS character_voice,
                c.image_prompt AS character_image_prompt,
                c.lora_trigger AS character_lora_trigger,
                c.private_notes AS character_private_notes,
                c.auto_created AS character_auto_created,
                c.created_at AS character_created_at,
                c.updated_at AS character_updated_at
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ?
            ORDER BY sc.is_active DESC, lower(c.name) ASC
            """,
            (session_id,),
        ).fetchall()
        profiles = load_visual_profiles(db, [row["character_id"] for row in rows])
        references = load_reference_images_by_character(db, [row["character_id"] for row in rows])
    return [
        row_to_session_character(
            row,
            profiles.get(row["character_id"]),
            references.get(row["character_id"], []),
        )
        for row in rows
    ]


@router.post("/sessions/{session_id}/characters/{character_id}", response_model=SessionCharacterRead)
def attach_session_character(
    session_id: str,
    character_id: str,
    payload: SessionCharacterAttach | None = None,
) -> SessionCharacterRead:
    is_active = payload.is_active if payload is not None else True
    with db_session() as db:
        ensure_session(db, session_id)
        ensure_character(db, character_id)
        try:
            db.execute(
                """
                INSERT INTO session_characters (id, session_id, character_id, is_active)
                VALUES (?, ?, ?, ?)
                """,
                (str(uuid4()), session_id, character_id, 1 if is_active else 0),
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="Character is already attached to this story",
            ) from exc
        row = load_session_character(db, session_id, character_id)
        profile = row_to_visual_profile(load_visual_profile(db, character_id))
        references = list_character_references(db, character_id)

    return row_to_session_character(row, profile, references)


@router.patch("/sessions/{session_id}/characters/{character_id}", response_model=SessionCharacterRead)
def update_session_character(
    session_id: str,
    character_id: str,
    payload: SessionCharacterUpdate,
) -> SessionCharacterRead:
    with db_session() as db:
        ensure_session(db, session_id)
        ensure_character(db, character_id)
        existing = load_session_character(db, session_id, character_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Character is not attached to this story")
        db.execute(
            """
            UPDATE session_characters
            SET is_active = ?
            WHERE session_id = ? AND character_id = ?
            """,
            (1 if payload.is_active else 0, session_id, character_id),
        )
        row = load_session_character(db, session_id, character_id)
        profile = row_to_visual_profile(load_visual_profile(db, character_id))
        references = list_character_references(db, character_id)

    return row_to_session_character(row, profile, references)


@router.delete("/sessions/{session_id}/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_session_character(session_id: str, character_id: str) -> None:
    with db_session() as db:
        ensure_session(db, session_id)
        existing = db.execute(
            """
            SELECT id
            FROM session_characters
            WHERE session_id = ? AND character_id = ?
            """,
            (session_id, character_id),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Character is not attached to this story")
        db.execute(
            "DELETE FROM session_characters WHERE session_id = ? AND character_id = ?",
            (session_id, character_id),
        )
