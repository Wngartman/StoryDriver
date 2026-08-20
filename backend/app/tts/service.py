from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import logging
import os
import re
from time import perf_counter, time
from typing import Any

from app.config import DATA_DIR, KOKORO_START_COMMAND, KOKORO_WORKING_DIR
from app.database import db_session
from app.schemas import TTSSynthesizeRequest, TTSSynthesizeResponse, TTSPreviewRequest, TTSPreviewResponse
from app.tts.kokoro import KokoroClient
from app.settings.store import load_tts_settings
from app.memory.foundation import foundation_pronunciation_entries
from app.tts.profiles import (
    NORMALIZATION_VERSION,
    PREVIEW_SAMPLE_TEXT,
    build_voice_profiles,
    female_voice_ids,
    normalize_tts_text,
    profile_by_id,
    pronunciation_dictionary_version,
)
from app.tts.registry import (
    PROVIDER_BROWSER,
    PROVIDER_HIGH_QUALITY_LOCAL,
    PROVIDER_KOKORO,
    append_high_quality_integration_report,
    fallback_for_requested_provider,
    premium_female_narrator_profile,
    provider_snapshot,
)
from app.tts.qwen import QwenTTSClient
from app.tts.breaths import breath_asset_for_event
from app.tts.custom_voices import (
    custom_voice_profile,
    custom_voice_id_from_selection,
    list_custom_voices,
    mark_custom_voice_used,
    prompt_for_style,
    selected_custom_voice,
    voice_cache_identity,
)
from app.tts.prosody import narration_direction
from app.diagnostics.runtime import diagnostic_logging_enabled


logger = logging.getLogger("storydriver.tts")


def pronunciation_entry_dict(entry: Any) -> dict[str, Any]:
    if hasattr(entry, "model_dump"):
        return entry.model_dump()
    if isinstance(entry, dict):
        return dict(entry)
    return {}


CONTENT_TYPE_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
}

LAST_TTS_EVENT: dict = {
    "provider": None,
    "voice": None,
    "audio_path": None,
    "error": None,
    "cached": False,
    "synthesis_seconds": None,
    "cache_key": None,
    "status": "ready",
    "text_chars": None,
    "seconds_per_1000_chars": None,
    "chunk_index": None,
    "chunk_count": None,
    "narration_job_id": None,
}

TTS_LATENCY_REPORT_PATH = DATA_DIR / "logs" / "TTS_LATENCY_REPORT.md"
KOKORO_RESILIENCE_REPORT_PATH = DATA_DIR / "logs" / "KOKORO_RESILIENCE_REPORT.md"
SUPPORTED_SPEECH_OPTIONS_CACHE: dict[str, tuple[float, set[str]]] = {}
SUPPORTED_SPEECH_OPTIONS_TTL_SECONDS = 300.0
CACHE_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".webm"}
PRIVATE_TEXT_FIELDS = {"text", "input", "generated_text", "scene_text", "normalized_tts_text"}
MANIFEST_PRIVACY_SCRUBBED = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:32]


def without_full_text(details: dict[str, Any]) -> dict[str, Any]:
    safe = dict(details or {})
    for key in PRIVATE_TEXT_FIELDS:
        value = safe.pop(key, None)
        if isinstance(value, str) and value:
            safe.setdefault(f"{key}_hash", text_hash(value))
    return safe


def safe_round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def seconds_per_1000_chars(seconds: float | None, char_count: int) -> float | None:
    if seconds is None or char_count <= 0:
        return None
    return round(seconds / max(char_count / 1000, 0.001), 3)


def tts_cache_usage(audio_dir: Path) -> dict[str, int]:
    files = [
        path
        for path in audio_dir.glob("*")
        if path.is_file()
        and (path.name.startswith("kokoro_") or path.name.startswith("tts_manifest_"))
        and (path.suffix.lower() in CACHE_AUDIO_EXTENSIONS or path.name.startswith("tts_manifest_"))
    ]
    return {"bytes": sum(path.stat().st_size for path in files), "files": len(files)}


def enforce_tts_cache_limit(
    audio_dir: Path,
    max_bytes: int,
    *,
    protected_cache_keys: set[str] | None = None,
) -> dict[str, int]:
    protected = protected_cache_keys or set()
    groups: dict[str, list[Path]] = {}
    for path in audio_dir.glob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("kokoro_") and path.suffix.lower() in CACHE_AUDIO_EXTENSIONS:
            cache_key = path.stem.removeprefix("kokoro_")
        elif path.name.startswith("tts_manifest_") and path.suffix.lower() == ".json":
            cache_key = path.stem.removeprefix("tts_manifest_")
        else:
            continue
        groups.setdefault(cache_key, []).append(path)

    total_bytes = sum(path.stat().st_size for paths in groups.values() for path in paths)
    deleted_bytes = 0
    deleted_files = 0
    if total_bytes <= max_bytes:
        return {"bytes": total_bytes, "files_deleted": 0, "bytes_deleted": 0}

    ordered = sorted(
        groups.items(),
        key=lambda item: max(path.stat().st_mtime for path in item[1]),
    )
    for cache_key, paths in ordered:
        if total_bytes <= max_bytes:
            break
        if cache_key in protected:
            continue
        for path in paths:
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            total_bytes -= size
            deleted_bytes += size
            deleted_files += 1
    if deleted_files:
        logger.info(
            "TTS cache LRU cleanup files_deleted=%s bytes_deleted=%s bytes_remaining=%s",
            deleted_files,
            deleted_bytes,
            total_bytes,
        )
    return {
        "bytes": total_bytes,
        "files_deleted": deleted_files,
        "bytes_deleted": deleted_bytes,
    }


def split_text_for_tts_chunks(text: str, max_chars: int = 1200, profile: str = "natural") -> list[str]:
    target = max(400, min(6000, int(max_chars or 1200)))
    if profile == "fast":
        target = min(target, 900)
    elif profile == "audiobook":
        target = min(max(target, 1500), 2200)
    normalized = (text or "").replace("\r", "").strip()
    if not normalized:
        return []
    pieces = [
        piece.strip()
        for piece in re.split(r"(?<=[.!?])\s+|\n{2,}", normalized)
        if piece and piece.strip()
    ]
    if not pieces:
        return [normalized]
    groups: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
            continue
        candidate = f"{current} {piece}"
        crosses_dialogue = current.endswith(("'", '"')) and re.match(r"^[A-Z][a-z]+ (said|asked|whispered|murmured|replied|answered|breathed|hissed|snapped)\b", piece or "")
        if len(candidate) <= target or (crosses_dialogue and len(candidate) <= target + 220):
            current = candidate
            continue
        groups.append(current)
        current = piece
    if current:
        groups.append(current)
    return groups or [normalized]


def append_tts_latency_report(title: str, details: dict[str, Any]) -> None:
    if not diagnostic_logging_enabled():
        return
    try:
        TTS_LATENCY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not TTS_LATENCY_REPORT_PATH.exists():
            TTS_LATENCY_REPORT_PATH.write_text(
                "# TTS Latency Report\n\n"
                "Local Kokoro narration timing log. Story text is not written here; only sizes, hashes, cache status, and timings are recorded.\n",
                encoding="utf-8",
            )
        lines = [f"\n## {utc_now()} - {title}"]
        for key, value in without_full_text(details).items():
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
            else:
                rendered = str(value)
            lines.append(f"- {key}: {rendered}")
        with TTS_LATENCY_REPORT_PATH.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        logger.debug("Could not append TTS latency report", exc_info=True)


def append_kokoro_resilience_report(title: str, details: dict[str, Any]) -> None:
    if not diagnostic_logging_enabled():
        return
    safe_details = without_full_text(details)
    try:
        KOKORO_RESILIENCE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not KOKORO_RESILIENCE_REPORT_PATH.exists():
            KOKORO_RESILIENCE_REPORT_PATH.write_text(
                "# Kokoro Resilience Report\n\n"
                "Local narration reliability log. Story text is not written here; only IDs, chunk metadata, timing, health, and error details are recorded.\n",
                encoding="utf-8",
            )
        lines = [f"\n## {utc_now()} - {title}"]
        for key, value in safe_details.items():
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
            else:
                rendered = str(value)
            lines.append(f"- {key}: {rendered}")
        with KOKORO_RESILIENCE_REPORT_PATH.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        logger.debug("Could not append Kokoro resilience report", exc_info=True)


