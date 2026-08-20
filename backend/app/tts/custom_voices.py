from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import DATA_DIR
from app.database import db_session
from app.tts.breaths import BREATH_DIR, custom_voice_breath_path, ensure_breath_dirs, list_custom_voice_breaths
from app.tts.qwen import QwenTTSClient


VOICE_ROOT = DATA_DIR / "voices"
REFERENCE_DIR = VOICE_ROOT / "references"
PROMPT_DIR = VOICE_ROOT / "prompts"
PREVIEW_DIR = VOICE_ROOT / "previews"
TEMP_DIR = VOICE_ROOT / "temp"
QWEN_PYTHON = Path(r"D:\StoryDriver\tts_engines\qwen3_tts\venv\Scripts\python.exe")
AUDIO_TOOLS = Path(r"D:\StoryDriver\tts_engines\qwen3_tts\service\audio_tools.py")
ALLOWED_EXTENSIONS = {".wav", ".flac", ".mp3"}
STYLE_NAMES = ("normal", "soft", "whisper", "heightened")
STYLE_REFERENCE_FALLBACKS = {
    "normal": ("normal",),
    "soft": ("soft", "normal"),
    "whisper": ("whisper", "soft", "normal"),
    "heightened": ("heightened", "normal"),
    "distressed": ("soft", "normal"),
    "intimate": ("soft", "normal"),
    "somber": ("soft", "normal"),
    "tense": ("soft", "normal"),
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ensure_voice_dirs() -> None:
    for path in (VOICE_ROOT, REFERENCE_DIR, PROMPT_DIR, PREVIEW_DIR, TEMP_DIR):
        path.mkdir(parents=True, exist_ok=True)
    ensure_breath_dirs()


def _json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _voice_row(row: Any) -> dict[str, Any]:
    references = {
        style: {
            "path": row[f"{style}_reference_path"] or None,
            "transcript": row[f"{style}_transcript"] or "",
        }
        for style in STYLE_NAMES
        if row[f"{style}_reference_path"] or row[f"{style}_transcript"]
    }
    prompt_paths = _json(row["prompt_paths_json"], {})
    prompt_checksums = _json(row["prompt_checksums_json"], {})
    validation = _json(row["validation_json"], {})
    voice_id = row["id"]
    return {
        "id": voice_id,
        "display_name": row["display_name"],
        "provider": row["provider"],
        "model": row["model"],
        "language": row["language"],
        "authorization_confirmed": bool(row["authorization_confirmed"]),
        "dominant_speaker_confirmed": bool(row["dominant_speaker_confirmed"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "references": references,
        "normalized_audio_checksum": row["normalized_audio_checksum"],
        "cached_voice_prompt_path": row["cached_voice_prompt_path"] or None,
        "cached_voice_prompt_checksum": row["cached_voice_prompt_checksum"] or None,
        "prompt_paths": prompt_paths,
        "prompt_checksums": prompt_checksums,
        "revision": int(row["revision"] or 1),
        "preview_path": row["preview_path"] or None,
        "enabled": bool(row["enabled"]),
        "last_used": row["last_used"],
        "user_notes": row["user_notes"] or "",
        "validation_status": row["validation_status"],
        "validation": validation,
        "selection_id": f"custom:{row['id']}",
        "profile_id": f"custom_voice:{row['id']}",
        "breaths": list_custom_voice_breaths(voice_id),
    }


def list_custom_voices(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    ensure_voice_dirs()
    clause = "" if include_disabled else "WHERE enabled = 1"
    with db_session() as db:
        rows = db.execute(
            f"SELECT * FROM custom_voices {clause} ORDER BY lower(display_name), created_at"
        ).fetchall()
    return [_voice_row(row) for row in rows]


def get_custom_voice(voice_id: str) -> dict[str, Any] | None:
    with db_session() as db:
        row = db.execute("SELECT * FROM custom_voices WHERE id = ?", (voice_id,)).fetchone()
    return _voice_row(row) if row else None


def custom_voice_id_from_selection(voice: str | None, profile_id: str | None = None) -> str | None:
    for value, prefix in ((voice, "custom:"), (profile_id, "custom_voice:")):
        if value and value.startswith(prefix):
            candidate = value[len(prefix) :].strip()
            return candidate or None
    return None


def selected_custom_voice(voice: str | None, profile_id: str | None = None) -> dict[str, Any] | None:
    voice_id = custom_voice_id_from_selection(voice, profile_id)
    if not voice_id:
        return None
    record = get_custom_voice(voice_id)
    if not record or not record["enabled"]:
        return None
    return record


def _decode_audio(filename: str, encoded: str) -> tuple[bytes, str]:
    extension = Path(filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Reference audio must be WAV, FLAC, or MP3.")
    value = encoded.strip()
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ValueError("Reference audio is not valid base64 data.") from error
    if not decoded:
        raise ValueError("Reference audio is empty.")
    if len(decoded) > MAX_UPLOAD_BYTES:
        raise ValueError("Reference audio exceeds the 25 MB local upload limit.")
    return decoded, extension


def _normalize_reference(
    voice_id: str,
    style: str,
    revision: int,
    filename: str,
    audio_base64: str,
    transcript: str,
) -> dict[str, Any]:
    ensure_voice_dirs()
    raw, extension = _decode_audio(filename, audio_base64)
    source = TEMP_DIR / f"{voice_id}_{style}_{uuid4().hex}{extension}"
    destination = REFERENCE_DIR / f"{voice_id}_{style}_r{revision}.wav"
    source.write_bytes(raw)
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
    try:
        completed = subprocess.run(
            [
                str(QWEN_PYTHON),
                str(AUDIO_TOOLS),
                str(source),
                str(destination),
                "--transcript",
                transcript.strip(),
            ],
            cwd=str(AUDIO_TOOLS.parent),
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "Reference normalization failed.").strip()
            raise ValueError(message[-1000:])
        return json.loads(completed.stdout)
    finally:
        source.unlink(missing_ok=True)


def create_custom_voice(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("authorization_confirmed"):
        raise ValueError("Confirm that you own the recording or have permission to use the voice.")
    if not payload.get("dominant_speaker_confirmed"):
        raise ValueError("Confirm that every reference contains one dominant adult speaker.")
    display_name = str(payload.get("display_name") or "").strip()
    if not display_name:
        raise ValueError("Voice name is required.")
    normal = (payload.get("references") or {}).get("normal") or {}
    if not normal.get("audio_base64") or not str(normal.get("transcript") or "").strip():
        raise ValueError("A normal reference and exact transcript are required.")
    with db_session() as db:
        duplicate = db.execute(
            "SELECT id FROM custom_voices WHERE lower(display_name) = lower(?)",
            (display_name,),
        ).fetchone()
    if duplicate:
        raise ValueError("A custom voice with that name already exists.")
    voice_id = str(uuid4())
    revision = 1
    references: dict[str, dict[str, Any]] = {}
    validation: dict[str, Any] = {"styles": {}, "warnings": []}
    created_paths: list[Path] = []
    try:
        for style in STYLE_NAMES:
            item = (payload.get("references") or {}).get(style) or {}
            if not item.get("audio_base64"):
                continue
            transcript = str(item.get("transcript") or "").strip()
            if not transcript:
                raise ValueError(f"An exact transcript is required for the {style} reference.")
            normalized = _normalize_reference(
                voice_id,
                style,
                revision,
                str(item.get("filename") or ""),
                str(item["audio_base64"]),
                transcript,
            )
            created_paths.append(Path(normalized["path"]))
            references[style] = {"path": normalized["path"], "transcript": transcript}
            validation["styles"][style] = normalized
            validation["warnings"].extend(normalized.get("warnings") or [])
        normal_checksum = validation["styles"]["normal"]["checksum"]
        status = "ready_for_prompt" if not validation["warnings"] else "ready_for_prompt_with_warnings"
        now = utc_now()
        values: list[Any] = [
            voice_id,
            display_name,
            str(payload.get("language") or "English"),
            1,
            1,
        ]
        for style in STYLE_NAMES:
            values.extend(
                [
                    references.get(style, {}).get("path", ""),
                    references.get(style, {}).get("transcript", ""),
                ]
            )
        values.extend(
            [
                normal_checksum,
                revision,
                1,
                str(payload.get("user_notes") or "").strip(),
                status,
                json.dumps(validation, separators=(",", ":")),
                now,
                now,
            ]
        )
        with db_session() as db:
            db.execute(
                """
                INSERT INTO custom_voices (
                    id, display_name, language, authorization_confirmed, dominant_speaker_confirmed,
                    normal_reference_path, normal_transcript,
                    soft_reference_path, soft_transcript,
                    whisper_reference_path, whisper_transcript,
                    heightened_reference_path, heightened_transcript,
                    normalized_audio_checksum, revision, enabled, user_notes,
                    validation_status, validation_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        record = get_custom_voice(voice_id)
        if not record:
            raise RuntimeError("Custom voice was not saved.")
        return record
    except Exception:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise


async def build_custom_voice_prompts(voice_id: str) -> dict[str, Any]:
    record = get_custom_voice(voice_id)
    if not record:
        raise ValueError("Custom voice was not found.")
    requests = []
    for style, reference in record["references"].items():
        if not reference.get("path") or not reference.get("transcript"):
            continue
        output = PROMPT_DIR / f"{voice_id}_{style}_r{record['revision']}.pt"
        requests.append(
            {
                "style": style,
                "reference_path": reference["path"],
                "transcript": reference["transcript"],
                "output_path": str(output),
            }
        )
    if not requests:
        raise ValueError("No valid reference is available to build this voice.")
    result = await QwenTTSClient().build_voice_prompts(
        voice_id=voice_id,
        revision=record["revision"],
        prompts=requests,
    )
    prompt_paths = dict(result.get("prompt_paths") or {})
    prompt_checksums = dict(result.get("prompt_checksums") or {})
    normal_path = prompt_paths.get("normal") or ""
    normal_checksum = prompt_checksums.get("normal") or ""
    with db_session() as db:
        db.execute(
            """
            UPDATE custom_voices
            SET cached_voice_prompt_path = ?, cached_voice_prompt_checksum = ?,
                prompt_paths_json = ?, prompt_checksums_json = ?,
                validation_status = 'ready', updated_at = ?
            WHERE id = ?
            """,
            (
                normal_path,
                normal_checksum,
                json.dumps(prompt_paths, separators=(",", ":")),
                json.dumps(prompt_checksums, separators=(",", ":")),
                utc_now(),
                voice_id,
            ),
        )
    updated = get_custom_voice(voice_id)
    if not updated:
        raise RuntimeError("Custom voice disappeared after prompt creation.")
    return {"voice": updated, "prompt_build": result}


def update_custom_voice(voice_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    record = get_custom_voice(voice_id)
    if not record:
        raise ValueError("Custom voice was not found.")
    display_name = str(payload.get("display_name", record["display_name"])).strip()
    if not display_name:
        raise ValueError("Voice name is required.")
    with db_session() as db:
        duplicate = db.execute(
            "SELECT id FROM custom_voices WHERE lower(display_name) = lower(?) AND id != ?",
            (display_name, voice_id),
        ).fetchone()
        if duplicate:
            raise ValueError("A custom voice with that name already exists.")
        db.execute(
            """
            UPDATE custom_voices
            SET display_name = ?, language = ?, enabled = ?, user_notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                display_name,
                str(payload.get("language", record["language"])).strip() or "English",
                1 if payload.get("enabled", record["enabled"]) else 0,
                str(payload.get("user_notes", record["user_notes"])).strip(),
                utc_now(),
                voice_id,
            ),
        )
    updated = get_custom_voice(voice_id)
    if not updated:
        raise RuntimeError("Custom voice update failed.")
    return updated


def mark_custom_voice_used(voice_id: str) -> None:
    with db_session() as db:
        db.execute("UPDATE custom_voices SET last_used = ?, updated_at = ? WHERE id = ?", (utc_now(), utc_now(), voice_id))


def delete_custom_voice(voice_id: str, *, remove_generated_audio: bool = False) -> dict[str, Any]:
    record = get_custom_voice(voice_id)
    if not record:
        raise ValueError("Custom voice was not found.")
    files: set[Path] = set()
    for reference in record["references"].values():
        if reference.get("path"):
            files.add(Path(reference["path"]))
    for breath_type in record.get("breaths", {}):
        path = custom_voice_breath_path(voice_id, breath_type)
        if path:
            files.add(path)
    files.update(Path(path) for path in record["prompt_paths"].values() if path)
    if record.get("preview_path"):
        files.add(Path(record["preview_path"]))
    generated: list[Path] = []
    if remove_generated_audio:
        with db_session() as db:
            rows = db.execute(
                "SELECT audio_path FROM narration_chunks WHERE custom_voice_id = ?",
                (voice_id,),
            ).fetchall()
            generated = [Path(row["audio_path"]) for row in rows if row["audio_path"]]
            db.execute("DELETE FROM narration_chunks WHERE custom_voice_id = ?", (voice_id,))
        files.update(generated)
    with db_session() as db:
        db.execute("DELETE FROM custom_voices WHERE id = ?", (voice_id,))
    removed = []
    for path in files:
        try:
            resolved = path.resolve()
            if (
                VOICE_ROOT.resolve() in resolved.parents
                or BREATH_DIR.resolve() in resolved.parents
                or (remove_generated_audio and (DATA_DIR / "generated_audio").resolve() in resolved.parents)
            ):
                path.unlink(missing_ok=True)
                removed.append(str(path))
        except OSError:
            continue
    return {
        "ok": True,
        "voice_id": voice_id,
        "removed_files": removed,
        "generated_audio_removed": len(generated),
    }


def prompt_for_style(record: dict[str, Any], style: str) -> tuple[str, str, str]:
    candidates = STYLE_REFERENCE_FALLBACKS.get(style, ("normal",))
    effective_style = next((name for name in candidates if record["prompt_paths"].get(name)), "normal")
    prompt_path = record["prompt_paths"].get(effective_style)
    if not prompt_path:
        raise ValueError("This custom voice has no cached prompt. Process the voice again.")
    checksum = record["prompt_checksums"].get(effective_style) or ""
    return str(prompt_path), str(checksum), effective_style


def voice_cache_identity(record: dict[str, Any]) -> str:
    value = {
        "voice_id": record["id"],
        "revision": record["revision"],
        "normal_checksum": record["normalized_audio_checksum"],
        "prompts": record["prompt_checksums"],
        "breaths": {
            breath_type: {
                "checksum": breath.get("checksum"),
                "revision": breath.get("revision"),
            }
            for breath_type, breath in record.get("breaths", {}).items()
        },
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def custom_voice_profile(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["profile_id"],
        "display_name": record["display_name"],
        "provider": "high_quality_local",
        "voice_id": record["selection_id"],
        "model": record["model"],
        "speed": 1.0,
        "style_notes": "Local authorized-reference Qwen voice with conservative cue-based expression.",
        "recommended_use": "Long-form local narration.",
        "enabled": bool(record["enabled"]),
        "available": record["validation_status"] == "ready" and bool(record["cached_voice_prompt_path"]),
        "default": False,
        "fallback_provider": "kokoro",
        "quality_mode": "premium",
        "chunking_profile": "natural",
        "narration_pacing": "natural",
        "dialogue_pause_strength": "medium",
        "paragraph_pause_strength": "medium",
        "dialogue_narration_style": "neutral",
        "voice_preferences": [],
        "pronunciation_profile": "global_story_aliases",
        "authorized_reference_metadata": {
            "authorization_confirmed": bool(record["authorization_confirmed"]),
            "dominant_speaker_confirmed": bool(record["dominant_speaker_confirmed"]),
            "reference_checksum": record["normalized_audio_checksum"],
            "revision": record["revision"],
        },
        "unavailable_reason": "" if record["validation_status"] == "ready" else "Voice prompt processing is incomplete.",
        "benchmark_report": None,
    }
