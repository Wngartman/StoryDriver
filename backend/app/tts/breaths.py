from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import DATA_DIR
from app.database import db_session


VOICE_ROOT = DATA_DIR / "voices"
BREATH_DIR = VOICE_ROOT / "breaths"
TEMP_DIR = VOICE_ROOT / "temp"
GENERATED_AUDIO_DIR = DATA_DIR / "generated_audio"
QWEN_PYTHON = Path(r"D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe")
AUDIO_TOOLS = Path(r"D:\StoryDriver\tts_engines\qwen3_tts\service\audio_tools.py")
ALLOWED_EXTENSIONS = {".wav", ".flac", ".mp3"}
BREATH_TYPES = (
    "soft_inhale",
    "normal_inhale",
    "shaky_inhale",
    "quiet_exhale",
    "recovering_breath",
    "gasp",
)
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def ensure_breath_dirs() -> None:
    for path in (VOICE_ROOT, BREATH_DIR, TEMP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _validate_breath_type(value: str) -> str:
    breath_type = str(value or "").strip().lower()
    if breath_type not in BREATH_TYPES:
        raise ValueError("Unknown custom breath type.")
    return breath_type


def _json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _breath_row(row: Any) -> dict[str, Any]:
    validation = _json(row["validation_json"])
    validation.pop("path", None)
    return {
        "voice_id": row["voice_id"],
        "breath_type": row["breath_type"],
        "checksum": row["checksum"],
        "revision": int(row["revision"] or 1),
        "duration_seconds": float(row["duration_seconds"] or 0.0),
        "sample_rate": int(row["sample_rate"] or 24_000),
        "channels": int(row["channels"] or 1),
        "normalization_version": row["normalization_version"],
        "authorization_confirmed": bool(row["authorization_confirmed"]),
        "isolated_breath_confirmed": bool(row["isolated_breath_confirmed"]),
        "validation": validation,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "audio_url": f"/tts/custom-voices/{row['voice_id']}/breaths/{row['breath_type']}/audio",
    }


def list_custom_voice_breaths(voice_id: str) -> dict[str, dict[str, Any]]:
    with db_session() as db:
        rows = db.execute(
            "SELECT * FROM custom_voice_breaths WHERE voice_id = ? ORDER BY breath_type",
            (voice_id,),
        ).fetchall()
    return {row["breath_type"]: _breath_row(row) for row in rows}


def get_custom_voice_breath(voice_id: str, breath_type: str) -> dict[str, Any] | None:
    resolved_type = _validate_breath_type(breath_type)
    with db_session() as db:
        row = db.execute(
            "SELECT * FROM custom_voice_breaths WHERE voice_id = ? AND breath_type = ?",
            (voice_id, resolved_type),
        ).fetchone()
    return _breath_row(row) if row else None


def custom_voice_breath_path(voice_id: str, breath_type: str) -> Path | None:
    resolved_type = _validate_breath_type(breath_type)
    with db_session() as db:
        row = db.execute(
            "SELECT audio_path FROM custom_voice_breaths WHERE voice_id = ? AND breath_type = ?",
            (voice_id, resolved_type),
        ).fetchone()
    if not row or not row["audio_path"]:
        return None
    path = Path(row["audio_path"])
    try:
        if BREATH_DIR.resolve() not in path.resolve().parents or not path.is_file():
            return None
    except OSError:
        return None
    return path


def _decode_audio(filename: str, encoded: str) -> tuple[bytes, str]:
    extension = Path(filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Breath audio must be WAV, FLAC, or MP3.")
    value = str(encoded or "").strip()
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ValueError("Breath audio is not valid base64 data.") from error
    if not decoded:
        raise ValueError("Breath audio is empty.")
    if len(decoded) > MAX_UPLOAD_BYTES:
        raise ValueError("Breath audio exceeds the 12 MB local upload limit.")
    return decoded, extension


def _normalization_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "TMP": str(DATA_DIR / "temp"),
            "TEMP": str(DATA_DIR / "temp"),
            "HF_HOME": str(Path(r"D:\StoryDriver\tts_engines\cache\huggingface")),
            "TRANSFORMERS_CACHE": str(Path(r"D:\StoryDriver\tts_engines\cache\transformers")),
            "TORCH_HOME": str(Path(r"D:\StoryDriver\tts_engines\cache\torch")),
            "XDG_CACHE_HOME": str(Path(r"D:\StoryDriver\tts_engines\cache")),
        }
    )
    return environment