def write_tts_manifest(cache_key: str, details: dict[str, Any]) -> None:
    try:
        manifest_path = DATA_DIR / "generated_audio" / f"tts_manifest_{cache_key}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        pending = manifest_path.with_suffix(".pending.json")
        pending.write_text(json.dumps(without_full_text(details), indent=2, sort_keys=True), encoding="utf-8")
        pending.replace(manifest_path)
    except OSError:
        logger.debug("Could not write TTS manifest cache_key=%s", cache_key, exc_info=True)


def scrub_legacy_tts_manifests(audio_dir: Path) -> int:
    global MANIFEST_PRIVACY_SCRUBBED
    if MANIFEST_PRIVACY_SCRUBBED:
        return 0
    scrubbed = 0
    for path in audio_dir.glob("tts_manifest_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not PRIVATE_TEXT_FIELDS.intersection(payload):
                continue
            pending = path.with_suffix(".privacy.pending.json")
            pending.write_text(json.dumps(without_full_text(payload), indent=2, sort_keys=True), encoding="utf-8")
            pending.replace(path)
            scrubbed += 1
        except (OSError, ValueError):
            logger.warning("Could not privacy-scrub TTS manifest path=%s", path, exc_info=True)
    MANIFEST_PRIVACY_SCRUBBED = True
    if scrubbed:
        logger.info("Privacy-scrubbed legacy TTS manifests count=%s", scrubbed)
    return scrubbed


async def supported_speech_options(client: KokoroClient, force_refresh: bool = False) -> set[str]:
    cache_key = client.speech_endpoint
    now = time()
    cached = SUPPORTED_SPEECH_OPTIONS_CACHE.get(cache_key)
    if cached and not force_refresh and now - cached[0] < SUPPORTED_SPEECH_OPTIONS_TTL_SECONDS:
        return set(cached[1])
    options = set(await client.supported_speech_options())
    SUPPORTED_SPEECH_OPTIONS_CACHE[cache_key] = (now, options)
    return set(options)


def normalize_cache_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: normalize_cache_value(value[key]) for key in sorted(value)}
    return value


