from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen
from urllib.error import URLError
from uuid import uuid4


ROOT = Path(r"D:\StoryDriver")
BASE_URL = "http://127.0.0.1:8001"
EVIDENCE_PATH = ROOT / "backend" / "data" / "logs" / "qwen_real_startup_profile_evidence.json"


def request_json(path: str, payload: dict | list | None = None, timeout: int = 700):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def synthesize(text: str, settings: dict) -> tuple[float, dict]:
    started = time.perf_counter()
    responses = request_json(
        "/tts/synthesize-batch",
        [
            {
                "text": text,
                "provider": "high_quality_local",
                "voice": settings.get("tts_voice"),
                "voice_profile_id": settings.get("tts_voice_profile_id"),
                "speed": 1.0,
                "chunk_index": 0,
                "chunk_count": 1,
                "narration_job_id": f"startup-profile-{uuid4().hex}",
                "follow_mode": "off",
                "breathing_mode": settings.get("breathing_mode") or "natural",
                "narration_pacing": settings.get("narration_pacing") or "natural",
                "dialogue_pause_strength": settings.get("dialogue_pause_strength") or "medium",
                "paragraph_pause_strength": settings.get("paragraph_pause_strength") or "medium",
                "dialogue_narration_style": settings.get("dialogue_narration_style") or "neutral",
            }
        ],
    )
    elapsed = time.perf_counter() - started
    return elapsed, responses[0]


def remove_test_audio(response: dict) -> None:
    audio_url = str(response.get("audio_url") or "")
    if not audio_url:
        return
    audio_path = ROOT / "backend" / "data" / "generated_audio" / Path(audio_url).name
    audio_path.unlink(missing_ok=True)


def wait_for_worker_shutdown(timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:8891/health", timeout=0.4):
                time.sleep(0.25)
        except (OSError, URLError):
            return
    raise RuntimeError("Qwen worker did not shut down before the cold-start measurement.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-audio", action="store_true")
    args = parser.parse_args()
    settings = request_json("/settings/tts")
    marker = uuid4().hex[:10]
    cold_text = f"Local startup fixture {marker}. Rain traced the window while the narrator counted three quiet breaths."
    warm_text = f"Local warm fixture {marker}. The lamp stayed lit and the hallway remained still."
    request_json("/tts/unload", {"provider": "high_quality_local"}, timeout=30)
    wait_for_worker_shutdown()
    cold_seconds, cold = synthesize(cold_text, settings)
    cached_seconds, cached = synthesize(cold_text, settings)
    warm_seconds, warm = synthesize(warm_text, settings)
    evidence = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "selected_profile": settings.get("tts_voice_profile_id"),
        "selected_voice": settings.get("tts_voice"),
        "cold": {
            "first_audio_backend_seconds": round(cold_seconds, 3),
            "cached": bool(cold.get("cached")),
            "effective_provider": cold.get("effective_provider"),
            "effective_model": cold.get("effective_model"),
            "effective_voice": cold.get("effective_voice"),
            "startup_timings": cold.get("startup_timings") or {},
        },
        "exact_cache": {
            "first_audio_backend_seconds": round(cached_seconds, 3),
            "cached": bool(cached.get("cached")),
            "startup_timings": cached.get("startup_timings") or {},
        },
        "warm_new": {
            "first_audio_backend_seconds": round(warm_seconds, 3),
            "cached": bool(warm.get("cached")),
            "startup_timings": warm.get("startup_timings") or {},
        },
    }
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if not args.keep_audio:
        for response in (cold, cached, warm):
            remove_test_audio(response)
    if cold.get("effective_provider") != "high_quality_local":
        raise AssertionError(f"Cold Qwen request fell back: {cold.get('fallback_reason')}")
    if cold_seconds > 60:
        raise AssertionError(f"Cold prepared Qwen first audio exceeded 60 seconds: {cold_seconds:.3f}s")
    if not cached.get("cached") or cached_seconds > 8:
        raise AssertionError(f"Exact Qwen cache hit missed the 8-second target: {cached_seconds:.3f}s")
    if warm.get("effective_provider") != "high_quality_local" or warm_seconds > 60:
        raise AssertionError(f"Warm Qwen narration failed its 60-second target: {warm_seconds:.3f}s")
    print(json.dumps(evidence, indent=2))
    print("PASS: qwen_real_startup_profile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
