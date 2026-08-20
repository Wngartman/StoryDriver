from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.schemas import TTSSynthesizeRequest
from app.settings.store import load_tts_settings
from app.tts.service import TTSService, split_text_for_tts_chunks, text_hash


LAST_TTS_PRESYNTH_EVENT: dict[str, Any] = {
    "status": "idle",
    "session_id": None,
    "scene_id": None,
    "version_id": None,
    "started_at": None,
    "completed_at": None,
    "cached": False,
    "synthesis_seconds": None,
    "audio_url": None,
    "error": None,
    "chunk_count": None,
    "first_chunk_only": False,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tts_presynth_status() -> dict[str, Any]:
    return dict(LAST_TTS_PRESYNTH_EVENT)


def update_presynth_event(**patch: Any) -> None:
    LAST_TTS_PRESYNTH_EVENT.update(patch)


def should_presynthesize() -> bool:
    settings = load_tts_settings()
    return (
        settings.tts_provider == "kokoro"
        and (
            settings.pre_synthesize_mode != "off"
            or settings.pre_synthesize_new_scenes
            or settings.fast_reading_mode
            or settings.auto_read_new_scenes
        )
    )


def schedule_scene_tts_presynthesis(
    *,
    session_id: str,
    scene_id: str,
    version_id: str | None,
    text: str,
    delay_seconds: float = 0.0,
) -> None:
    if not text.strip() or not should_presynthesize():
        return

    async def runner() -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        settings = load_tts_settings()
        if settings.tts_provider != "kokoro":
            return
        chunks = split_text_for_tts_chunks(text, settings.tts_chunk_size, settings.tts_chunking_profile)
        use_first_chunk = (
            settings.chunked_narration_mode in {"first_chunk_fast", "progressive_chunks", "off"}
            and len(chunks) > 1
        )
        synth_text = chunks[0] if use_first_chunk else text
        chunk_index = 0 if use_first_chunk else None
        chunk_count = len(chunks) if use_first_chunk else None
        narration_job_id = f"presynth-{session_id}-{scene_id}-{version_id or 'active'}"
        update_presynth_event(
            status="running",
            session_id=session_id,
            scene_id=scene_id,
            version_id=version_id,
            started_at=utc_now(),
            completed_at=None,
            cached=False,
            synthesis_seconds=None,
            audio_url=None,
            error=None,
            chunk_count=chunk_count,
            first_chunk_only=use_first_chunk,
        )
        try:
            response = await TTSService().synthesize(
                TTSSynthesizeRequest(
                    text=synth_text,
                    voice=settings.tts_voice,
                    speed=settings.tts_speed,
                    provider="kokoro",
                    voice_profile_id=settings.tts_voice_profile_id,
                    session_id=session_id,
                    scene_id=scene_id,
                    version_id=version_id,
                    narration_job_id=narration_job_id,
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                    text_hash=text_hash(text),
                    narration_pacing=settings.narration_pacing,
                    dialogue_pause_strength=settings.dialogue_pause_strength,
                    paragraph_pause_strength=settings.paragraph_pause_strength,
                    dialogue_narration_style=settings.dialogue_narration_style,
                    tts_chunking_profile=settings.tts_chunking_profile,
                    pronunciation_dictionary_version=settings.pronunciation_dictionary_version,
                    normalization_version=settings.normalization_version,
                    temperature=settings.kokoro_temperature,
                    top_p=settings.kokoro_top_p,
                    exaggeration=settings.kokoro_exaggeration,
                    style=settings.kokoro_style,
                    cfg=settings.kokoro_cfg,
                )
            )
            update_presynth_event(
                status="ready",
                completed_at=utc_now(),
                cached=response.cached,
                synthesis_seconds=response.synthesis_seconds,
                audio_url=response.audio_url,
                error=None,
                chunk_count=chunk_count,
                first_chunk_only=use_first_chunk,
            )
        except Exception as error:
            update_presynth_event(status="failed", completed_at=utc_now(), error=str(error))

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(runner())

    def consume_exception(completed: asyncio.Task) -> None:
        try:
            completed.exception()
        except asyncio.CancelledError:
            return

    task.add_done_callback(consume_exception)