def kokoro_cache_key(
    *,
    text: str,
    voice: str | None,
    speed: float,
    base_url: str,
    extra_options: dict[str, Any],
    cache_metadata: dict[str, Any] | None = None,
) -> str:
    payload = {
        "provider": "kokoro",
        "text": text.strip(),
        "voice": voice or "af_heart",
        "speed": normalize_cache_value(float(speed or 1.0)),
        "base_url": base_url.rstrip("/"),
        "extra_options": normalize_cache_value(extra_options),
        "cache_metadata": normalize_cache_value(cache_metadata or {}),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def direction_for_payload(payload: TTSSynthesizeRequest) -> dict[str, Any]:
    return narration_direction(
        payload.text,
        paragraph_break_after=payload.paragraph_break_after,
        scene_break_after=payload.scene_break_after,
        speaker_change_after=payload.speaker_change_after,
        breathing_mode=payload.breathing_mode,
    )


def breath_event_for_direction(direction: dict[str, Any]) -> str | None:
    return direction.get("breath_before") or direction.get("breath_after") or None


def resolved_breath_metadata(
    custom_voice: dict[str, Any] | None,
    direction: dict[str, Any],
) -> dict[str, Any]:
    event = breath_event_for_direction(direction)
    asset = breath_asset_for_event(custom_voice.get("id") if custom_voice else None, event)
    return {
        "breathing_mode": direction.get("breathing_mode") or "natural",
        "breath_before": direction.get("breath_before"),
        "breath_after": direction.get("breath_after"),
        "breath_confidence": float(direction.get("breath_confidence") or 0.0),
        "breath_source_cue": direction.get("breath_source_cue") or "",
        "breath_reference_used": bool(asset),
        "breath_reference_checksum": asset.get("checksum") if asset else None,
        "breath_reference_revision": asset.get("revision") if asset else None,
        "breath_audio_url": asset.get("audio_url") if asset else None,
        "breath_duration": asset.get("duration_seconds") if asset else None,
    }


def persist_narration_response(payload: TTSSynthesizeRequest, response: TTSSynthesizeResponse) -> None:
    job_id = payload.narration_job_id
    chunk_index = payload.chunk_index
    if not job_id or chunk_index is None:
        return
    requested_provider = response.requested_provider or payload.provider
    requested_profile = response.requested_profile or payload.voice_profile_id or ""
    requested_voice = response.requested_voice or payload.voice or ""
    effective_provider = response.effective_provider or response.provider
    effective_profile = response.effective_profile or response.voice_profile_id or ""
    effective_voice = response.effective_voice or ""
    audio_path = ""
    if response.audio_url:
        audio_path = str(DATA_DIR / "generated_audio" / Path(response.audio_url).name)
    chunk_id = hashlib.sha256(f"{job_id}:{chunk_index}".encode("utf-8")).hexdigest()[:32]
    prose_checksum = payload.text_hash or text_hash(payload.normalized_tts_text or payload.text)
    prosody_revision = hashlib.sha256(
        json.dumps(
            {
                "pacing": payload.narration_pacing,
                "dialogue_pause": payload.dialogue_pause_strength,
                "paragraph_pause": payload.paragraph_pause_strength,
                "dialogue_style": payload.dialogue_narration_style,
                "style": payload.style,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    breathing_revision = f"{payload.breathing_mode or 'natural'}:{response.breath_reference_revision or 0}"
    try:
        with db_session() as db:
            if payload.session_id or payload.scene_id or payload.version_id:
                target = db.execute(
                    """
                    SELECT 1
                    FROM scene_versions sv
                    JOIN scenes s ON s.id = sv.scene_id
                    WHERE sv.session_id = ? AND s.session_id = ?
                      AND s.id = ? AND sv.id = ?
                    """,
                    (payload.session_id, payload.session_id, payload.scene_id, payload.version_id),
                ).fetchone()
                if target is None:
                    logger.warning("Discarded narration metadata for an invalid story/scene/version target.")
                    return
            existing_job = db.execute(
                "SELECT session_id, scene_id, version_id FROM narration_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if existing_job and (
                existing_job["session_id"] != payload.session_id
                or existing_job["scene_id"] != payload.scene_id
                or existing_job["version_id"] != payload.version_id
            ):
                logger.warning("Discarded narration metadata for a reused cross-story job ID.")
                return
            db.execute(
                """
                INSERT INTO narration_jobs (
                    id, session_id, scene_id, version_id, provider, voice_profile_id,
                    requested_provider, requested_profile, requested_voice,
                    effective_provider, effective_profile, effective_voice,
                    effective_model, fallback_used, fallback_reason, prose_checksum,
                    pronunciation_revision, prosody_revision, breathing_revision,
                    custom_voice_revision, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider = excluded.provider,
                    voice_profile_id = excluded.voice_profile_id,
                    requested_provider = excluded.requested_provider,
                    requested_profile = excluded.requested_profile,
                    requested_voice = excluded.requested_voice,
                    effective_provider = excluded.effective_provider,
                    effective_profile = excluded.effective_profile,
                    effective_voice = excluded.effective_voice,
                    effective_model = excluded.effective_model,
                    fallback_used = MAX(narration_jobs.fallback_used, excluded.fallback_used),
                    fallback_reason = CASE WHEN excluded.fallback_reason != '' THEN excluded.fallback_reason ELSE narration_jobs.fallback_reason END,
                    prose_checksum = excluded.prose_checksum,
                    pronunciation_revision = excluded.pronunciation_revision,
                    prosody_revision = excluded.prosody_revision,
                    breathing_revision = excluded.breathing_revision,
                    custom_voice_revision = excluded.custom_voice_revision,
                    status = 'ready',
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    payload.session_id,
                    payload.scene_id,
                    payload.version_id,
                    effective_provider,
                    effective_profile,
                    requested_provider,
                    requested_profile,
                    requested_voice,
                    effective_provider,
                    effective_profile,
                    effective_voice,
                    response.effective_model or "",
                    1 if response.fallback_used else 0,
                    response.fallback_reason or "",
                    prose_checksum,
                    payload.pronunciation_dictionary_version or "",
                    prosody_revision,
                    breathing_revision,
                    response.voice_prompt_revision,
                    utc_now(),
                ),
            )
            db.execute(
                """
                INSERT INTO narration_chunks (
                    id, job_id, chunk_index, text_hash, cache_key, audio_path,
                    duration_seconds, synthesis_seconds, text_start_offset, text_end_offset,
                    requested_provider, requested_profile, requested_voice,
                    effective_provider, effective_profile, effective_voice,
                    fallback_used, fallback_reason, custom_voice_id,
                    voice_prompt_revision, style_reference, narration_style,
                    narration_emotion, narration_intensity, narration_intensity_bucket,
                    pause_before_ms, pause_after_ms, source_cue, generation_instruction,
                    breathing_mode, breath_before, breath_after, breath_confidence,
                    breath_source_cue, breath_reference_checksum, breath_reference_revision,
                    breath_audio_path, breath_duration_seconds, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready')
                ON CONFLICT(job_id, chunk_index) DO UPDATE SET
                    text_hash = excluded.text_hash,
                    cache_key = excluded.cache_key,
                    audio_path = excluded.audio_path,
                    duration_seconds = excluded.duration_seconds,
                    synthesis_seconds = excluded.synthesis_seconds,
                    requested_provider = excluded.requested_provider,
                    requested_profile = excluded.requested_profile,
                    requested_voice = excluded.requested_voice,
                    effective_provider = excluded.effective_provider,
                    effective_profile = excluded.effective_profile,
                    effective_voice = excluded.effective_voice,
                    fallback_used = excluded.fallback_used,
                    fallback_reason = excluded.fallback_reason,
                    custom_voice_id = excluded.custom_voice_id,
                    voice_prompt_revision = excluded.voice_prompt_revision,
                    style_reference = excluded.style_reference,
                    narration_style = excluded.narration_style,
                    narration_emotion = excluded.narration_emotion,
                    narration_intensity = excluded.narration_intensity,
                    narration_intensity_bucket = excluded.narration_intensity_bucket,
                    pause_before_ms = excluded.pause_before_ms,
                    pause_after_ms = excluded.pause_after_ms,
                    source_cue = excluded.source_cue,
                    generation_instruction = excluded.generation_instruction,
                    breathing_mode = excluded.breathing_mode,
                    breath_before = excluded.breath_before,
                    breath_after = excluded.breath_after,
                    breath_confidence = excluded.breath_confidence,
                    breath_source_cue = excluded.breath_source_cue,
                    breath_reference_checksum = excluded.breath_reference_checksum,
                    breath_reference_revision = excluded.breath_reference_revision,
                    breath_audio_path = excluded.breath_audio_path,
                    breath_duration_seconds = excluded.breath_duration_seconds,
                    status = 'ready'
                """,
                (
                    chunk_id,
                    job_id,
                    chunk_index,
                    prose_checksum,
                    response.cache_key or "",
                    audio_path,
                    response.duration,
                    response.synthesis_seconds,
                    payload.text_start_offset,
                    payload.text_end_offset,
                    requested_provider,
                    requested_profile,
                    requested_voice,
                    effective_provider,
                    effective_profile,
                    effective_voice,
                    1 if response.fallback_used else 0,
                    response.fallback_reason or "",
                    response.custom_voice_id or "",
                    response.voice_prompt_revision,
                    response.style_reference or "normal",
                    response.narration_style or "normal",
                    response.narration_emotion or "neutral",
                    response.narration_intensity if response.narration_intensity is not None else 0.25,
                    response.narration_intensity_bucket or "low",
                    response.pause_before_ms or 0,
                    response.pause_after_ms or 0,
                    response.source_cue or "",
                    response.generation_instruction or "",
                    response.breathing_mode or "natural",
                    response.breath_before or "",
                    response.breath_after or "",
                    response.breath_confidence or 0.0,
                    response.breath_source_cue or "",
                    response.breath_reference_checksum or "",
                    response.breath_reference_revision,
                    response.breath_audio_url or "",
                    response.breath_duration or 0.0,
                ),
            )
    except Exception:
        logger.warning("Could not persist narration metadata job=%s chunk=%s", job_id, chunk_index, exc_info=True)


def finalize_narration_response(
    payload: TTSSynthesizeRequest,
    response: TTSSynthesizeResponse,
) -> TTSSynthesizeResponse:
    direction = direction_for_payload(payload)
    breath = resolved_breath_metadata(None, direction)
    completed = response.model_copy(
        update={
            "requested_provider": response.requested_provider or payload.provider,
            "requested_profile": response.requested_profile or payload.voice_profile_id,
            "requested_voice": response.requested_voice or payload.voice,
            "effective_provider": response.effective_provider or response.provider,
            "effective_profile": response.effective_profile or response.voice_profile_id,
            "effective_voice": response.effective_voice or payload.voice,
            "fallback_used": response.fallback_used or bool(response.fallback_reason),
            "style_reference": response.style_reference or "normal",
            "narration_style": response.narration_style or direction["style"],
            "narration_emotion": response.narration_emotion or direction["emotion"],
            "narration_intensity": (
                response.narration_intensity
                if response.narration_intensity is not None
                else direction["intensity"]
            ),
            "narration_intensity_bucket": response.narration_intensity_bucket or direction["intensity_bucket"],
            "pause_before_ms": (
                response.pause_before_ms if response.pause_before_ms is not None else direction["pause_before_ms"]
            ),
            "pause_after_ms": (
                min(
                    1500,
                    max(
                        0,
                        response.pause_after_ms
                        if response.pause_after_ms is not None
                        else direction["pause_after_ms"],
                    ),
                )
            ),
            "source_cue": response.source_cue if response.source_cue is not None else direction["source_cue"],
            # The installed Qwen 0.6B Base and CustomVoice APIs do not accept
            # an instruction for these generation paths. Keep this effective
            # value empty rather than claiming unsupported expression control.
            "generation_instruction": response.generation_instruction or "",
            "breathing_mode": response.breathing_mode or breath["breathing_mode"],
            "breath_before": response.breath_before if response.breath_before is not None else breath["breath_before"],
            "breath_after": response.breath_after if response.breath_after is not None else breath["breath_after"],
            "breath_confidence": (
                response.breath_confidence
                if response.breath_confidence is not None
                else breath["breath_confidence"]
            ),
            "breath_source_cue": (
                response.breath_source_cue
                if response.breath_source_cue is not None
                else breath["breath_source_cue"]
            ),
        }
    )
    LAST_TTS_EVENT.update(
        {
            "narration_style": completed.narration_style,
            "narration_emotion": completed.narration_emotion,
            "narration_intensity_bucket": completed.narration_intensity_bucket,
            "pause_after_ms": completed.pause_after_ms,
            "source_cue": completed.source_cue or "",
            "style_reference": completed.style_reference or "normal",
            "custom_voice_id": completed.custom_voice_id,
            "breathing_mode": completed.breathing_mode,
            "breath_event": completed.breath_before or completed.breath_after,
            "breath_confidence": completed.breath_confidence,
            "breath_reference_used": completed.breath_reference_used,
        }
    )
    persist_narration_response(payload, completed)
    return completed


class TTSService:
    def __init__(self, audio_dir: Path | None = None) -> None:
        self.audio_dir = audio_dir or DATA_DIR / "generated_audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        scrub_legacy_tts_manifests(self.audio_dir)

    async def synthesize_high_quality_batch(
        self,
        payloads: list[TTSSynthesizeRequest],
    ) -> list[TTSSynthesizeResponse]:
        if not payloads or len(payloads) > 4:
            raise ValueError("Premium narration batches must contain one to four chunks.")
        settings = load_tts_settings()
        base_pronunciation_entries = [
            entry
            for entry in (pronunciation_entry_dict(item) for item in (settings.pronunciation_entries or []))
            if entry
        ]
        qwen_requests: list[dict[str, Any]] = []
        normalized: list[dict[str, Any]] = []
        qwen_voices = {"Vivian", "Serena", "Ono_Anna", "Sohee"}
        custom_voice_ids: set[str] = set()
        for payload in payloads:
            foundation_entries = foundation_pronunciation_entries(payload.session_id) if payload.session_id else []
            active_settings = settings.model_copy(
                update={
                    "pronunciation_entries": [*base_pronunciation_entries, *foundation_entries],
                    **{
                        key: value
                        for key, value in {
                            "narration_pacing": payload.narration_pacing,
                            "dialogue_pause_strength": payload.dialogue_pause_strength,
                            "paragraph_pause_strength": payload.paragraph_pause_strength,
                            "dialogue_narration_style": payload.dialogue_narration_style,
                            "tts_chunking_profile": payload.tts_chunking_profile,
                        }.items()
                        if value is not None
                    },
                }
            )
            custom_voice = selected_custom_voice(payload.voice, payload.voice_profile_id)
            requested_custom_voice_id = custom_voice_id_from_selection(payload.voice, payload.voice_profile_id)
            if requested_custom_voice_id and not custom_voice:
                raise ValueError("The selected custom voice is missing, disabled, or not ready.")
            if custom_voice:
                custom_voice_ids.add(custom_voice["id"])
            if len(custom_voice_ids) > 1:
                raise ValueError("A premium batch cannot mix different custom voices.")
            profile = (
                {
                    **premium_female_narrator_profile(),
                    "id": custom_voice["profile_id"],
                    "display_name": custom_voice["display_name"],
                    "voice_id": custom_voice["selection_id"],
                    "model": custom_voice["model"],
                }
                if custom_voice
                else premium_female_narrator_profile()
            )
            normalization = normalize_tts_text(
                payload.text.strip(),
                active_settings,
                profile,
                story_id=payload.session_id,
            )
            normalized_text = normalization["text"] or payload.text.strip()
            direction = direction_for_payload(payload)
            breath = resolved_breath_metadata(custom_voice, direction)
            # Qwen 0.6B Base can vary cloned delivery through distinct reference
            # prompts. The 0.6B built-in model explicitly ignores instructions,
            # so do not claim an expression change that it cannot apply.
            style_reference = str(direction["style"]) if custom_voice else "normal"
            voice = custom_voice["display_name"] if custom_voice else payload.voice if payload.voice in qwen_voices else "Serena"
            prompt_path = ""
            prompt_checksum = ""
            if custom_voice:
                prompt_path, prompt_checksum, style_reference = prompt_for_style(custom_voice, style_reference)
            request_id = f"{payload.narration_job_id or 'premium'}:{payload.chunk_index or 0}"
            qwen_requests.append(
                {
                    "text": normalized_text,
                    "voice": voice,
                    "language": "English",
                    "speed": payload.speed,
                    "max_new_tokens": 384,
                    "request_id": request_id[:120],
                    "profile_id": profile["id"],
                    "custom_voice_id": custom_voice["id"] if custom_voice else None,
                    "voice_prompt_path": prompt_path or None,
                    "voice_prompt_checksum": prompt_checksum or None,
                    "voice_prompt_revision": custom_voice["revision"] if custom_voice else None,
                    "style_reference": style_reference,
                    "style_classification": direction["style"],
                    "intensity_bucket": direction["intensity_bucket"],
                    "generation_instruction": "",
                    "pronunciation_revision": normalization["pronunciation_dictionary_version"],
                    "normalization_version": NORMALIZATION_VERSION,
                    "breathing_mode": breath["breathing_mode"],
                    "breath_type": breath_event_for_direction(direction),
                    "breath_sample_checksum": breath["breath_reference_checksum"],
                    "breath_sample_revision": breath["breath_reference_revision"],
                }
            )
            normalized.append(
                {
                    "text": normalized_text,
                    "voice": voice,
                    "normalization": normalization,
                    "pronunciation_version": normalization["pronunciation_dictionary_version"],
                    "profile_id": profile["id"],
                    "custom_voice": custom_voice,
                    "style_reference": style_reference,
                    "direction": direction,
                    "generation_instruction": "",
                    "breath": breath,
                }
            )
        started = perf_counter()
        try:
            batch = await QwenTTSClient().synthesize_batch(qwen_requests)
            qwen_results = list(batch.get("results") or [])
            if len(qwen_results) != len(payloads):
                raise RuntimeError("Qwen returned an incomplete premium narration batch.")
        except Exception as error:
            reason = f"Premium Qwen failed; Kokoro fallback used: {error}"
            append_high_quality_integration_report(
                "Premium batch fallback",
                {
                    "reason": str(error),
                    "batch_size": len(payloads),
                    "narration_job_id": payloads[0].narration_job_id or "",
                },
            )
            fallback_responses: list[TTSSynthesizeResponse] = []
            for payload in payloads:
                fallback = await self.synthesize(
                    payload.model_copy(
                        update={
                            "provider": PROVIDER_KOKORO,
                            "voice": None,
                            "voice_profile_id": "natural_female_narrator",
                        }
                    )
                )
                fallback_response = fallback.model_copy(
                    update={
                        "requested_provider": PROVIDER_HIGH_QUALITY_LOCAL,
                        "requested_profile": payload.voice_profile_id,
                        "requested_voice": payload.voice,
                        "effective_provider": PROVIDER_KOKORO,
                        "fallback_provider": PROVIDER_KOKORO,
                        "fallback_used": True,
                        "fallback_reason": reason,
                    }
                )
                persist_narration_response(payload, fallback_response)
                fallback_responses.append(fallback_response)
            return fallback_responses

        responses: list[TTSSynthesizeResponse] = []
        for payload, item, metadata in zip(payloads, qwen_results, normalized, strict=True):
            response = TTSSynthesizeResponse(
                provider=PROVIDER_HIGH_QUALITY_LOCAL,
                requested_provider=PROVIDER_HIGH_QUALITY_LOCAL,
                requested_profile=payload.voice_profile_id,
                requested_voice=payload.voice,
                effective_provider=PROVIDER_HIGH_QUALITY_LOCAL,
                effective_profile=metadata["profile_id"],
                effective_voice=metadata["voice"],
                effective_model=(
                    "Qwen3-TTS-12Hz-0.6B-Base"
                    if metadata["custom_voice"]
                    else "Qwen3-TTS-12Hz-0.6B-CustomVoice"
                ),
                startup_timings={
                    **(batch.get("startup_timings") or {}),
                    **(item.get("phase_timings") or {}),
                },
                fallback_provider=PROVIDER_KOKORO,
                audio_url=item.get("audio_url"),
                duration=item.get("duration"),
                cached=bool(item.get("cached")),
                synthesis_seconds=item.get("synthesis_seconds"),
                cache_key=item.get("cache_key"),
                text_chars=len(metadata["text"]),
                seconds_per_1000_chars=seconds_per_1000_chars(
                    float(item.get("synthesis_seconds") or 0.0),
                    len(metadata["text"]),
                ),
                chunk_index=payload.chunk_index,
                chunk_count=payload.chunk_count,
                narration_job_id=payload.narration_job_id,
                voice_profile_id=metadata["profile_id"],
                normalization_version=NORMALIZATION_VERSION,
                pronunciation_dictionary_version=metadata["pronunciation_version"],
                custom_voice_id=metadata["custom_voice"]["id"] if metadata["custom_voice"] else None,
                voice_prompt_revision=metadata["custom_voice"]["revision"] if metadata["custom_voice"] else None,
                style_reference=metadata["style_reference"],
                narration_style=metadata["direction"]["style"],
                narration_emotion=metadata["direction"]["emotion"],
                narration_intensity=metadata["direction"]["intensity"],
                narration_intensity_bucket=metadata["direction"]["intensity_bucket"],
                pause_before_ms=metadata["direction"]["pause_before_ms"],
                pause_after_ms=metadata["direction"]["pause_after_ms"],
                source_cue=metadata["direction"]["source_cue"],
                generation_instruction=metadata["generation_instruction"],
                **metadata["breath"],
            )
            response = finalize_narration_response(payload, response)
            if metadata["custom_voice"]:
                mark_custom_voice_used(metadata["custom_voice"]["id"])
            responses.append(response)
        last_payload = payloads[-1]
        last_response = responses[-1]
        LAST_TTS_EVENT.update(
            {
                "provider": PROVIDER_HIGH_QUALITY_LOCAL,
                "voice": normalized[-1]["voice"],
                "audio_path": qwen_results[-1].get("audio_path"),
                "error": None,
                "cached": all(response.cached for response in responses),
                "synthesis_seconds": round(perf_counter() - started, 3),
                "cache_key": last_response.cache_key,
                "status": "cache_hit" if all(response.cached for response in responses) else "generated_batch",
                "text_chars": sum(response.text_chars or 0 for response in responses),
                "seconds_per_1000_chars": None,
                "chunk_index": last_payload.chunk_index,
                "chunk_count": last_payload.chunk_count,
                "narration_job_id": last_payload.narration_job_id,
                "narration_style": last_response.narration_style,
                "narration_emotion": last_response.narration_emotion,
                "narration_intensity_bucket": last_response.narration_intensity_bucket,
                "pause_after_ms": last_response.pause_after_ms,
                "source_cue": last_response.source_cue or "",
                "style_reference": last_response.style_reference or "normal",
                "custom_voice_id": last_response.custom_voice_id,
                "breathing_mode": last_response.breathing_mode,
                "breath_event": last_response.breath_before or last_response.breath_after,
                "breath_confidence": last_response.breath_confidence,
                "breath_reference_used": last_response.breath_reference_used,
            }
        )
        return responses

    async def synthesize(self, payload: TTSSynthesizeRequest) -> TTSSynthesizeResponse:
        request_started = perf_counter()
        request_received_at = utc_now()
        requested_provider = payload.provider
        effective_provider, fallback_reason = fallback_for_requested_provider(requested_provider)
        if effective_provider != requested_provider:
            append_high_quality_integration_report(
                "Provider fallback",
                {
                    "requested_provider": requested_provider,
                    "effective_provider": effective_provider,
                    "reason": fallback_reason or "",
                    "narration_job_id": payload.narration_job_id or "",
                    "chunk_index": payload.chunk_index if payload.chunk_index is not None else "",
                    "chunk_count": payload.chunk_count if payload.chunk_count is not None else "",
                },
            )
            payload = payload.model_copy(update={"provider": effective_provider})
        if payload.provider == PROVIDER_HIGH_QUALITY_LOCAL:
            return (await self.synthesize_high_quality_batch([payload]))[0]
        input_text = payload.text.strip()
        direction = direction_for_payload(payload)
        char_count = len(input_text)
        payload_text_hash = payload.text_hash or text_hash(input_text)
        if payload.provider == PROVIDER_BROWSER:
            LAST_TTS_EVENT.update(
                {
                    "provider": "browser",
                    "voice": payload.voice,
                    "audio_path": None,
                    "error": None,
                    "cached": False,
                    "synthesis_seconds": 0.0,
                    "cache_key": None,
                    "status": "browser",
                    "text_chars": char_count,
                    "seconds_per_1000_chars": 0.0,
                    "chunk_index": payload.chunk_index,
                    "chunk_count": payload.chunk_count,
                    "narration_job_id": payload.narration_job_id,
                }
            )
            logger.info("TTS synthesize provider=browser voice=%s", payload.voice or "default")
            append_tts_latency_report(
                "Browser fallback request",
                {
                    "provider": "browser",
                    "request_received": request_received_at,
                    "text_chars": char_count,
                    "text_hash": payload_text_hash,
                    "chunk": f"{payload.chunk_index}/{payload.chunk_count}",
                },
            )
            return finalize_narration_response(payload, TTSSynthesizeResponse(
                provider="browser",
                requested_provider=requested_provider,
                effective_provider=PROVIDER_BROWSER,
                use_browser=True,
                synthesis_seconds=0.0,
                text_chars=char_count,
                seconds_per_1000_chars=0.0,
                chunk_index=payload.chunk_index,
                chunk_count=payload.chunk_count,
                narration_job_id=payload.narration_job_id,
            ))

        settings = load_tts_settings()
        normalized_started = perf_counter()
        client = KokoroClient(settings.kokoro_base_url)
        local_voice_hints = client.local_voice_files()
        settings_overrides = {
            key: value
            for key, value in {
                "narration_pacing": payload.narration_pacing,
                "dialogue_pause_strength": payload.dialogue_pause_strength,
                "paragraph_pause_strength": payload.paragraph_pause_strength,
                "dialogue_narration_style": payload.dialogue_narration_style,
                "tts_chunking_profile": payload.tts_chunking_profile,
            }.items()
            if value is not None
        }
        base_pronunciation_entries = [
            entry
            for entry in (pronunciation_entry_dict(item) for item in (settings.pronunciation_entries or []))
            if entry
        ]
        foundation_entries = foundation_pronunciation_entries(payload.session_id) if payload.session_id else []
        settings_overrides["pronunciation_entries"] = [*base_pronunciation_entries, *foundation_entries]
        active_settings = settings.model_copy(update=settings_overrides)
        profile = profile_by_id(payload.voice_profile_id or active_settings.tts_voice_profile_id, local_voice_hints)
        normalization = normalize_tts_text(input_text, active_settings, profile, story_id=payload.session_id)
        normalized_text = normalization["text"] or input_text
        normalized_done = perf_counter()
        char_count = len(normalized_text)
        voice = payload.voice or profile.get("voice_id") or active_settings.tts_voice or "af_heart"
        supported_options = await supported_speech_options(client)
        requested_options = {
            "temperature": payload.temperature if payload.temperature is not None else settings.kokoro_temperature,
            "top_p": payload.top_p if payload.top_p is not None else settings.kokoro_top_p,
            "exaggeration": payload.exaggeration if payload.exaggeration is not None else settings.kokoro_exaggeration,
            "style": payload.style if payload.style is not None else settings.kokoro_style,
            "cfg": payload.cfg if payload.cfg is not None else settings.kokoro_cfg,
        }
        extra_options = {
            key: value
            for key, value in requested_options.items()
            if key in supported_options and value is not None and value != ""
        }
        cache_metadata = {
            "model": "kokoro",
            "custom_voice_id": None,
            "custom_voice_revision": None,
            "selected_style_reference": "normal",
            "style_classification": direction["style"],
            "intensity_bucket": direction["intensity_bucket"],
            "generation_instruction": "",
            "voice_profile_id": payload.voice_profile_id or active_settings.tts_voice_profile_id or profile.get("id"),
            "tts_chunking_profile": payload.tts_chunking_profile or active_settings.tts_chunking_profile,
            "narration_pacing": payload.narration_pacing or active_settings.narration_pacing,
            "dialogue_pause_strength": payload.dialogue_pause_strength or active_settings.dialogue_pause_strength,
            "paragraph_pause_strength": payload.paragraph_pause_strength or active_settings.paragraph_pause_strength,
            "dialogue_narration_style": payload.dialogue_narration_style or active_settings.dialogue_narration_style,
            "pronunciation_dictionary_version": normalization["pronunciation_dictionary_version"],
            "normalization_version": NORMALIZATION_VERSION,
            "breathing_mode": direction["breathing_mode"],
            "breath_type": breath_event_for_direction(direction),
            "breath_sample_checksum": None,
            "breath_sample_revision": None,
        }
        cache_key = kokoro_cache_key(
            text=normalized_text,
            voice=voice,
            speed=payload.speed,
            base_url=settings.kokoro_base_url,
            extra_options=extra_options,
            cache_metadata=cache_metadata,
        )
        cache_lookup_started = perf_counter()
        cached_path = self._cached_audio_path(cache_key)
        cache_lookup_seconds = perf_counter() - cache_lookup_started
        common_event = {
            "provider": "kokoro",
            "session_id": payload.session_id,
            "scene_id": payload.scene_id,
            "version_id": payload.version_id,
            "narration_job_id": payload.narration_job_id,
            "chunk_index": payload.chunk_index,
            "chunk_count": payload.chunk_count,
            "text_start_offset": payload.text_start_offset,
            "text_end_offset": payload.text_end_offset,
            "follow_mode": payload.follow_mode or "off",
            "text_chars": char_count,
            "text_hash": payload_text_hash,
            "normalized_tts_text_hash": text_hash(normalized_text),
            "voice": voice,
            "voice_profile_id": cache_metadata["voice_profile_id"],
            "speed": payload.speed,
            "base_url": settings.kokoro_base_url,
            "speech_endpoint": client.speech_endpoint,
            "cache_key": cache_key,
            "tts_chunking_profile": cache_metadata["tts_chunking_profile"],
            "narration_pacing": cache_metadata["narration_pacing"],
            "dialogue_pause_strength": cache_metadata["dialogue_pause_strength"],
            "paragraph_pause_strength": cache_metadata["paragraph_pause_strength"],
            "dialogue_narration_style": cache_metadata["dialogue_narration_style"],
            "pronunciation_dictionary_version": cache_metadata["pronunciation_dictionary_version"],
            "normalization_version": cache_metadata["normalization_version"],
            "style_classification": direction["style"],
            "intensity_bucket": direction["intensity_bucket"],
            "style_reference": "normal",
            "source_cue_present": bool(direction["source_cue"]),
            "pause_after_ms": direction["pause_after_ms"],
            "breathing_mode": direction["breathing_mode"],
            "breath_event": breath_event_for_direction(direction),
            "breath_confidence": direction["breath_confidence"],
            "breath_source_cue_present": bool(direction["breath_source_cue"]),
            "breath_reference_used": False,
            "normalization_removed_ui_lines": normalization["removed_ui_lines"],
            "pronunciation_replacements": normalization["pronunciation_replacements"],
        }
        if cached_path:
            LAST_TTS_EVENT.update(
                {
                    "provider": "kokoro",
                    "voice": voice or "af_heart",
                    "audio_path": str(cached_path),
                    "error": None,
                    "cached": True,
                    "synthesis_seconds": 0.0,
                    "cache_key": cache_key,
                    "status": "cache_hit",
                    "text_chars": char_count,
                    "seconds_per_1000_chars": 0.0,
                    "chunk_index": payload.chunk_index,
                    "chunk_count": payload.chunk_count,
                    "narration_job_id": payload.narration_job_id,
                    "voice_profile_id": cache_metadata["voice_profile_id"],
                }
            )
            logger.info("TTS cache hit provider=kokoro audio_path=%s", cached_path)
            write_tts_manifest(
                cache_key,
                {
                    **common_event,
                    "audio_path": str(cached_path),
                    "audio_url": f"/audio/{cached_path.name}",
                    "normalized_tts_text_hash": text_hash(normalized_text),
                    "cached": True,
                    "created_at": utc_now(),
                    "synthesis_seconds": 0.0,
                },
            )
            append_tts_latency_report(
                "Kokoro cache hit",
                {
                    **common_event,
                    "audio_file": cached_path.name,
                    "request_to_cache_hit_seconds": safe_round(perf_counter() - request_started),
                    "cache_lookup_seconds": safe_round(cache_lookup_seconds),
                },
            )
            return finalize_narration_response(payload, TTSSynthesizeResponse(
                provider="kokoro",
                requested_provider=requested_provider,
                effective_provider=PROVIDER_KOKORO,
                effective_profile=cache_metadata["voice_profile_id"],
                effective_voice=voice,
                fallback_provider=PROVIDER_KOKORO if requested_provider == PROVIDER_HIGH_QUALITY_LOCAL else None,
                fallback_reason=fallback_reason,
                audio_url=f"/audio/{cached_path.name}",
                duration=None,
                cached=True,
                synthesis_seconds=0.0,
                cache_key=cache_key,
                text_chars=char_count,
                seconds_per_1000_chars=0.0,
                chunk_index=payload.chunk_index,
                chunk_count=payload.chunk_count,
                narration_job_id=payload.narration_job_id,
                voice_profile_id=cache_metadata["voice_profile_id"],
                normalization_version=NORMALIZATION_VERSION,
                pronunciation_dictionary_version=cache_metadata["pronunciation_dictionary_version"],
            ))

        pending_path = self.audio_dir / f"kokoro_{cache_key}.pending"
        wait_started = perf_counter()
        waited_cached_path = await self._wait_for_pending_cache(cache_key, pending_path)
        wait_seconds = perf_counter() - wait_started
        if waited_cached_path:
            LAST_TTS_EVENT.update(
                {
                    "provider": "kokoro",
                    "voice": voice or "af_heart",
                    "audio_path": str(waited_cached_path),
                    "error": None,
                    "cached": True,
                    "synthesis_seconds": 0.0,
                    "cache_key": cache_key,
                    "status": "cache_hit_after_wait",
                    "text_chars": char_count,
                    "seconds_per_1000_chars": 0.0,
                    "chunk_index": payload.chunk_index,
                    "chunk_count": payload.chunk_count,
                    "narration_job_id": payload.narration_job_id,
                    "voice_profile_id": cache_metadata["voice_profile_id"],
                }
            )
            append_tts_latency_report(
                "Kokoro cache hit after pending wait",
                {
                    **common_event,
                    "audio_file": waited_cached_path.name,
                    "pending_wait_seconds": safe_round(wait_seconds),
                    "request_to_cache_hit_seconds": safe_round(perf_counter() - request_started),
                },
            )
            return finalize_narration_response(payload, TTSSynthesizeResponse(
                provider="kokoro",
                requested_provider=requested_provider,
                effective_provider=PROVIDER_KOKORO,
                effective_profile=cache_metadata["voice_profile_id"],
                effective_voice=voice,
                fallback_provider=PROVIDER_KOKORO if requested_provider == PROVIDER_HIGH_QUALITY_LOCAL else None,
                fallback_reason=fallback_reason,
                audio_url=f"/audio/{waited_cached_path.name}",
                duration=None,
                cached=True,
                synthesis_seconds=0.0,
                cache_key=cache_key,
                text_chars=char_count,
                seconds_per_1000_chars=0.0,
                chunk_index=payload.chunk_index,
                chunk_count=payload.chunk_count,
                narration_job_id=payload.narration_job_id,
                voice_profile_id=cache_metadata["voice_profile_id"],
                normalization_version=NORMALIZATION_VERSION,
                pronunciation_dictionary_version=cache_metadata["pronunciation_dictionary_version"],
            ))

        logger.info(
            "TTS synthesize provider=kokoro base_url=%s voice=%s speed=%s extra=%s",
            settings.kokoro_base_url,
            voice or "af_heart",
            payload.speed,
            sorted(extra_options.keys()),
        )
        pending_path.write_text("pending", encoding="utf-8")
        synth_started = perf_counter()
        kokoro_request_started_at = utc_now()
        try:
            audio = await client.synthesize(
                text=normalized_text,
                voice=voice,
                speed=payload.speed,
                extra_options=extra_options,
            )
        except Exception as error:
            failed_seconds = round(perf_counter() - synth_started, 3)
            LAST_TTS_EVENT.update(
                {
                    "provider": "kokoro",
                    "voice": voice,
                    "audio_path": None,
                    "error": str(error),
                    "cached": False,
                    "synthesis_seconds": failed_seconds,
                    "cache_key": cache_key,
                    "status": "failed",
                    "text_chars": char_count,
                    "seconds_per_1000_chars": seconds_per_1000_chars(failed_seconds, char_count),
                    "chunk_index": payload.chunk_index,
                    "chunk_count": payload.chunk_count,
                    "narration_job_id": payload.narration_job_id,
                    "voice_profile_id": cache_metadata["voice_profile_id"],
                }
            )
            logger.warning("TTS synthesize failed provider=kokoro error=%s", error)
            append_tts_latency_report(
                "Kokoro synthesis failed",
                {
                    **common_event,
                    "error": str(error),
                    "kokoro_request_started": kokoro_request_started_at,
                    "synthesis_seconds": failed_seconds,
                    "seconds_per_1000_chars": seconds_per_1000_chars(failed_seconds, char_count),
                    "request_total_seconds": safe_round(perf_counter() - request_started),
                },
            )
            append_kokoro_resilience_report(
                "Backend synthesis error",
                {
                    **common_event,
                    "error": str(error),
                    "kokoro_request_started": kokoro_request_started_at,
                    "synthesis_seconds": failed_seconds,
                    "request_total_seconds": safe_round(perf_counter() - request_started),
                    "cached_chunk_existed": False,
                },
            )
            raise
        finally:
            try:
                pending_path.unlink(missing_ok=True)
            except OSError:
                pass

        extension = CONTENT_TYPE_EXTENSIONS.get(audio.content_type, ".mp3")
        filename = f"kokoro_{cache_key}{extension}"
        path = self.audio_dir / filename
        write_started = perf_counter()
        path.write_bytes(audio.content)
        write_seconds = perf_counter() - write_started
        synthesis_seconds = round(perf_counter() - synth_started, 3)
        per_1000_chars = seconds_per_1000_chars(synthesis_seconds, char_count)
        LAST_TTS_EVENT.update(
            {
                "provider": "kokoro",
                "voice": voice or "af_heart",
                "audio_path": str(path),
                "error": None,
                "cached": False,
                "synthesis_seconds": synthesis_seconds,
                "cache_key": cache_key,
                "status": "generated",
                "text_chars": char_count,
                "seconds_per_1000_chars": per_1000_chars,
                "chunk_index": payload.chunk_index,
                    "chunk_count": payload.chunk_count,
                    "narration_job_id": payload.narration_job_id,
                    "voice_profile_id": cache_metadata["voice_profile_id"],
                }
            )
        logger.info("TTS generated provider=kokoro audio_path=%s seconds=%s", path, synthesis_seconds)
        write_tts_manifest(
            cache_key,
            {
                **common_event,
                "audio_path": str(path),
                "audio_url": f"/audio/{filename}",
                "normalized_tts_text_hash": text_hash(normalized_text),
                "cached": False,
                "created_at": utc_now(),
                "synthesis_seconds": synthesis_seconds,
                "seconds_per_1000_chars": per_1000_chars,
                "bytes": len(audio.content),
                "content_type": audio.content_type,
            },
        )
        enforce_tts_cache_limit(
            self.audio_dir,
            int(settings.tts_cache_max_mb) * 1024 * 1024,
            protected_cache_keys={cache_key},
        )
        append_tts_latency_report(
            "Kokoro synthesis generated",
            {
                **common_event,
                "audio_file": filename,
                "cached": False,
                "request_received": request_received_at,
                "normalization_seconds": safe_round(normalized_done - normalized_started),
                "cache_lookup_seconds": safe_round(cache_lookup_seconds),
                "pending_wait_seconds": safe_round(wait_seconds),
                "kokoro_request_started": kokoro_request_started_at,
                "kokoro_request_seconds": synthesis_seconds,
                "audio_write_seconds": safe_round(write_seconds),
                "request_total_seconds": safe_round(perf_counter() - request_started),
                "seconds_per_1000_chars": per_1000_chars,
                "slow_warning": (
                    "slow for <=12 min narration"
                    if char_count <= 18000 and synthesis_seconds > 45
                    else ""
                ),
            },
        )

        return finalize_narration_response(payload, TTSSynthesizeResponse(
            provider="kokoro",
            requested_provider=requested_provider,
            effective_provider=PROVIDER_KOKORO,
            effective_profile=cache_metadata["voice_profile_id"],
            effective_voice=voice,
            fallback_provider=PROVIDER_KOKORO if requested_provider == PROVIDER_HIGH_QUALITY_LOCAL else None,
            fallback_reason=fallback_reason,
            audio_url=f"/audio/{filename}",
            duration=None,
            cached=False,
            synthesis_seconds=synthesis_seconds,
            cache_key=cache_key,
            text_chars=char_count,
            seconds_per_1000_chars=per_1000_chars,
            chunk_index=payload.chunk_index,
            chunk_count=payload.chunk_count,
            narration_job_id=payload.narration_job_id,
            voice_profile_id=cache_metadata["voice_profile_id"],
            normalization_version=NORMALIZATION_VERSION,
            pronunciation_dictionary_version=cache_metadata["pronunciation_dictionary_version"],
        ))

    async def preview(self, payload: TTSPreviewRequest) -> TTSPreviewResponse:
        settings = load_tts_settings()
        client = KokoroClient(settings.kokoro_base_url)
        try:
            voices = await client.list_voices()
        except Exception:
            voices = []
        custom_voice = selected_custom_voice(payload.voice, payload.voice_profile_id)
        profile = (
            custom_voice_profile(custom_voice)
            if custom_voice
            else profile_by_id(payload.voice_profile_id or settings.tts_voice_profile_id, voices)
        )
        voice = payload.voice or profile.get("voice_id") or settings.tts_voice or "af_heart"
        provider = profile.get("provider") or settings.tts_provider or PROVIDER_KOKORO
        speed = float(payload.speed if payload.speed is not None else profile.get("speed") or settings.tts_speed or 0.95)
        sample_text = (payload.sample_text or PREVIEW_SAMPLE_TEXT).strip()
        request = TTSSynthesizeRequest(
            text=sample_text,
            voice=voice,
            speed=speed,
            provider=provider,
            voice_profile_id=str(profile.get("id") or "natural_female_narrator"),
            follow_mode="off",
            text_hash=text_hash(sample_text),
            narration_pacing=profile.get("narration_pacing") or settings.narration_pacing,
            dialogue_pause_strength=profile.get("dialogue_pause_strength") or settings.dialogue_pause_strength,
            paragraph_pause_strength=profile.get("paragraph_pause_strength") or settings.paragraph_pause_strength,
            dialogue_narration_style=profile.get("dialogue_narration_style") or settings.dialogue_narration_style,
            tts_chunking_profile=profile.get("chunking_profile") or settings.tts_chunking_profile,
        )
        response = await self.synthesize(request)
        return TTSPreviewResponse(
            **response.model_dump(),
            sample_text=sample_text,
            voice=voice,
            speed=speed,
            profile=profile,
        )

    def _cached_audio_path(self, cache_key: str) -> Path | None:
        for extension in CONTENT_TYPE_EXTENSIONS.values():
            candidate = self.audio_dir / f"kokoro_{cache_key}{extension}"
            if candidate.exists() and candidate.stat().st_size > 0:
                try:
                    candidate.touch()
                    manifest = self.audio_dir / f"tts_manifest_{cache_key}.json"
                    if manifest.exists():
                        manifest.touch()
                except OSError:
                    pass
                return candidate
        return None

    async def _wait_for_pending_cache(self, cache_key: str, pending_path: Path) -> Path | None:
        if not pending_path.exists():
            return None
        try:
            age_seconds = time() - pending_path.stat().st_mtime
        except OSError:
            age_seconds = 999
        if age_seconds > 240:
            try:
                pending_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        for _ in range(12):
            await asyncio.sleep(0.25)
            cached = self._cached_audio_path(cache_key)
            if cached:
                return cached
        return None

    async def status(self) -> dict:
        settings = load_tts_settings()
        client = KokoroClient(settings.kokoro_base_url)
        reachable, error = await client.is_reachable()
        voices: list[str] = []
        local_voices = client.local_voice_files()
        voice_profiles = build_voice_profiles(local_voices or [settings.tts_voice or "af_heart"])
        kokoro = {
            "reachable": reachable,
            "base_url": settings.kokoro_base_url,
            "speech_endpoint": client.speech_endpoint,
            "supported_options": sorted(await supported_speech_options(client)) if reachable else [],
            "voice_selected": None,
            "voice_profile_selected": settings.tts_voice_profile_id,
            "device": "device not reported by Kokoro",
            "launch_command": KOKORO_START_COMMAND,
            "working_dir": KOKORO_WORKING_DIR,
            "python_exe": os.getenv("KOKORO_PYTHON_EXE", ""),
            "local_voice_count": len(local_voices),
            "local_female_voice_count": len(female_voice_ids(local_voices)),
        }
        if reachable:
            try:
                voices = await client.list_voices()
                voice_profiles = build_voice_profiles(voices)
                kokoro["voice_count"] = len(voices)
                kokoro["voice_list_loaded"] = True
                kokoro["female_voice_count"] = len(female_voice_ids(voices))
            except Exception:
                kokoro["voice_count"] = 0
                kokoro["voice_list_loaded"] = False
        available_kokoro_voices = voices or local_voices
        requested_kokoro_profile = (
            settings.tts_voice_profile_id
            if settings.tts_provider == PROVIDER_KOKORO
            else "natural_female_narrator"
        )
        selected_kokoro_profile = profile_by_id(requested_kokoro_profile, available_kokoro_voices)
        explicit_kokoro_voice = (
            settings.tts_voice
            if settings.tts_provider == PROVIDER_KOKORO and settings.tts_voice in available_kokoro_voices
            else None
        )
        kokoro["voice_selected"] = explicit_kokoro_voice or selected_kokoro_profile.get("voice_id") or "af_heart"
        if error and not reachable:
            kokoro["error"] = error
        qwen = await QwenTTSClient().health()
        custom_voices = list_custom_voices(include_disabled=True)
        all_voice_profiles: list[dict[str, Any]] = []
        seen_profile_ids: set[str] = set()
        for candidate in [
            premium_female_narrator_profile(),
            *[custom_voice_profile(record) for record in custom_voices],
            *[
                profile
                for profile in voice_profiles
                if not str(profile.get("id") or "").startswith("custom_voice:")
            ],
        ]:
            profile_id = str(candidate.get("id") or "")
            if not profile_id or profile_id in seen_profile_ids:
                continue
            seen_profile_ids.add(profile_id)
            all_voice_profiles.append(candidate)
        cache_usage = tts_cache_usage(self.audio_dir)
        return {
            "active_provider": settings.tts_provider,
            "requested_primary_provider": settings.tts_provider,
            "effective_primary_provider": settings.tts_provider,
            "browser": True,
            "browser_available": True,
            "browser_fallback_enabled": settings.allow_browser_fallback,
            "kokoro": kokoro,
            "qwen_premium": qwen,
            "custom_voices": custom_voices,
            "generated_audio_dir_exists": self.audio_dir.exists(),
            "tts_cache_bytes": cache_usage["bytes"],
            "tts_cache_files": cache_usage["files"],
            "tts_cache_max_mb": settings.tts_cache_max_mb,
            "last_provider_used": LAST_TTS_EVENT.get("provider"),
            "last_voice_used": LAST_TTS_EVENT.get("voice"),
            "last_audio_file": LAST_TTS_EVENT.get("audio_path"),
            "last_generated_audio_file": LAST_TTS_EVENT.get("audio_path"),
            "last_error": LAST_TTS_EVENT.get("error"),
            "last_cached": LAST_TTS_EVENT.get("cached"),
            "last_synthesis_seconds": LAST_TTS_EVENT.get("synthesis_seconds"),
            "last_seconds_per_1000_chars": LAST_TTS_EVENT.get("seconds_per_1000_chars"),
            "last_text_chars": LAST_TTS_EVENT.get("text_chars"),
            "last_chunk_index": LAST_TTS_EVENT.get("chunk_index"),
            "last_chunk_count": LAST_TTS_EVENT.get("chunk_count"),
            "last_narration_job_id": LAST_TTS_EVENT.get("narration_job_id"),
            "last_cache_key": LAST_TTS_EVENT.get("cache_key"),
            "last_status": LAST_TTS_EVENT.get("status"),
            "last_narration_style": LAST_TTS_EVENT.get("narration_style"),
            "last_narration_emotion": LAST_TTS_EVENT.get("narration_emotion"),
            "last_narration_intensity_bucket": LAST_TTS_EVENT.get("narration_intensity_bucket"),
            "last_pause_after_ms": LAST_TTS_EVENT.get("pause_after_ms"),
            "last_source_cue": LAST_TTS_EVENT.get("source_cue"),
            "last_style_reference": LAST_TTS_EVENT.get("style_reference"),
            "last_custom_voice_id": LAST_TTS_EVENT.get("custom_voice_id"),
            "last_breathing_mode": LAST_TTS_EVENT.get("breathing_mode"),
            "last_breath_event": LAST_TTS_EVENT.get("breath_event"),
            "last_breath_confidence": LAST_TTS_EVENT.get("breath_confidence"),
            "last_breath_reference_used": LAST_TTS_EVENT.get("breath_reference_used"),
            "fast_reading_mode": settings.fast_reading_mode,
            "pre_synthesize_new_scenes": settings.pre_synthesize_new_scenes,
            "pre_synthesize_mode": settings.pre_synthesize_mode,
            "chunked_narration_mode": settings.chunked_narration_mode,
            "tts_chunk_size": settings.tts_chunk_size,
            "tts_prebuffer_chunks": settings.tts_prebuffer_chunks,
            "tts_chunking_profile": settings.tts_chunking_profile,
            "tts_voice_profile_id": settings.tts_voice_profile_id,
            "tts_voice_profiles": all_voice_profiles,
            "narration_pacing": settings.narration_pacing,
            "dialogue_pause_strength": settings.dialogue_pause_strength,
            "paragraph_pause_strength": settings.paragraph_pause_strength,
            "dialogue_narration_style": settings.dialogue_narration_style,
            "breathing_mode": settings.breathing_mode,
            "pronunciation_dictionary_version": pronunciation_dictionary_version([entry.model_dump() for entry in settings.pronunciation_entries]),
            "normalization_version": NORMALIZATION_VERSION,
            "provider_registry": provider_snapshot(
                active_provider=settings.tts_provider,
                kokoro=kokoro,
                qwen=qwen,
                browser_fallback_enabled=settings.allow_browser_fallback,
            ),
        }

    async def voices(self, provider: str | None = None) -> dict:
        settings = load_tts_settings()
        if provider == PROVIDER_HIGH_QUALITY_LOCAL:
            profile = premium_female_narrator_profile()
            custom_voices = list_custom_voices(include_disabled=True)
            custom_profiles = [custom_voice_profile(record) for record in custom_voices]
            qwen = await QwenTTSClient().health()
            voices: list[str] = ["Vivian", "Serena", "Ono_Anna", "Sohee"]
            if qwen.get("reachable"):
                try:
                    voices = list((await QwenTTSClient().voices()).get("voices") or [])
                except Exception:
                    pass
            return {
                "browser": [],
                "kokoro": [],
                "voices": [*voices, *[record["selection_id"] for record in custom_voices if record["enabled"]]],
                "female_voices": [voice for voice in voices if voice in {"Vivian", "Serena", "Ono_Anna", "Sohee"}],
                "voice_profiles": [profile, *custom_profiles],
                "profiles": [profile, *custom_profiles],
                "custom_voices": custom_voices,
                "default": None,
                "default_profile": profile["id"],
                "available": bool(profile["available"]),
                "fallback_provider": PROVIDER_KOKORO,
                "unavailable_reason": profile["unavailable_reason"],
            }
        if provider == "browser":
            return {"browser": [], "kokoro": [], "voices": [], "default": None}
        client = KokoroClient(settings.kokoro_base_url)
        voices = await client.list_voices()
        selected = settings.tts_voice or "af_heart"
        if selected and selected not in voices:
            voices = sorted({selected, *voices})
        profiles = build_voice_profiles(voices)
        return {
            "browser": [],
            "kokoro": voices,
            "voices": voices,
            "female_voices": female_voice_ids(voices),
            "voice_profiles": profiles,
            "profiles": profiles,
            "default": selected,
            "default_profile": settings.tts_voice_profile_id or "natural_female_narrator",
        }
