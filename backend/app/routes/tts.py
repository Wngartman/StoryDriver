from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas import (
    CustomVoiceCreateRequest,
    CustomBreathUploadRequest,
    CustomVoiceUpdateRequest,
    TTSSynthesizeRequest,
    TTSSynthesizeResponse,
    TTSPreviewRequest,
    TTSPreviewResponse,
)
from app.tts.breaths import (
    custom_voice_breath_path,
    delete_custom_voice_breath,
    save_custom_voice_breath,
)
from app.tts.custom_voices import (
    build_custom_voice_prompts,
    create_custom_voice,
    delete_custom_voice,
    get_custom_voice,
    list_custom_voices,
    update_custom_voice,
)
from app.tts.kokoro import KokoroEndpointError, KokoroPayloadError, KokoroUnavailableError
from app.tts.service import TTSService, append_kokoro_resilience_report
from app.tts.registry import append_high_quality_integration_report
from app.tts.qwen import QwenTTSClient
from app.settings.store import load_tts_settings, save_tts_settings


router = APIRouter(prefix="/tts", tags=["tts"])


@router.post("/synthesize", response_model=TTSSynthesizeResponse)
async def synthesize_tts(payload: TTSSynthesizeRequest) -> TTSSynthesizeResponse:
    try:
        return await TTSService().synthesize(payload)
    except KokoroUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KokoroEndpointError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except KokoroPayloadError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/synthesize-batch", response_model=list[TTSSynthesizeResponse])
async def synthesize_tts_batch(payloads: list[TTSSynthesizeRequest]) -> list[TTSSynthesizeResponse]:
    try:
        if not payloads or len(payloads) > 4:
            raise HTTPException(status_code=422, detail="TTS batches must contain one to four chunks.")
        if any(payload.provider != "high_quality_local" for payload in payloads):
            raise HTTPException(status_code=422, detail="Batch synthesis is reserved for high-quality local narration.")
        return await TTSService().synthesize_high_quality_batch(payloads)
    except HTTPException:
        raise
    except (KokoroUnavailableError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KokoroEndpointError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except (KokoroPayloadError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/preview", response_model=TTSPreviewResponse)
async def preview_tts(payload: TTSPreviewRequest) -> TTSPreviewResponse:
    try:
        return await TTSService().preview(payload)
    except KokoroUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KokoroEndpointError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except KokoroPayloadError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.get("/status")
async def tts_status() -> dict:
    return await TTSService().status()


@router.get("/voices")
async def tts_voices(provider: str | None = None) -> dict:
    return await TTSService().voices(provider=provider)


@router.get("/providers")
async def tts_providers() -> dict:
    status = await TTSService().status()
    return status.get("provider_registry", {})


@router.get("/custom-voices")
async def custom_voices() -> dict:
    return {"voices": list_custom_voices(include_disabled=True)}


@router.post("/custom-voices")
async def create_custom_voice_route(payload: CustomVoiceCreateRequest) -> dict:
    try:
        voice = create_custom_voice(payload.model_dump())
        if not payload.build_prompt:
            return {"voice": voice, "prompt_build": None}
        return await build_custom_voice_prompts(voice["id"])
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/custom-voices/{voice_id}")
async def get_custom_voice_route(voice_id: str) -> dict:
    voice = get_custom_voice(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail="Custom voice was not found.")
    return voice


@router.post("/custom-voices/{voice_id}/build")
async def build_custom_voice_route(voice_id: str) -> dict:
    try:
        return await build_custom_voice_prompts(voice_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.put("/custom-voices/{voice_id}/breaths/{breath_type}")
async def save_custom_breath_route(
    voice_id: str,
    breath_type: str,
    payload: CustomBreathUploadRequest,
) -> dict:
    try:
        result = save_custom_voice_breath(voice_id, breath_type, payload.model_dump())
        result["voice"] = get_custom_voice(voice_id)
        return result
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.delete("/custom-voices/{voice_id}/breaths/{breath_type}")
async def delete_custom_breath_route(voice_id: str, breath_type: str) -> dict:
    try:
        return delete_custom_voice_breath(voice_id, breath_type)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/custom-voices/{voice_id}/breaths/{breath_type}/audio")
async def custom_breath_audio_route(voice_id: str, breath_type: str) -> FileResponse:
    try:
        path = custom_voice_breath_path(voice_id, breath_type)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not path:
        raise HTTPException(status_code=404, detail="Custom breath reference was not found.")
    return FileResponse(path, media_type="audio/wav", filename=f"{breath_type}.wav")


@router.patch("/custom-voices/{voice_id}")
async def update_custom_voice_route(voice_id: str, payload: CustomVoiceUpdateRequest) -> dict:
    try:
        return update_custom_voice(voice_id, payload.model_dump(exclude_none=True))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/custom-voices/{voice_id}")
async def delete_custom_voice_route(voice_id: str, remove_generated_audio: bool = False) -> dict:
    try:
        result = delete_custom_voice(voice_id, remove_generated_audio=remove_generated_audio)
        settings = load_tts_settings()
        if settings.tts_voice == f"custom:{voice_id}" or settings.tts_voice_profile_id == f"custom_voice:{voice_id}":
            save_tts_settings(
                settings.model_copy(
                    update={
                        "tts_provider": "kokoro",
                        "tts_quality_mode": "balanced",
                        "tts_voice": None,
                        "tts_voice_profile_id": "natural_female_narrator",
                        "tts_voice_profiles": [
                            profile
                            for profile in settings.tts_voice_profiles
                            if profile.id != f"custom_voice:{voice_id}"
                        ],
                    }
                )
            )
            result["active_selection_reset_to"] = "Kokoro / Natural Female Narrator"
        return result
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/cancel")
async def tts_cancel(payload: dict | None = None) -> dict:
    job_id = (payload or {}).get("narration_job_id")
    provider = (payload or {}).get("provider")
    if provider == "high_quality_local":
        return await QwenTTSClient().cancel(job_id)
    append_high_quality_integration_report(
        "Provider cancel",
        {
            "narration_job_id": job_id or "",
            "result": "No backend TTS worker to cancel; frontend playback controller owns active playback.",
        },
    )
    return {"ok": True, "cancelled": False, "reason": "No backend TTS worker is active."}


@router.post("/unload")
async def tts_unload(payload: dict | None = None) -> dict:
    provider = (payload or {}).get("provider") or "high_quality_local"
    if provider == "high_quality_local":
        return await QwenTTSClient().shutdown()
    append_high_quality_integration_report(
        "Provider unload",
        {
            "provider": provider,
            "result": "This provider has no unloadable high-quality worker; Kokoro remains available as fallback.",
        },
    )
    return {
        "ok": True,
        "provider": provider,
        "unloaded": False,
        "reason": "This provider has no unloadable high-quality worker. Kokoro is not unloaded by StoryDriver.",
    }


@router.post("/resilience-log")
async def tts_resilience_log(payload: dict) -> dict:
    title = str(payload.get("event") or payload.get("title") or "Frontend narration event")[:120]
    append_kokoro_resilience_report(title, payload)
    return {"ok": True}
