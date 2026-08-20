from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from app.config import DATA_DIR


GENERATED_IMAGE_DIR = DATA_DIR / "generated_images"
GENERATED_AUDIO_DIR = DATA_DIR / "generated_audio"
TTS_MANIFEST_PREFIX = "tts_manifest_"
TTS_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".webm"}
IMAGE_URL_PREFIXES = ("/generated-images/", "/generated_images/")
AUDIO_URL_PREFIX = "/audio/"
PENDING_SUFFIX = ".pending"
GENERATED_PLACEHOLDER_FILENAMES = {
    ".gitkeep",
    ".gitignore",
    "README.md",
    "desktop.ini",
    "Thumbs.db",
}


def generated_roots() -> dict[str, Path]:
    return {
        "image": GENERATED_IMAGE_DIR,
        "audio": GENERATED_AUDIO_DIR,
    }


def _safe_relative_path(value: str) -> Path | None:
    normalized = value.strip().replace("\\", "/").lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return None
    return Path(normalized)


def candidate_path_from_reference(reference: str | Path | None, media_type: str | None = None) -> tuple[Path | None, str, str | None]:
    if reference is None:
        return None, "empty reference", None
    raw = str(reference).strip().strip('"')
    if not raw:
        return None, "empty reference", None
    if "://" in raw:
        return None, "URL references are not local generated files", None

    for prefix in IMAGE_URL_PREFIXES:
        if raw.startswith(prefix):
            relative = _safe_relative_path(raw[len(prefix) :])
            if relative is None:
                return None, "image URL contains unsafe path traversal", "image"
            return GENERATED_IMAGE_DIR / relative, "", "image"
    if raw.startswith(AUDIO_URL_PREFIX):
        relative = _safe_relative_path(raw[len(AUDIO_URL_PREFIX) :])
        if relative is None:
            return None, "audio URL contains unsafe path traversal", "audio"
        return GENERATED_AUDIO_DIR / relative, "", "audio"

    path = Path(raw)
    if path.is_absolute():
        return path, "", media_type

    if media_type in generated_roots():
        relative = _safe_relative_path(raw)
        if relative is None:
            return None, "relative reference contains unsafe path traversal", media_type
        return generated_roots()[media_type] / relative, "", media_type

    return None, "relative reference has no generated media type", media_type


def safe_generated_file_path(reference: str | Path | None, media_type: str | None = None) -> tuple[Path | None, str, str | None]:
    candidate, reason, candidate_media_type = candidate_path_from_reference(reference, media_type)
    if candidate is None:
        return None, reason, candidate_media_type

    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:
        return None, f"could not resolve path: {exc}", candidate_media_type

    matched_media_type: str | None = None
    for root_media_type, root in generated_roots().items():
        root_resolved = root.resolve(strict=False)
        try:
            if os.path.commonpath([str(resolved), str(root_resolved)]) == str(root_resolved):
                matched_media_type = root_media_type
                break
        except ValueError:
            continue
    if not matched_media_type:
        return None, "path is outside StoryDriver generated folders", candidate_media_type
    if candidate_media_type and candidate_media_type != matched_media_type:
        return None, f"path is under generated_{matched_media_type}, not generated_{candidate_media_type}", candidate_media_type

    return resolved, "", matched_media_type


def canonical_path(path: str | Path) -> str:
    return str(Path(path).resolve(strict=False)).casefold()


def generated_file_record(path: Path, media_type: str, reason: str = "") -> dict[str, Any]:
    size = 0
    try:
        size = path.stat().st_size if path.exists() and path.is_file() and not path.is_symlink() else 0
    except OSError:
        size = 0
    return {
        "path": str(path),
        "media_type": media_type,
        "size_bytes": size,
        "reason": reason,
    }


