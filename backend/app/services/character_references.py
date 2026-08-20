from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import DATA_DIR
from app.schemas import CharacterReferenceImageRead, ImageWorkflowConfig


REFERENCE_DIR = DATA_DIR / "character_refs"
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
EXTENSION_BY_MAGIC = {
    "png": ".png",
    "jpg": ".jpg",
    "webp": ".webp",
}


def ensure_reference_root() -> None:
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)


def safe_extension(filename: str, content: bytes) -> str:
    requested = Path(filename).suffix.lower()
    detected = detect_image_type(content)
    if detected:
        return EXTENSION_BY_MAGIC[detected]
    if requested in ALLOWED_EXTENSIONS:
        return requested
    raise ValueError("Reference image must be PNG, JPG, JPEG, or WEBP.")


def detect_image_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


def decode_reference_content(content_base64: str) -> bytes:
    value = content_base64.strip()
    if "," in value and value.lower().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Reference image content is not valid base64.") from error
    if not content:
        raise ValueError("Reference image content is empty.")
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("Reference image is too large. Keep reference images under 20 MB.")
    if detect_image_type(content) is None:
        raise ValueError("Reference image must be PNG, JPG, JPEG, or WEBP.")
    return content


def reference_url(character_id: str, filename: str) -> str:
    return f"/character-refs/{character_id}/{filename}"


def row_to_reference(row) -> CharacterReferenceImageRead:
    image_path = row["image_path"] or ""
    return CharacterReferenceImageRead(
        id=row["id"],
        character_id=row["character_id"],
        filename=row["filename"] or "",
        image_path=image_path,
        image_url=row["image_url"] or "",
        source=row["source"] or "",
        notes=row["notes"] or "",
        is_primary=bool(row["is_primary"]),
        archived=bool(row["archived"]),
        missing=bool(image_path and not Path(image_path).exists()),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_character_references(db, character_id: str, *, include_archived: bool = False) -> list[CharacterReferenceImageRead]:
    archived_clause = "" if include_archived else "AND archived = 0"
    rows = db.execute(
        f"""
        SELECT *
        FROM character_reference_images
        WHERE character_id = ?
          {archived_clause}
        ORDER BY is_primary DESC, archived ASC, updated_at DESC, created_at DESC
        """,
        (character_id,),
    ).fetchall()
    return [row_to_reference(row) for row in rows]


def store_character_reference(
    db,
    *,
    character_id: str,
    filename: str,
    content_base64: str,
    source: str = "upload",
    notes: str = "",
    is_primary: bool = False,
) -> CharacterReferenceImageRead:
    content = decode_reference_content(content_base64)
    extension = safe_extension(filename, content)
    reference_id = str(uuid4())
    stored_filename = f"{reference_id}{extension}"
    character_dir = REFERENCE_DIR / character_id
    character_dir.mkdir(parents=True, exist_ok=True)
    image_path = character_dir / stored_filename
    image_path.write_bytes(content)
    if is_primary:
        db.execute(
            "UPDATE character_reference_images SET is_primary = 0 WHERE character_id = ?",
            (character_id,),
        )
    db.execute(
        """
        INSERT INTO character_reference_images (
            id, character_id, filename, image_path, image_url, source, notes, is_primary, archived
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            reference_id,
            character_id,
            stored_filename,
            str(image_path),
            reference_url(character_id, stored_filename),
            source.strip(),
            notes.strip(),
            1 if is_primary else 0,
        ),
    )
    row = db.execute("SELECT * FROM character_reference_images WHERE id = ?", (reference_id,)).fetchone()
    return row_to_reference(row)


def update_character_reference(
    db,
    *,
    character_id: str,
    reference_id: str,
    source: str | None = None,
    notes: str | None = None,
    is_primary: bool | None = None,
    archived: bool | None = None,
) -> CharacterReferenceImageRead | None:
    existing = db.execute(
        "SELECT * FROM character_reference_images WHERE id = ? AND character_id = ?",
        (reference_id, character_id),
    ).fetchone()
    if existing is None:
        return None
    updates: list[str] = []
    values: list[Any] = []
    if source is not None:
        updates.append("source = ?")
        values.append(source.strip())
    if notes is not None:
        updates.append("notes = ?")
        values.append(notes.strip())
    if archived is not None:
        updates.append("archived = ?")
        values.append(1 if archived else 0)
    if is_primary is not None:
        if is_primary:
            db.execute(
                "UPDATE character_reference_images SET is_primary = 0 WHERE character_id = ?",
                (character_id,),
            )
        updates.append("is_primary = ?")
        values.append(1 if is_primary else 0)
    if updates:
        values.extend([reference_id, character_id])
        db.execute(
            f"""
            UPDATE character_reference_images
            SET {", ".join(updates)}
            WHERE id = ? AND character_id = ?
            """,
            values,
        )
    row = db.execute(
        "SELECT * FROM character_reference_images WHERE id = ? AND character_id = ?",
        (reference_id, character_id),
    ).fetchone()
    return row_to_reference(row) if row else None


def workflow_supports_reference_mapping(config: ImageWorkflowConfig | None) -> bool:
    if config is None:
        return False
    return any(
        (
            config.reference_image_node_id.strip() and config.reference_image_input.strip(),
            config.img2img_input_node_id.strip() and config.img2img_input.strip(),
            config.character_reference_node_id.strip() and config.character_reference_input.strip(),
        )
    )


def references_for_generation(
    db,
    *,
    session_id: str,
    characters_included: list[str],
    config: ImageWorkflowConfig | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    if not workflow_supports_reference_mapping(config):
        notes.append("Selected workflow has no reference image mapping; using text-only character consistency.")
        return [], notes

    cleaned_names = [name.strip() for name in characters_included if name and name.strip()]
    unique_names: list[str] = []
    seen: set[str] = set()
    for name in cleaned_names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique_names.append(name)

    if not unique_names:
        notes.append("No included character matched a reference image.")
        return [], notes
    if len(unique_names) > 1:
        notes.append("Multiple characters are included; no multi-reference mapping is configured, so StoryDriver used text-only consistency.")
        return [], notes

    rows = db.execute(
        """
        SELECT
            c.id AS character_id,
            c.name AS character_name,
            r.*
        FROM session_characters sc
        JOIN characters c ON c.id = sc.character_id
        JOIN character_reference_images r ON r.character_id = c.id
        WHERE sc.session_id = ?
          AND sc.is_active = 1
          AND lower(c.name) = lower(?)
          AND r.archived = 0
        ORDER BY r.is_primary DESC, r.updated_at DESC, r.created_at DESC
        LIMIT 1
        """,
        (session_id, unique_names[0]),
    ).fetchall()
    if not rows:
        notes.append(f"No active reference image found for {unique_names[0]}.")
        return [], notes

    row = rows[0]
    image_path = row["image_path"] or ""
    if not image_path or not Path(image_path).exists():
        db.execute(
            "UPDATE character_reference_images SET archived = 1 WHERE id = ?",
            (row["id"],),
        )
        notes.append(f"Reference image for {row['character_name']} is missing on disk and was archived.")
        return [], notes

    reference = row_to_reference(row).model_dump()
    reference["character_name"] = row["character_name"]
    reference["used_as"] = "primary_character_reference"
    return [reference], notes
