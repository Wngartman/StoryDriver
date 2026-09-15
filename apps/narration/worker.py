"""Local CPU Kokoro service. Distributed under GPL-3.0-or-later; see LICENSES."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import io
import multiprocessing
import os
from pathlib import Path
import sys
import time
import wave

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException, Request, Response
from kokoro_onnx import Kokoro
from pydantic import BaseModel, Field
import uvicorn


ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "runtimes" / "kokoro"
engine: Kokoro | None = None
load_seconds = 0.0
synthesis_lock = asyncio.Lock()


def load_engine() -> Kokoro:
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 2)
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.log_severity_level = 3
    session = ort.InferenceSession(str(ROOT / "kokoro-v1.0.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
    result = Kokoro.from_session(session, str(ROOT / "voices-v1.0.bin"))
    result.create("Ready.", voice="af_aoede", speed=1.0, lang="en-us")
    return result


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global engine, load_seconds
    started = time.perf_counter()
    engine = await asyncio.to_thread(load_engine)
    load_seconds = time.perf_counter() - started
    yield
    engine = None


app = FastAPI(title="StoryDriver Kokoro", lifespan=lifespan)


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=6000)
    voice: str = "af_aoede"
    speed: float = Field(default=0.95, ge=0.5, le=2.0)
    model: str = "kokoro"
    response_format: str = "wav"


@app.get("/health")
def health():
    return {"status": "healthy" if engine else "starting", "engine": "kokoro-onnx", "device": "cpu", "load_seconds": round(load_seconds, 3)}


@app.get("/v1/audio/voices")
def voices():
    return {"voices": [voice for voice in engine.get_voices() if voice.startswith(("af_", "bf_", "am_", "bm_"))] if engine else []}


def render(payload: SpeechRequest) -> tuple[bytes, float]:
    assert engine is not None
    samples, rate = engine.create(payload.input, voice=payload.voice, speed=payload.speed, lang="en-gb" if payload.voice.startswith("b") else "en-us")
    samples = np.asarray(samples)
    if not samples.size or not np.isfinite(samples).all():
        raise RuntimeError("Kokoro produced invalid audio.")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue(), len(samples) / rate


@app.post("/v1/audio/speech")
async def speech(payload: SpeechRequest, request: Request):
    if engine is None:
        raise HTTPException(503, "Kokoro is still loading.")
    if payload.voice not in engine.get_voices():
        raise HTTPException(422, "Unknown Kokoro voice.")
    if not payload.input.strip():
        raise HTTPException(422, "Narration text is empty.")
    async with synthesis_lock:
        if await request.is_disconnected():
            return Response(status_code=499)
        started = time.perf_counter()
        try:
            content, duration = await asyncio.to_thread(render, payload)
        except Exception as error:
            raise HTTPException(500, "Local Kokoro synthesis failed. Retry the chunk.") from error
    return Response(content, media_type="audio/wav", headers={
        "X-Audio-Duration": str(round(duration, 4)),
        "X-Synthesis-Seconds": str(round(time.perf_counter() - started, 4)),
        "Cache-Control": "no-store",
    })


if __name__ == "__main__":
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8880)
    parser.add_argument("--model-root", type=Path)
    args = parser.parse_args()
    if args.model_root:
        ROOT = args.model_root.resolve()
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning", log_config=None)