def delete_generated_file_references(references: Iterable[str | Path | None]) -> dict[str, Any]:
    deleted: list[str] = []
    skipped: list[str] = []
    deleted_counts = {"image": 0, "audio": 0}
    deleted_bytes = 0
    seen: set[str] = set()

    for reference in references:
        path, reason, media_type = safe_generated_file_path(reference)
        label = str(reference) if reference is not None else ""
        if path is None:
            skipped.append(f"{label or '<empty>'} - {reason}")
            continue
        key = canonical_path(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.exists():
            skipped.append(f"{path} - missing")
            continue
        if path.is_symlink():
            skipped.append(f"{path} - symlink skipped")
            continue
        if path.is_dir():
            skipped.append(f"{path} - directory skipped")
            continue
        if not path.is_file():
            skipped.append(f"{path} - not a regular file")
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            deleted.append(str(path))
            if media_type in deleted_counts:
                deleted_counts[media_type] += 1
            deleted_bytes += size
        except OSError as exc:
            skipped.append(f"{path} - delete failed: {exc}")

    return {
        "files_deleted": deleted,
        "files_skipped": skipped,
        "deleted_counts": deleted_counts,
        "deleted_bytes": deleted_bytes,
    }


def _row_values(db, sql: str, params: tuple = ()) -> set[str]:
    rows = db.execute(sql, params).fetchall()
    values: set[str] = set()
    for row in rows:
        value = row[0]
        if value:
            values.add(str(value))
    return values


def collect_story_generated_file_references(db, session_id: str) -> list[str]:
    references: list[str] = []
    if _table_exists(db, "generated_images"):
        rows = db.execute(
            """
            SELECT image_path, image_url
            FROM generated_images
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
        for row in rows:
            references.extend([row["image_path"], row["image_url"]])

    scene_ids = _row_values(db, "SELECT id FROM scenes WHERE session_id = ?", (session_id,)) if _table_exists(db, "scenes") else set()
    version_ids = (
        _row_values(db, "SELECT id FROM scene_versions WHERE session_id = ?", (session_id,))
        if _table_exists(db, "scene_versions")
        else set()
    )
    for manifest_path, manifest in iter_tts_manifests():
        if manifest_matches_story(manifest, session_id=session_id, scene_ids=scene_ids, version_ids=version_ids):
            references.append(str(manifest_path))
            references.extend(audio_references_from_manifest(manifest, manifest_path))
    return [reference for reference in references if reference]


def _table_exists(db, table_name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def iter_tts_manifests() -> Iterable[tuple[Path, dict[str, Any]]]:
    if not GENERATED_AUDIO_DIR.exists():
        return []
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in GENERATED_AUDIO_DIR.glob(f"{TTS_MANIFEST_PREFIX}*.json"):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = {}
        manifests.append((manifest_path, parsed if isinstance(parsed, dict) else {}))
    return manifests


def manifest_matches_story(
    manifest: dict[str, Any],
    *,
    session_id: str,
    scene_ids: set[str],
    version_ids: set[str],
) -> bool:
    manifest_session_id = str(manifest.get("session_id") or "").strip()
    manifest_scene_id = str(manifest.get("scene_id") or "").strip()
    manifest_version_id = str(manifest.get("version_id") or "").strip()
    return (
        bool(manifest_session_id and manifest_session_id == session_id)
        or bool(manifest_scene_id and manifest_scene_id in scene_ids)
        or bool(manifest_version_id and manifest_version_id in version_ids)
    )


def audio_references_from_manifest(manifest: dict[str, Any], manifest_path: Path | None = None) -> list[str]:
    references: list[str] = []
    audio_path = str(manifest.get("audio_path") or "").strip()
    audio_url = str(manifest.get("audio_url") or "").strip()
    if audio_path:
        references.append(audio_path)
    if audio_url:
        references.append(audio_url)
    cache_key = str(manifest.get("cache_key") or "").strip()
    if cache_key:
        for extension in TTS_AUDIO_EXTENSIONS:
            references.append(str(GENERATED_AUDIO_DIR / f"kokoro_{cache_key}{extension}"))
    if manifest_path is not None:
        references.append(str(manifest_path))
    return references


def manifest_has_active_db_reference(db, manifest: dict[str, Any]) -> bool:
    session_id = str(manifest.get("session_id") or "").strip()
    scene_id = str(manifest.get("scene_id") or "").strip()
    version_id = str(manifest.get("version_id") or "").strip()
    if not any((session_id, scene_id, version_id)):
        return False
    if session_id and _table_exists(db, "sessions"):
        row = db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return False
    if scene_id and _table_exists(db, "scenes"):
        params: tuple[Any, ...] = (scene_id, session_id) if session_id else (scene_id,)
        clause = "id = ? AND session_id = ?" if session_id else "id = ?"
        row = db.execute(f"SELECT 1 FROM scenes WHERE {clause}", params).fetchone()
        if row is None:
            return False
    if version_id and _table_exists(db, "scene_versions"):
        params = (version_id, session_id) if session_id else (version_id,)
        clause = "id = ? AND session_id = ?" if session_id else "id = ?"
        row = db.execute(f"SELECT 1 FROM scene_versions WHERE {clause}", params).fetchone()
        if row is None:
            return False
    return True


def collect_active_generated_file_reference_keys(db) -> set[str]:
    references: list[str] = []
    if _table_exists(db, "generated_images"):
        rows = db.execute(
            """
            SELECT image_path, image_url
            FROM generated_images
            WHERE COALESCE(image_path, '') != '' OR COALESCE(image_url, '') != ''
            """
        ).fetchall()
        for row in rows:
            references.extend([row["image_path"], row["image_url"]])

    for manifest_path, manifest in iter_tts_manifests():
        if manifest_has_active_db_reference(db, manifest):
            references.extend(audio_references_from_manifest(manifest, manifest_path))

    keys: set[str] = set()
    for reference in references:
        path, _reason, _media_type = safe_generated_file_path(reference)
        if path is not None:
            keys.add(canonical_path(path))
    return keys


def scan_generated_files() -> tuple[list[dict[str, Any]], list[str]]:
    files: list[dict[str, Any]] = []
    skipped: list[str] = []
    for media_type, root in generated_roots().items():
        root.mkdir(parents=True, exist_ok=True)
        for path in root.rglob("*"):
            safe_path, reason, resolved_media_type = safe_generated_file_path(path, media_type)
            if safe_path is None:
                skipped.append(f"{path} - {reason}")
                continue
            if path.is_symlink():
                skipped.append(f"{path} - symlink skipped")
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                skipped.append(f"{path} - not a regular file")
                continue
            if safe_path.name in GENERATED_PLACEHOLDER_FILENAMES:
                skipped.append(f"{safe_path} - generated-folder placeholder skipped")
                continue
            if safe_path.suffix == PENDING_SUFFIX:
                skipped.append(f"{safe_path} - pending synthesis marker skipped")
                continue
            files.append(generated_file_record(safe_path, resolved_media_type or media_type))
    return files, skipped


def find_orphaned_generated_files(db) -> dict[str, Any]:
    referenced_keys = collect_active_generated_file_reference_keys(db)
    scanned_files, skipped = scan_generated_files()
    orphaned = [record for record in scanned_files if canonical_path(record["path"]) not in referenced_keys]
    total_size = sum(int(record.get("size_bytes") or 0) for record in orphaned)
    return {
        "orphaned_files": orphaned,
        "orphaned_count": len(orphaned),
        "orphaned_bytes": total_size,
        "referenced_count": len(referenced_keys),
        "scanned_count": len(scanned_files),
        "skipped": skipped,
    }
