from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(r"D:\StoryDriver")
BASE_URL = "http://127.0.0.1:8001"
DB_PATH = ROOT / "backend" / "data" / "app.db"
TEMP_REPORT = (
    ROOT
    / "tts_engines"
    / "qwen3_tts"
    / "benchmarks"
    / "results"
    / "qwen_custom_voice_acceptance_result.json"
)
SAMPLE_DIR = ROOT / "tts_engines" / "samples"
REFERENCE_TEXT = (
    "Morning light crossed the quiet kitchen while Elena counted three cups, checked the old clock, "
    "and said that everyone had enough time to begin carefully."
)


def request_json(path: str, payload: dict | list | None = None, method: str | None = None, timeout: int = 720) -> dict | list:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method or ("POST" if body is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} returned HTTP {error.code}: {detail}") from error


def local_audio_path(audio_url: str) -> Path:
    return ROOT / "backend" / "data" / "generated_audio" / Path(audio_url).name


def main() -> int:
    run_id = uuid4().hex[:10]
    reference_text = f"{REFERENCE_TEXT} Local test marker {run_id}."
    voice_name = f"Synthetic Qwen Acceptance {run_id}"
    narration_job_id = f"qwen-custom-acceptance-{run_id}"
    voice_id: str | None = None
    reference_path: Path | None = None
    reference_manifest: Path | None = None
    result: dict = {
        "run_id": run_id,
        "local_only": True,
        "reference_source": "Kokoro synthetic female voice generated from public synthetic test text",
        "started_at_epoch": time.time(),
        "checks": {},
        "metrics": {},
        "samples": [],
    }
    try:
        before = request_json("/tts/status")
        result["before"] = {
            "kokoro_reachable": bool(before.get("kokoro", {}).get("reachable")),
            "qwen_reachable": bool(before.get("qwen_premium", {}).get("reachable")),
        }
        reference_started = time.perf_counter()
        reference = request_json(
            "/tts/synthesize",
            {
                "text": reference_text,
                "voice": "af_heart",
                "speed": 1.0,
                "provider": "kokoro",
                "voice_profile_id": "natural_female_narrator",
                "follow_mode": "off",
            },
        )
        result["metrics"]["reference_synthesis_seconds"] = round(time.perf_counter() - reference_started, 3)
        reference_path = local_audio_path(str(reference["audio_url"]))
        if not reference_path.exists():
            raise RuntimeError("Synthetic reference audio was not written locally.")
        if reference.get("cache_key"):
            reference_manifest = reference_path.parent / f"tts_manifest_{reference['cache_key']}.json"
        encoded = base64.b64encode(reference_path.read_bytes()).decode("ascii")
        references = {
            style: {
                "filename": reference_path.name,
                "audio_base64": encoded,
                "transcript": reference_text,
            }
            for style in ("normal", "soft", "whisper", "heightened")
        }
        create_started = time.perf_counter()
        created = request_json(
            "/tts/custom-voices",
            {
                "display_name": voice_name,
                "language": "English",
                "authorization_confirmed": True,
                "dominant_speaker_confirmed": True,
                "user_notes": "Disposable local synthetic acceptance fixture.",
                "references": references,
                "build_prompt": True,
            },
        )
        result["metrics"]["reference_normalize_and_prompt_seconds"] = round(time.perf_counter() - create_started, 3)
        voice = created["voice"]
        voice_id = str(voice["id"])
        result["checks"]["prompt_ready"] = voice.get("validation_status") == "ready"
        result["checks"]["four_style_prompts"] = set(voice.get("prompt_paths", {})) == {
            "normal", "soft", "whisper", "heightened"
        }
        disabled = request_json(
            f"/tts/custom-voices/{voice_id}",
            {"enabled": False, "display_name": f"{voice_name} Managed"},
            method="PATCH",
        )
        listed = request_json("/tts/custom-voices")
        listed_voice = next((item for item in listed.get("voices", []) if item.get("id") == voice_id), {})
        result["checks"]["manage_update"] = (
            disabled.get("display_name") == f"{voice_name} Managed"
            and disabled.get("enabled") is False
            and listed_voice.get("enabled") is False
        )
        voice = request_json(
            f"/tts/custom-voices/{voice_id}",
            {"enabled": True, "display_name": voice_name},
            method="PATCH",
        )

        payloads = [
            {
                "text": "Elena opened the notebook and read the first careful line to the room.",
                "voice": voice["selection_id"],
                "speed": 1.0,
                "provider": "high_quality_local",
                "voice_profile_id": voice["profile_id"],
                "narration_job_id": narration_job_id,
                "chunk_index": 0,
                "chunk_count": 4,
            },
            {
                "text": '"Read the next line," Elena said softly.',
                "voice": voice["selection_id"],
                "speed": 1.0,
                "provider": "high_quality_local",
                "voice_profile_id": voice["profile_id"],
                "narration_job_id": narration_job_id,
                "chunk_index": 1,
                "chunk_count": 4,
            },
            {
                "text": "\"Keep the lantern low,\" she whispered, and everyone leaned closer.",
                "voice": voice["selection_id"],
                "speed": 1.0,
                "provider": "high_quality_local",
                "voice_profile_id": voice["profile_id"],
                "narration_job_id": narration_job_id,
                "chunk_index": 2,
                "chunk_count": 4,
            },
            {
                "text": '"Get away from the door!" Elena shouted.',
                "voice": voice["selection_id"],
                "speed": 1.0,
                "provider": "high_quality_local",
                "voice_profile_id": voice["profile_id"],
                "narration_job_id": narration_job_id,
                "chunk_index": 3,
                "chunk_count": 4,
            },
        ]
        synthesis_started = time.perf_counter()
        responses = request_json("/tts/synthesize-batch", payloads)
        result["metrics"]["four_style_synthesis_seconds"] = round(time.perf_counter() - synthesis_started, 3)
        result["checks"]["effective_custom_voice"] = all(
            response.get("effective_provider") == "high_quality_local"
            and response.get("custom_voice_id") == voice_id
            and response.get("effective_voice") == voice_name
            for response in responses
        )
        expected_styles = ["normal", "soft", "whisper", "heightened"]
        result["checks"]["prosody_routing"] = [response.get("style_reference") for response in responses] == expected_styles
        result["checks"]["automatic_direction"] = [response.get("narration_style") for response in responses] == expected_styles
        result["checks"]["source_cues"] = [response.get("source_cue") for response in responses] == [
            "", "said softly", "whispered", "shouted"
        ]
        result["checks"]["fresh_outputs"] = all(not response.get("cached") for response in responses)
        result["checks"]["distinct_cache_keys"] = len({response.get("cache_key") for response in responses}) == 4

        SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
        for label, response in zip(("neutral", "soft", "whisper", "heightened"), responses, strict=True):
            source = local_audio_path(str(response["audio_url"]))
            destination = SAMPLE_DIR / f"qwen_customvoice_{label}.wav"
            shutil.copy2(source, destination)
            result["samples"].append(str(destination))

        cached_started = time.perf_counter()
        cached = request_json("/tts/synthesize-batch", [payloads[0]])
        result["metrics"]["cached_chunk_latency_seconds"] = round(time.perf_counter() - cached_started, 3)
        result["checks"]["cache_reuse"] = bool(cached[0].get("cached")) and cached[0].get("cache_key") == responses[0].get("cache_key")

        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT effective_provider, effective_voice, custom_voice_id, style_reference,
                       narration_style, narration_intensity_bucket, source_cue, generation_instruction
                FROM narration_chunks WHERE job_id = ? ORDER BY chunk_index
                """,
                (narration_job_id,),
            ).fetchall()
        result["checks"]["chunk_truth_persisted"] = len(rows) == 4 and all(
            row["effective_provider"] == "high_quality_local"
            and row["effective_voice"] == voice_name
            and row["custom_voice_id"] == voice_id
            and row["generation_instruction"] == ""
            for row in rows
        )
        result["persisted_styles"] = [row["style_reference"] for row in rows]
        result["persisted_classifications"] = [row["narration_style"] for row in rows]
    except Exception as error:
        result["error"] = str(error)
    finally:
        if not voice_id:
            try:
                listed = request_json("/tts/custom-voices")
                match = next((voice for voice in listed.get("voices", []) if voice.get("display_name") == voice_name), None)
                voice_id = str(match["id"]) if match else None
            except Exception:
                pass
        if voice_id:
            try:
                request_json(f"/tts/custom-voices/{voice_id}?remove_generated_audio=true", method="DELETE")
                result["checks"]["voice_cleanup"] = True
            except Exception as error:
                result["cleanup_error"] = str(error)
        try:
            request_json("/tts/unload", {"provider": "high_quality_local"})
        except Exception:
            pass
        for path in (reference_path, reference_manifest):
            if path:
                path.unlink(missing_ok=True)
        with sqlite3.connect(DB_PATH) as db:
            db.execute("DELETE FROM narration_jobs WHERE id = ?", (narration_job_id,))
        result["finished_at_epoch"] = time.time()
        required = [
            "prompt_ready",
            "four_style_prompts",
            "manage_update",
            "effective_custom_voice",
            "prosody_routing",
            "automatic_direction",
            "source_cues",
            "fresh_outputs",
            "distinct_cache_keys",
            "cache_reuse",
            "chunk_truth_persisted",
            "voice_cleanup",
        ]
        result["passed"] = not result.get("error") and all(result["checks"].get(check) for check in required)
        TEMP_REPORT.parent.mkdir(parents=True, exist_ok=True)
        TEMP_REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
