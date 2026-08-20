from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from threading import Event, Lock
from typing import Literal

import librosa
import psutil
import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from qwen_tts import Qwen3TTSModel, VoiceClonePromptItem


ROOT = Path(r"D:\StoryDriver")
ENGINE_ROOT = ROOT / "tts_engines" / "qwen3_tts"
CUSTOM_MODEL_PATH = ENGINE_ROOT / "models" / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
BASE_MODEL_PATH = ENGINE_ROOT / "models" / "Qwen3-TTS-12Hz-0.6B-Base"
AUDIO_DIR = ROOT / "backend" / "data" / "generated_audio"
VOICE_ROOT = ROOT / "backend" / "data" / "voices"
METRICS_PATH = ENGINE_ROOT / "logs" / "service_metrics.json"
CUSTOM_MODEL_ID = "Qwen3-TTS-12Hz-0.6B-CustomVoice"
CUSTOM_MODEL_REVISION = "85e237c12c027371202489a0ec509ded67b5e4b5"
BASE_MODEL_ID = "Qwen3-TTS-12Hz-0.6B-Base"
BASE_MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"
FEMALE_VOICES = ("Vivian", "Serena", "Ono_Anna", "Sohee")
ALL_VOICES = FEMALE_VOICES + ("Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden")


class LoadRequest(BaseModel):
    threads: int = Field(default=4, ge=1, le=16)
    interop_threads: int = Field(default=4, ge=1, le=4)
    model_kind: Literal["custom_voice", "base"] = "custom_voice"


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str = "Serena"
    language: str = "English"
    speed: float = Field(default=1.0, ge=0.7, le=2.0)
    max_new_tokens: int = Field(default=384, ge=64, le=1024)
    request_id: str | None = Field(default=None, max_length=120)
    profile_id: str = Field(default="premium_female_narrator", max_length=160)
    custom_voice_id: str | None = Field(default=None, max_length=120)
    voice_prompt_path: str | None = Field(default=None, max_length=500)
    voice_prompt_checksum: str | None = Field(default=None, max_length=128)
    voice_prompt_revision: int | None = Field(default=None, ge=1)
    style_reference: Literal["normal", "soft", "whisper", "heightened"] = "normal"
    style_classification: Literal[
        "normal", "soft", "whisper", "heightened", "distressed", "intimate", "somber", "tense"
    ] = "normal"
    intensity_bucket: Literal["low", "medium", "high"] = "low"
    generation_instruction: str = Field(default="", max_length=240)
    pronunciation_revision: str | None = Field(default=None, max_length=128)
    normalization_version: str | None = Field(default=None, max_length=128)
    breathing_mode: Literal["off", "natural", "cinematic"] = "natural"
    breath_type: Literal[
        "soft_inhale", "normal_inhale", "shaky_inhale", "quiet_exhale", "recovering_breath", "gasp"
    ] | None = None
    breath_sample_checksum: str | None = Field(default=None, max_length=128)
    breath_sample_revision: int | None = Field(default=None, ge=1)


class SynthesizeBatchRequest(BaseModel):
    requests: list[SynthesizeRequest] = Field(min_length=1, max_length=4)


class CancelRequest(BaseModel):
    request_id: str | None = None


class VoicePromptRequest(BaseModel):
    style: Literal["normal", "soft", "whisper", "heightened"]
    reference_path: str = Field(min_length=1, max_length=500)
    transcript: str = Field(min_length=1, max_length=4000)
    output_path: str = Field(min_length=1, max_length=500)


class BuildVoicePromptsRequest(BaseModel):
    voice_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    prompts: list[VoicePromptRequest] = Field(min_length=1, max_length=4)


