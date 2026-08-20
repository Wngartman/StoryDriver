import asyncio
import json
import socket
import sys
import time
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import DATA_DIR  # noqa: E402
from app.settings.store import load_tts_settings  # noqa: E402
from app.tts.profiles import NORMALIZATION_VERSION, normalize_tts_text, profile_by_id  # noqa: E402
from app.tts.service import kokoro_cache_key  # noqa: E402


REPORT = DATA_DIR / "logs" / "KOKORO_RESILIENCE_FIX_REPORT.md"
RESILIENCE_REPORT = DATA_DIR / "logs" / "KOKORO_RESILIENCE_REPORT.md"
BASE_URL = "http://localhost:8001"
SAMPLE_TEXT = (
    "The lantern guttered in the cold draft. Elara lowered her voice. "
    "'Wait until the ridge goes dark,' she said. Beyond the barn, the valley held its breath."
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def append_report(lines: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    section = "\n".join(lines).rstrip() + "\n"
    REPORT.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 5.0) -> dict | None:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    request_lines = [
        f"{method} {path} HTTP/1.1",
        "Host: localhost:8001",
        "Connection: close",
        "Accept: application/json",
        "Content-Type: application/json",
        f"Content-Length: {len(body)}",
        "",
        "",
    ]
    request = "\r\n".join(request_lines).encode("utf-8") + body
    try:
        with socket.create_connection(("127.0.0.1", 8001), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        raw_response = b"".join(chunks)
        header, _, raw_body = raw_response.partition(b"\r\n\r\n")
        if not header.startswith(b"HTTP/1.1 2") and not header.startswith(b"HTTP/1.0 2"):
            return None
        return json.loads(raw_body.decode("utf-8")) if raw_body else None
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None


def static_frontend_checks() -> dict:
    controller = (ROOT / "frontend" / "src" / "services" / "ttsController.js").read_text(encoding="utf-8")
    store = (ROOT / "frontend" / "src" / "store" / "useAppStore.js").read_text(encoding="utf-8")
    api = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
    checks = {
        "cursor_storage": "CURSOR_STORAGE_PREFIX" in controller and "storydriver_tts_cursor" in controller,
        "saved_cursor_api": "getSavedCursor" in controller and "resumeCursor" in controller,
        "chunk_retry": "synthesizeKokoroChunkWithRetry" in controller and "Retrying narration chunk" in controller,
        "health_watchdog": "startKokoroHealthWatchdog" in controller and "Kokoro reconnecting" in controller,
        "play_rejection_state": "Tap play to continue narration" in controller,
        "resilience_log_api": "logTTSResilience" in api and "/tts/resilience-log" in api,
        "store_uses_resume": "resumeCursor" in store and "Resuming narration" in store,
    }
    for name, passed in checks.items():
        assert_true(passed, f"Static frontend check failed: {name}")
    return checks


async def cached_chunk_without_kokoro_check() -> dict:
    settings = load_tts_settings()
    profile = profile_by_id(settings.tts_voice_profile_id)
    voice = settings.tts_voice or profile.get("voice_id") or "af_heart"
    speed = float(settings.tts_speed or 0.95)
    normalization = normalize_tts_text(SAMPLE_TEXT, settings, profile)
    normalized_text = normalization["text"] or SAMPLE_TEXT
    status = request_json("/tts/status", timeout=5) or {}
    supported = set(status.get("kokoro", {}).get("supported_options") or [])
    requested_options = {
        "temperature": settings.kokoro_temperature,
        "top_p": settings.kokoro_top_p,
        "exaggeration": settings.kokoro_exaggeration,
        "style": settings.kokoro_style,
        "cfg": settings.kokoro_cfg,
    }
    extra_options = {
        key: value
        for key, value in requested_options.items()
        if key in supported and value is not None and value != ""
    }
    cache_metadata = {
        "voice_profile_id": settings.tts_voice_profile_id or profile.get("id"),
        "tts_chunking_profile": settings.tts_chunking_profile,
        "narration_pacing": settings.narration_pacing,
        "dialogue_pause_strength": settings.dialogue_pause_strength,
        "paragraph_pause_strength": settings.paragraph_pause_strength,
        "dialogue_narration_style": settings.dialogue_narration_style,
        "pronunciation_dictionary_version": normalization["pronunciation_dictionary_version"],
        "normalization_version": NORMALIZATION_VERSION,
    }
    cache_key = kokoro_cache_key(
        text=normalized_text,
        voice=voice,
        speed=speed,
        base_url=settings.kokoro_base_url,
        extra_options=extra_options,
        cache_metadata=cache_metadata,
    )
    audio_path = DATA_DIR / "generated_audio" / f"kokoro_{cache_key}.mp3"
    original_bytes = audio_path.read_bytes() if audio_path.exists() else None
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x15StoryDriver cached resilience smoke")
    try:
        response = request_json(
            "/tts/synthesize",
            method="POST",
            payload={
                "text": SAMPLE_TEXT,
                "provider": "kokoro",
                "voice": voice,
                "speed": speed,
                "session_id": f"kokoro-resilience-{uuid4()}",
                "scene_id": f"kokoro-resilience-scene-{uuid4()}",
                "version_id": f"kokoro-resilience-version-{uuid4()}",
                "narration_job_id": f"kokoro-resilience-job-{uuid4()}",
                "chunk_index": 0,
                "chunk_count": 1,
                "voice_profile_id": settings.tts_voice_profile_id,
                "narration_pacing": settings.narration_pacing,
                "dialogue_pause_strength": settings.dialogue_pause_strength,
                "paragraph_pause_strength": settings.paragraph_pause_strength,
                "dialogue_narration_style": settings.dialogue_narration_style,
                "tts_chunking_profile": settings.tts_chunking_profile,
            },
            timeout=10,
        )
        assert_true(response is not None, "Backend /tts/synthesize did not return a response.")
        assert_true(response.get("cached") is True, "Backend did not return the prepared cached chunk.")
        assert_true(response.get("audio_url") and response.get("cache_key") == cache_key, "Cached chunk response is missing URL/cache key.")
        return {
            "cache_key": cache_key,
            "audio_url": response.get("audio_url"),
            "supported_options": sorted(supported),
        }
    finally:
        if original_bytes is not None:
            audio_path.write_bytes(original_bytes)
        else:
            try:
                audio_path.unlink(missing_ok=True)
            except OSError:
                pass


def resilience_log_endpoint_check() -> dict:
    payload = {
        "event": "Resilience smoke log",
        "scene_id": "smoke-scene",
        "version_id": "smoke-version",
        "chunk_index": 0,
        "chunk_count": 1,
        "audio_current_time": 1.25,
        "global_narration_time": 1.25,
        "kokoro_health": {"reachable": False, "error": "simulated"},
        "cached_chunk_existed": True,
    }
    response = request_json("/tts/resilience-log", method="POST", payload=payload, timeout=5)
    if response is None:
        return {"backend_reachable": False, "logged": False}
    assert_true(response.get("ok") is True, "Resilience log endpoint did not return ok=true.")
    report_text = RESILIENCE_REPORT.read_text(encoding="utf-8") if RESILIENCE_REPORT.exists() else ""
    assert_true("Resilience smoke log" in report_text, "Resilience report did not receive smoke log entry.")
    return {"backend_reachable": True, "logged": True}


async def main() -> int:
    static_checks = static_frontend_checks()
    cached_check = await cached_chunk_without_kokoro_check()
    log_check = resilience_log_endpoint_check()
    lines = [
        f"## Kokoro Resilience Smoke Test - {utc_now()}",
        "",
        "- result: PASS",
        "- Static frontend cursor/retry/watchdog checks: PASS",
        "- Cached Kokoro chunk can be returned without synthesizing: PASS",
        f"- Resilience log endpoint reachable: {log_check['backend_reachable']}",
        f"- Resilience log entry written: {log_check['logged']}",
        f"- Cache key tested: `{cached_check['cache_key']}`",
        f"- Cache URL tested: `{cached_check['audio_url']}`",
        f"- Supported Kokoro options seen: `{json.dumps(cached_check['supported_options'])}`",
        f"- Static checks: `{json.dumps(static_checks, sort_keys=True)}`",
    ]
    append_report(lines)
    print("[OK] Kokoro resilience smoke test passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as error:
        print(f"[FAIL] {error}")
        sys.exit(1)