def _normalize_breath(
    voice_id: str,
    breath_type: str,
    revision: int,
    filename: str,
    audio_base64: str,
) -> dict[str, Any]:
    ensure_breath_dirs()
    raw, extension = _decode_audio(filename, audio_base64)
    source = TEMP_DIR / f"{voice_id}_{breath_type}_{uuid4().hex}{extension}"
    destination = BREATH_DIR / f"{voice_id}_{breath_type}_r{revision}.wav"
    source.write_bytes(raw)
    try:
        completed = subprocess.run(
            [str(QWEN_PYTHON), str(AUDIO_TOOLS), str(source), str(destination), "--breath"],
            cwd=str(AUDIO_TOOLS.parent),
            env=_normalization_environment(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "Breath normalization failed.").strip()
            raise ValueError(message[-1000:])
        return json.loads(completed.stdout)
    finally:
        source.unlink(missing_ok=True)


def _invalidate_breath_cache(checksum: str) -> dict[str, int]:
    if not checksum:
        return {"chunks": 0, "files": 0}
    with db_session() as db:
        rows = db.execute(
            "SELECT cache_key, audio_path FROM narration_chunks WHERE breath_reference_checksum = ?",
            (checksum,),
        ).fetchall()
        db.execute("DELETE FROM narration_chunks WHERE breath_reference_checksum = ?", (checksum,))
    files: set[Path] = set()
    for row in rows:
        if row["audio_path"]:
            files.add(Path(row["audio_path"]))
        if row["cache_key"]:
            files.add(GENERATED_AUDIO_DIR / f"tts_manifest_{row['cache_key']}.json")
    removed = 0
    for path in files:
        try:
            if GENERATED_AUDIO_DIR.resolve() in path.resolve().parents and path.exists():
                path.unlink()
                removed += 1
        except OSError:
            continue
    return {"chunks": len(rows), "files": removed}


def save_custom_voice_breath(voice_id: str, breath_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    resolved_type = _validate_breath_type(breath_type)
    if not payload.get("authorization_confirmed"):
        raise ValueError("Confirm that you own this recording or have permission to use the voice.")
    if not payload.get("isolated_breath_confirmed"):
        raise ValueError("Confirm that the sample is one isolated breath with no speech, music, or reverb.")
    with db_session() as db:
        voice = db.execute("SELECT id FROM custom_voices WHERE id = ?", (voice_id,)).fetchone()
        current = db.execute(
            "SELECT * FROM custom_voice_breaths WHERE voice_id = ? AND breath_type = ?",
            (voice_id, resolved_type),
        ).fetchone()
    if not voice:
        raise ValueError("Custom voice was not found.")
    revision = int(current["revision"] or 0) + 1 if current else 1
    normalized = _normalize_breath(
        voice_id,
        resolved_type,
        revision,
        str(payload.get("filename") or ""),
        str(payload.get("audio_base64") or ""),
    )
    old_path = Path(current["audio_path"]) if current and current["audio_path"] else None
    old_checksum = str(current["checksum"] or "") if current else ""
    with db_session() as db:
        db.execute(
            """
            INSERT INTO custom_voice_breaths (
                voice_id, breath_type, audio_path, checksum, revision, duration_seconds,
                sample_rate, channels, normalization_version, authorization_confirmed,
                isolated_breath_confirmed, validation_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(voice_id, breath_type) DO UPDATE SET
                audio_path = excluded.audio_path,
                checksum = excluded.checksum,
                revision = excluded.revision,
                duration_seconds = excluded.duration_seconds,
                sample_rate = excluded.sample_rate,
                channels = excluded.channels,
                normalization_version = excluded.normalization_version,
                authorization_confirmed = 1,
                isolated_breath_confirmed = 1,
                validation_json = excluded.validation_json,
                updated_at = excluded.updated_at
            """,
            (
                voice_id,
                resolved_type,
                normalized["path"],
                normalized["checksum"],
                revision,
                normalized["duration_seconds"],
                normalized["sample_rate"],
                normalized["channels"],
                normalized["normalization_version"],
                json.dumps(normalized, separators=(",", ":")),
            ),
        )
    invalidated = _invalidate_breath_cache(old_checksum)
    if old_path and old_path != Path(normalized["path"]):
        try:
            if BREATH_DIR.resolve() in old_path.resolve().parents:
                old_path.unlink(missing_ok=True)
        except OSError:
            pass
    saved = get_custom_voice_breath(voice_id, resolved_type)
    if not saved:
        raise RuntimeError("Custom breath reference was not saved.")
    return {"breath": saved, "cache_invalidated": invalidated}


def delete_custom_voice_breath(voice_id: str, breath_type: str) -> dict[str, Any]:
    resolved_type = _validate_breath_type(breath_type)
    with db_session() as db:
        row = db.execute(
            "SELECT * FROM custom_voice_breaths WHERE voice_id = ? AND breath_type = ?",
            (voice_id, resolved_type),
        ).fetchone()
        if row:
            db.execute(
                "DELETE FROM custom_voice_breaths WHERE voice_id = ? AND breath_type = ?",
                (voice_id, resolved_type),
            )
    if not row:
        raise ValueError("Custom breath reference was not found.")
    invalidated = _invalidate_breath_cache(str(row["checksum"] or ""))
    path = Path(row["audio_path"])
    removed = False
    try:
        if BREATH_DIR.resolve() in path.resolve().parents:
            removed = path.exists()
            path.unlink(missing_ok=True)
    except OSError:
        removed = False
    return {
        "ok": True,
        "voice_id": voice_id,
        "breath_type": resolved_type,
        "reference_removed": removed,
        "cache_invalidated": invalidated,
    }


def breath_asset_for_event(voice_id: str | None, event: str | None) -> dict[str, Any] | None:
    if not voice_id or not event or event == "none":
        return None
    try:
        return get_custom_voice_breath(voice_id, event)
    except ValueError:
        return None