class Runtime:
    def __init__(self) -> None:
        self.model: Qwen3TTSModel | None = None
        self.model_kind: Literal["custom_voice", "base"] | None = None
        self.prompt_cache: dict[str, tuple[float, list[VoiceClonePromptItem]]] = {}
        self.model_lock = Lock()
        self.cancel_event = Event()
        self.active_request_id: str | None = None
        self.loaded_at: float | None = None
        self.last_used_at: float | None = None
        self.load_seconds: float | None = None
        self.last_metrics: dict = {}
        self.total_requests = 0
        self.cancelled_requests = 0
        self.idle_unload_seconds = 240

    def snapshot(self) -> dict:
        process = psutil.Process()
        return {
            "installed": CUSTOM_MODEL_PATH.exists(),
            "base_installed": BASE_MODEL_PATH.exists(),
            "loaded": self.model is not None,
            "loaded_model_kind": self.model_kind,
            "loading_or_synthesizing": self.model_lock.locked(),
            "active_request_id": self.active_request_id,
            "model": BASE_MODEL_ID if self.model_kind == "base" else CUSTOM_MODEL_ID,
            "model_revision": BASE_MODEL_REVISION if self.model_kind == "base" else CUSTOM_MODEL_REVISION,
            "model_path": str(BASE_MODEL_PATH if self.model_kind == "base" else CUSTOM_MODEL_PATH),
            "device": "cpu",
            "dtype": "bfloat16",
            "attention_implementation": "sdpa",
            "batch_size": 4,
            "torch_version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "threads": torch.get_num_threads(),
            "interop_threads": torch.get_num_interop_threads(),
            "load_seconds": self.load_seconds,
            "loaded_at": self.loaded_at,
            "last_used_at": self.last_used_at,
            "idle_unload_seconds": self.idle_unload_seconds,
            "rss_bytes": process.memory_info().rss,
            "total_requests": self.total_requests,
            "cancelled_requests": self.cancelled_requests,
            "last_metrics": self.last_metrics,
            "practical_for_long_form": True,
            "style_instruction_supported": False,
            "custom_voice_library_supported": BASE_MODEL_PATH.exists(),
            "cached_voice_prompts": len(self.prompt_cache),
            "measured_aggregate_rtf": 0.7257,
            "measured_first_audible_seconds": 43.548,
        }

    def load(
        self,
        threads: int = 4,
        interop_threads: int = 4,
        model_kind: Literal["custom_voice", "base"] = "custom_voice",
    ) -> dict:
        with self.model_lock:
            if self.model is not None and self.model_kind == model_kind:
                self.last_used_at = time.time()
                return self.snapshot()
            model_path = BASE_MODEL_PATH if model_kind == "base" else CUSTOM_MODEL_PATH
            if not model_path.exists():
                raise RuntimeError(f"Local Qwen model is missing: {model_path}")
            if self.model is not None:
                self.model = None
                self.model_kind = None
                self.prompt_cache.clear()
                gc.collect()
            torch.set_num_threads(threads)
            try:
                torch.set_num_interop_threads(interop_threads)
            except RuntimeError:
                pass
            started = time.perf_counter()
            self.model = Qwen3TTSModel.from_pretrained(
                str(model_path),
                device_map="cpu",
                dtype=torch.bfloat16,
                attn_implementation="sdpa",
                local_files_only=True,
            )
            self.model_kind = model_kind
            self.load_seconds = round(time.perf_counter() - started, 3)
            self.loaded_at = time.time()
            self.last_used_at = self.loaded_at
            return self.snapshot()

    def unload(self) -> dict:
        if self.model_lock.locked():
            self.cancel_event.set()
            return {"ok": True, "unloaded": False, "cancel_requested": True, "reason": "Synthesis is still unwinding."}
        with self.model_lock:
            was_loaded = self.model is not None
            self.model = None
            self.model_kind = None
            self.prompt_cache.clear()
            self.loaded_at = None
            gc.collect()
        return {"ok": True, "unloaded": was_loaded, **self.snapshot()}

    @staticmethod
    def cache_key(request: SynthesizeRequest) -> str:
        model_id = BASE_MODEL_ID if request.custom_voice_id else CUSTOM_MODEL_ID
        model_revision = BASE_MODEL_REVISION if request.custom_voice_id else CUSTOM_MODEL_REVISION
        return hashlib.sha256(
            json.dumps(
                {
                    "provider": "qwen3_tts",
                    "model": model_id,
                    "revision": model_revision,
                    "voice": request.voice,
                    "profile_id": request.profile_id,
                    "custom_voice_id": request.custom_voice_id,
                    "voice_prompt_checksum": request.voice_prompt_checksum,
                    "voice_prompt_revision": request.voice_prompt_revision,
                    "style_reference": request.style_reference,
                    "style_classification": request.style_classification,
                    "intensity_bucket": request.intensity_bucket,
                    "generation_instruction": request.generation_instruction,
                    "language": request.language,
                    "speed": request.speed,
                    "max_new_tokens": request.max_new_tokens,
                    "pronunciation_revision": request.pronunciation_revision,
                    "normalization_version": request.normalization_version,
                    "breathing_mode": request.breathing_mode,
                    "breath_type": request.breath_type,
                    "breath_sample_checksum": request.breath_sample_checksum,
                    "breath_sample_revision": request.breath_sample_revision,
                    "text": request.text,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:32]

    def cached_response(self, request: SynthesizeRequest) -> dict | None:
        cache_key = self.cache_key(request)
        output = AUDIO_DIR / f"qwen_{cache_key}.wav"
        if not output.exists():
            return None
        try:
            info = sf.info(output)
        except (OSError, RuntimeError):
            return None
        return {
            "ok": True,
            "provider": "qwen3_tts",
            "request_id": request.request_id or cache_key[:16],
            "audio_path": str(output),
            "audio_url": f"/audio/{output.name}",
            "sample_rate": info.samplerate,
            "duration": round(float(info.duration), 3),
            "synthesis_seconds": 0.0,
            "real_time_factor": 0.0,
            "voice": request.voice,
            "profile_id": request.profile_id,
            "custom_voice_id": request.custom_voice_id,
            "voice_prompt_revision": request.voice_prompt_revision,
            "style_reference": request.style_reference,
            "style_classification": request.style_classification,
            "intensity_bucket": request.intensity_bucket,
            "generation_instruction": request.generation_instruction,
            "cached": True,
            "cache_key": cache_key,
        }

    @staticmethod
    def _voice_path(value: str, *, suffix: str | None = None) -> Path:
        path = Path(value).resolve()
        root = VOICE_ROOT.resolve()
        if root not in path.parents:
            raise ValueError("Custom voice files must stay under StoryDriver's local voice directory.")
        if suffix and path.suffix.lower() != suffix:
            raise ValueError(f"Custom voice file must use {suffix}.")
        return path

    def _load_prompt(self, value: str) -> list[VoiceClonePromptItem]:
        path = self._voice_path(value, suffix=".pt")
        if not path.exists():
            raise ValueError("Cached custom voice prompt is missing.")
        modified = path.stat().st_mtime
        cached = self.prompt_cache.get(str(path))
        if cached and cached[0] == modified:
            return cached[1]
        payload = torch.load(path, map_location="cpu", weights_only=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Cached custom voice prompt is invalid.")
        items: list[VoiceClonePromptItem] = []
        for raw in payload["items"]:
            if not isinstance(raw, dict) or raw.get("ref_spk_embedding") is None:
                raise ValueError("Cached custom voice prompt item is invalid.")
            ref_code = raw.get("ref_code")
            ref_spk = raw["ref_spk_embedding"]
            items.append(
                VoiceClonePromptItem(
                    ref_code=ref_code if torch.is_tensor(ref_code) or ref_code is None else torch.tensor(ref_code),
                    ref_spk_embedding=ref_spk if torch.is_tensor(ref_spk) else torch.tensor(ref_spk),
                    x_vector_only_mode=bool(raw.get("x_vector_only_mode", False)),
                    icl_mode=bool(raw.get("icl_mode", True)),
                    ref_text=raw.get("ref_text"),
                )
            )
        if not items:
            raise ValueError("Cached custom voice prompt is empty.")
        self.prompt_cache[str(path)] = (modified, items)
        return items

    def build_voice_prompts(self, request: BuildVoicePromptsRequest) -> dict:
        with self.model_lock:
            if self.model is None or self.model_kind != "base":
                raise RuntimeError("Qwen Base is not loaded. Call POST /load with model_kind=base first.")
            prompt_paths: dict[str, str] = {}
            prompt_checksums: dict[str, str] = {}
            timings: dict[str, float] = {}
            for item in request.prompts:
                reference = self._voice_path(item.reference_path, suffix=".wav")
                output = self._voice_path(item.output_path, suffix=".pt")
                if not reference.exists():
                    raise ValueError(f"The {item.style} reference is missing.")
                started = time.perf_counter()
                prompt_items = self.model.create_voice_clone_prompt(
                    ref_audio=str(reference),
                    ref_text=item.transcript,
                    x_vector_only_mode=False,
                )
                payload = {
                    "format": "storydriver-qwen-voice-prompt-v1",
                    "voice_id": request.voice_id,
                    "revision": request.revision,
                    "style": item.style,
                    "items": [asdict(prompt_item) for prompt_item in prompt_items],
                }
                output.parent.mkdir(parents=True, exist_ok=True)
                pending = output.with_suffix(".pending.pt")
                torch.save(payload, pending)
                pending.replace(output)
                prompt_paths[item.style] = str(output)
                prompt_checksums[item.style] = hashlib.sha256(output.read_bytes()).hexdigest()
                timings[item.style] = round(time.perf_counter() - started, 3)
                self.prompt_cache.pop(str(output.resolve()), None)
            self.last_used_at = time.time()
            return {
                "ok": True,
                "voice_id": request.voice_id,
                "revision": request.revision,
                "model": BASE_MODEL_ID,
                "model_revision": BASE_MODEL_REVISION,
                "prompt_paths": prompt_paths,
                "prompt_checksums": prompt_checksums,
                "prompt_creation_seconds": timings,
            }

    def synthesize_batch(self, requests: list[SynthesizeRequest]) -> list[dict]:
        if not requests or len(requests) > 4:
            raise ValueError("Qwen batch size must be between one and four.")
        with self.model_lock:
            valid_voices = {voice.lower() for voice in ALL_VOICES}
            custom_batch = any(request.custom_voice_id for request in requests)
            if custom_batch and not all(request.custom_voice_id and request.voice_prompt_path for request in requests):
                raise ValueError("Custom voice batches require a cached prompt for every chunk.")
            if custom_batch and any(not request.custom_voice_id for request in requests):
                raise ValueError("Built-in and custom voices cannot be mixed in one Qwen batch.")
            for request in requests:
                if not request.custom_voice_id and request.voice.lower() not in valid_voices:
                    raise ValueError(f"Unsupported voice '{request.voice}'.")
            results: list[dict | None] = [None] * len(requests)
            missing: list[tuple[int, SynthesizeRequest]] = []
            cache_started = time.perf_counter()
            for index, request in enumerate(requests):
                cached = self.cached_response(request)
                if cached:
                    results[index] = cached
                else:
                    missing.append((index, request))
            cache_lookup_seconds = time.perf_counter() - cache_started
            for result in results:
                if result is not None:
                    result["phase_timings"] = {
                        "worker_cache_lookup_seconds": round(cache_lookup_seconds, 4),
                        "voice_prompt_load_seconds": 0.0,
                        "model_synthesis_seconds": 0.0,
                        "wav_finalize_seconds": 0.0,
                    }
            if not missing:
                self.last_used_at = time.time()
                return [result for result in results if result is not None]
            required_kind = "base" if custom_batch else "custom_voice"
            if self.model is None or self.model_kind != required_kind:
                raise RuntimeError(f"Qwen {required_kind} is not loaded. Call POST /load first.")
            self.cancel_event.clear()
            self.active_request_id = missing[0][1].request_id or hashlib.sha256(
                "|".join(request.text for _, request in missing).encode("utf-8")
            ).hexdigest()[:16]
            started = time.perf_counter()
            self.total_requests += len(missing)
            try:
                active_requests = [request for _, request in missing]
                prompt_load_seconds = 0.0
                with torch.inference_mode():
                    texts = [request.text for request in active_requests]
                    languages = [request.language for request in active_requests]
                    if custom_batch:
                        prompt_started = time.perf_counter()
                        checked_prompts: set[tuple[str, str]] = set()
                        for request in active_requests:
                            prompt_path = self._voice_path(str(request.voice_prompt_path), suffix=".pt")
                            expected_checksum = str(request.voice_prompt_checksum or "")
                            identity = (str(prompt_path), expected_checksum)
                            if identity in checked_prompts:
                                continue
                            if expected_checksum and hashlib.sha256(prompt_path.read_bytes()).hexdigest() != expected_checksum:
                                raise ValueError("Cached custom voice prompt checksum does not match its voice revision.")
                            checked_prompts.add(identity)
                        prompt_items = [
                            self._load_prompt(str(request.voice_prompt_path))[0] for request in active_requests
                        ]
                        prompt_load_seconds = time.perf_counter() - prompt_started
                        synthesis_started = time.perf_counter()
                        wavs, sample_rate = self.model.generate_voice_clone(
                            text=texts if len(texts) > 1 else texts[0],
                            language=languages if len(languages) > 1 else languages[0],
                            voice_clone_prompt=prompt_items,
                            max_new_tokens=max(request.max_new_tokens for request in active_requests),
                        )
                    else:
                        synthesis_started = time.perf_counter()
                        wavs, sample_rate = self.model.generate_custom_voice(
                            text=texts if len(texts) > 1 else texts[0],
                            language=languages if len(languages) > 1 else languages[0],
                            speaker=[request.voice for request in active_requests] if len(active_requests) > 1 else active_requests[0].voice,
                            max_new_tokens=max(request.max_new_tokens for request in active_requests),
                        )
                model_synthesis_seconds = time.perf_counter() - synthesis_started
                if self.cancel_event.is_set():
                    self.cancelled_requests += len(missing)
                    raise RuntimeError("Qwen synthesis was cancelled before cache commit.")
                synthesis_seconds = time.perf_counter() - started
                AUDIO_DIR.mkdir(parents=True, exist_ok=True)
                prepared_outputs: list[tuple[int, SynthesizeRequest, object, float]] = []
                generated_duration = 0.0
                for (result_index, request), waveform in zip(missing, wavs, strict=True):
                    if abs(request.speed - 1.0) > 0.001:
                        waveform = librosa.effects.time_stretch(waveform, rate=request.speed)
                    duration = len(waveform) / sample_rate
                    generated_duration += duration
                    prepared_outputs.append((result_index, request, waveform, duration))
                aggregate_rtf = synthesis_seconds / max(generated_duration, 0.001)
                finalize_started = time.perf_counter()
                for result_index, request, waveform, duration in prepared_outputs:
                    cache_key = self.cache_key(request)
                    output = AUDIO_DIR / f"qwen_{cache_key}.wav"
                    pending = output.with_suffix(".pending.wav")
                    sf.write(pending, waveform, sample_rate)
                    pending.replace(output)
                    results[result_index] = {
                        "ok": True,
                        "provider": "qwen3_tts",
                        "request_id": request.request_id or cache_key[:16],
                        "audio_path": str(output),
                        "audio_url": f"/audio/{output.name}",
                        "sample_rate": sample_rate,
                        "duration": round(duration, 3),
                        "synthesis_seconds": round(synthesis_seconds, 3),
                        "real_time_factor": round(aggregate_rtf, 3),
                        "voice": request.voice,
                        "profile_id": request.profile_id,
                        "custom_voice_id": request.custom_voice_id,
                        "voice_prompt_revision": request.voice_prompt_revision,
                        "style_reference": request.style_reference,
                        "style_classification": request.style_classification,
                        "intensity_bucket": request.intensity_bucket,
                        "generation_instruction": request.generation_instruction,
                        "cached": False,
                        "cache_key": cache_key,
                        "phase_timings": {
                            "worker_cache_lookup_seconds": round(cache_lookup_seconds, 4),
                            "voice_prompt_load_seconds": round(prompt_load_seconds, 4),
                            "model_synthesis_seconds": round(model_synthesis_seconds, 4),
                            "wav_finalize_seconds": 0.0,
                        },
                    }
                wav_finalize_seconds = time.perf_counter() - finalize_started
                for result_index, _request, _waveform, _duration in prepared_outputs:
                    if results[result_index] is not None:
                        results[result_index]["phase_timings"]["wav_finalize_seconds"] = round(
                            wav_finalize_seconds, 4
                        )
                self.last_used_at = time.time()
                self.last_metrics = {
                    "request_id": self.active_request_id,
                    "batch_size": len(active_requests),
                    "voices": sorted({request.voice for request in active_requests}),
                    "custom_voice_id": active_requests[0].custom_voice_id,
                    "model_kind": required_kind,
                    "text_chars": sum(len(request.text) for request in active_requests),
                    "duration_seconds": round(generated_duration, 3),
                    "synthesis_seconds": round(synthesis_seconds, 3),
                    "aggregate_real_time_factor": round(synthesis_seconds / max(generated_duration, 0.001), 3),
                }
                METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
                METRICS_PATH.write_text(json.dumps(self.snapshot(), indent=2), encoding="utf-8")
                return [result for result in results if result is not None]
            finally:
                self.active_request_id = None

    def synthesize(self, request: SynthesizeRequest) -> dict:
        return self.synthesize_batch([request])[0]


runtime = Runtime()


async def idle_unload_loop() -> None:
    while True:
        await asyncio.sleep(15)
        if runtime.model is None or runtime.model_lock.locked() or runtime.last_used_at is None:
            continue
        if time.time() - runtime.last_used_at >= runtime.idle_unload_seconds:
            await asyncio.to_thread(runtime.unload)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(idle_unload_loop())
    try:
        yield
    finally:
        task.cancel()
        runtime.cancel_event.set()
        await asyncio.to_thread(runtime.unload)


app = FastAPI(title="StoryDriver Qwen3-TTS Premium Service", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"ok": True, **runtime.snapshot()}


@app.get("/status")
def status() -> dict:
    return runtime.snapshot()


@app.get("/voices")
def voices() -> dict:
    return {
        "voices": list(ALL_VOICES),
        "female_voices": list(FEMALE_VOICES),
        "default_profile": {"id": "premium_female_narrator", "display_name": "Premium Female Narrator", "voice": "Serena"},
        "custom_voice_model": {
            "installed": BASE_MODEL_PATH.exists(),
            "model": BASE_MODEL_ID,
            "model_revision": BASE_MODEL_REVISION,
            "supported_styles": ["normal", "soft", "whisper", "heightened"],
        },
    }


@app.post("/load")
async def load(request: LoadRequest) -> dict:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(runtime.load, request.threads, request.interop_threads, request.model_kind),
            timeout=120,
        )
    except asyncio.TimeoutError as error:
        raise HTTPException(status_code=504, detail="Qwen model load exceeded 120 seconds.") from error
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/build-voice-prompts")
async def build_voice_prompts(request: BuildVoicePromptsRequest) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(runtime.build_voice_prompts, request), timeout=240)
    except asyncio.TimeoutError as error:
        raise HTTPException(status_code=504, detail="Qwen voice-prompt creation exceeded 240 seconds.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/unload")
async def unload() -> dict:
    return await asyncio.to_thread(runtime.unload)


@app.post("/shutdown")
async def shutdown() -> dict:
    result = await asyncio.to_thread(runtime.unload)

    async def stop_process() -> None:
        await asyncio.sleep(0.25)
        os._exit(0)

    asyncio.create_task(stop_process())
    return {"ok": True, "shutdown_scheduled": True, "unload": result}


@app.post("/cancel")
def cancel(request: CancelRequest) -> dict:
    active = runtime.active_request_id
    if request.request_id and active and request.request_id != active:
        return {"ok": True, "cancelled": False, "reason": "Requested job is not active.", "active_request_id": active}
    runtime.cancel_event.set()
    return {
        "ok": True,
        "cancel_requested": bool(active),
        "active_request_id": active,
        "limitation": "CPU generation cannot be interrupted inside the current Torch call; completed output is discarded after cancellation.",
    }


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(runtime.synthesize, request), timeout=600)
    except asyncio.TimeoutError as error:
        runtime.cancel_event.set()
        raise HTTPException(status_code=504, detail="Qwen synthesis exceeded 600 seconds.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/synthesize-batch")
async def synthesize_batch(request: SynthesizeBatchRequest) -> dict:
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(runtime.synthesize_batch, request.requests),
            timeout=600,
        )
        return {"ok": True, "results": results, "batch_size": len(request.requests)}
    except asyncio.TimeoutError as error:
        runtime.cancel_event.set()
        raise HTTPException(status_code=504, detail="Qwen batch synthesis exceeded 600 seconds.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/metrics")
def metrics() -> dict:
    return runtime.snapshot()
