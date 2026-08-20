from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = "http://localhost:8001"
DB_PATH = ROOT / "backend" / "data" / "app.db"
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas import ModelSettings
from app.routes.sessions import generation_parameters
from app.generation.prompt_builder import resolve_writing_length


PROFILE_FIELDS = {
    "lm_studio_url", "model", "temperature", "top_p", "max_tokens", "seed", "top_k", "min_p",
    "repeat_penalty", "presence_penalty", "frequency_penalty", "timeout_seconds", "streaming",
    "inference_backend", "reasoning_mode", "context_length", "fallback_to_openai_compatible", "notes",
}


def request(path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 60) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BACKEND + path,
        method=method,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def status(path: str, method: str, payload: dict[str, Any]) -> int:
    try:
        request(path, method, payload)
        return 200
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def restore_profile(task_type: str, profile: dict[str, Any]) -> None:
    request(
        f"/settings/task-model-profiles/{task_type}",
        "PUT",
        {key: profile.get(key) for key in PROFILE_FIELDS},
    )


def delete_story(session_id: str) -> None:
    result = request(f"/sessions/{session_id}?permanent=true", "DELETE") or {}
    job_id = result.get("job_id") or result.get("id")
    if not job_id:
        return
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = request(f"/sessions/delete-jobs/{job_id}") or {}
        if job.get("status") == "completed":
            return
        if job.get("status") in {"failed", "cancelled"}:
            raise AssertionError(f"settings fixture deletion failed: {job}")
        time.sleep(0.5)
    raise AssertionError("settings fixture deletion timed out")


def main() -> int:
    original_model = request("/settings/model")
    original_tts = request("/settings/tts")
    original_story_state = request("/settings/story-state")
    task_response = request("/settings/task-model-profiles")
    original_prose_profile = task_response["profiles"]["prose_generation"]
    session_id: str | None = None
    checks: dict[str, bool] = {}
    try:
        models = request("/models")
        model_ids = {item.get("id") for item in models}
        checks["model_refresh"] = original_model["model"] in model_ids

        modified_model = {
            **original_model,
            "active_preset_id": None,
            "system_prompt": original_model["system_prompt"] + "\nSETTINGS_CONTRACT_SYSTEM_MARKER",
            "temperature": 0.61,
            "top_p": 0.83,
            "max_tokens": 2111,
            "top_k": 31,
            "min_p": 0.07,
            "repeat_penalty": 1.17,
            "presence_penalty": 0.13,
            "frequency_penalty": 0.09,
            "seed": 424242,
            "streaming": False,
            "writing_length_mode": "beat",
        }
        saved_model = request("/settings/model", "PUT", modified_model)
        reloaded_model = request("/settings/model")
        for key in (
            "system_prompt", "temperature", "top_p", "max_tokens", "top_k", "min_p",
            "repeat_penalty", "presence_penalty", "frequency_penalty", "seed", "streaming",
            "writing_length_mode",
        ):
            require(reloaded_model[key] == saved_model[key], f"model setting did not persist: {key}")
        checks["model_settings_persist"] = True

        length = resolve_writing_length(director_note="A brief focused moment.", configured_mode="beat")
        params = generation_parameters(ModelSettings(**reloaded_model), length)
        expected_params = {
            "temperature": 0.61,
            "top_p": 0.83,
            "top_k": 31,
            "min_p": 0.07,
            "repeat_penalty": 1.17,
            "presence_penalty": 0.13,
            "frequency_penalty": 0.09,
            "seed": 424242,
        }
        for key, value in expected_params.items():
            require(params.get(key) == value, f"runtime generation payload ignored {key}")
        require(params["max_tokens"] >= 1575, "length-aware max token floor was not applied")
        checks["sampling_affects_runtime_payload"] = True

        session = request("/sessions", "POST", {"title": "SETTINGS CONTRACT DISPOSABLE"})
        session_id = session["id"]
        request(
            "/settings/task-model-profiles/prose_generation",
            "PUT",
            {
                "temperature": 0.42,
                "top_p": 0.79,
                "max_tokens": 2444,
                "seed": 1717,
                "streaming": False,
                "notes": "SETTINGS_CONTRACT_TASK_NOTE: preserve the copper token.",
            },
        )
        routed = request("/settings/task-model-profiles")
        prose = routed["resolved"]["prose_generation"]
        require(prose["temperature"] == 0.42 and prose["seed"] == 1717, "task routing overrides were not resolved")
        require(prose["streaming"] is False, "task streaming override was not resolved")
        preview = request(
            f"/sessions/{session_id}/prose-prompt-preview",
            "POST",
            {"director_note": "Write a brief moment where Mara checks the copper token.", "mode": "continue"},
        )
        require("SETTINGS_CONTRACT_SYSTEM_MARKER" in preview["system_prompt"], "system prompt did not reach runtime preview")
        require("SETTINGS_CONTRACT_TASK_NOTE" in preview["task_notes"], "task note did not reach runtime preview")
        require(preview["parameters"]["temperature"] == 0.42, "task temperature did not reach runtime preview")
        require(preview["parameters"]["seed"] == 1717, "task seed did not reach runtime preview")
        require(preview["writing_length"]["mode"] == "beat", "writing length did not reach runtime preview")
        checks["task_routing_and_notes_affect_runtime"] = True
        checks["streaming_selects_runtime_path"] = preview["override_fields"] and prose["streaming"] is False

        modified_tts = {
            **original_tts,
            "tts_provider": "kokoro",
            "tts_voice": "af_aoede",
            "tts_voice_profile_id": "natural_female_narrator",
            "tts_speed": 1.05,
            "tts_chunking_profile": "audiobook",
            "tts_chunk_size": 900,
            "tts_prebuffer_chunks": 3,
            "tts_cache_max_mb": 4096,
            "tts_follow_mode": "sentence",
            "tts_follow_highlight": "sentence",
            "pronunciation_entries": [
                {
                    "id": "settings-contract-aeron",
                    "written_form": "Aeron",
                    "spoken_form": "AIR-on",
                    "scope": "global",
                    "enabled": True,
                    "notes": "Disposable contract alias",
                }
            ],
        }
        saved_tts = request("/settings/tts", "PUT", modified_tts)
        reloaded_tts = request("/settings/tts")
        for key in (
            "tts_provider", "tts_voice", "tts_speed", "tts_chunking_profile", "tts_chunk_size",
            "tts_prebuffer_chunks", "tts_cache_max_mb", "tts_follow_mode", "pronunciation_dictionary_version",
        ):
            require(reloaded_tts[key] == saved_tts[key], f"TTS setting did not persist: {key}")
        with sqlite3.connect(DB_PATH) as db:
            alias = db.execute(
                "SELECT spoken_form FROM pronunciation_aliases WHERE written_form='Aeron' AND source='settings' AND enabled=1"
            ).fetchone()
        require(alias and alias[0] == "AIR-on", "pronunciation alias did not reach runtime storage")
        audio = request(
            "/tts/synthesize",
            "POST",
            {
                "text": "Aeron checks the lantern.",
                "provider": "kokoro",
                "voice": "af_aoede",
                "voice_profile_id": "natural_female_narrator",
                "speed": 1.05,
                "chunk_index": 0,
            },
            timeout=120,
        )
        require(audio.get("audio_url") and audio.get("provider") == "kokoro", "TTS runtime did not use Kokoro")
        checks["tts_settings_affect_runtime"] = True

        forced_qwen = {**saved_tts, "tts_provider": "high_quality_local", "high_quality_local_enabled": False}
        gated = request("/settings/tts", "PUT", forced_qwen)
        require(gated["tts_provider"] == "kokoro", "failed premium provider bypassed the benchmark gate")
        checks["qwen_gate_falls_back_to_kokoro"] = True

        changed_state = {**original_story_state, "state_extraction_timeout_seconds": 137}
        request("/settings/story-state", "PUT", changed_state)
        require(request("/settings/story-state")["state_extraction_timeout_seconds"] == 137, "Story State setting did not persist")
        checks["story_state_settings_persist"] = True

        external_model = {**original_model, "lm_studio_url": "https://example.invalid/v1"}
        external_tts = {**original_tts, "kokoro_base_url": "https://example.invalid"}
        checks["external_model_endpoint_rejected"] = status("/settings/model", "PUT", external_model) in {400, 422}
        checks["external_tts_endpoint_rejected"] = status("/settings/tts", "PUT", external_tts) in {400, 422}
        checks["image_settings_endpoint_removed"] = status("/settings/image", "PUT", {}) in {404, 405}

        failed = [name for name, passed in checks.items() if not passed]
        require(not failed, "failed settings contracts: " + ", ".join(failed))
        print("PASS: retained model, task, Story State, privacy, and TTS settings alter persisted runtime behavior.")
        for name in sorted(checks):
            print(f"  PASS {name}")
        return 0
    finally:
        request("/settings/model", "PUT", original_model)
        request("/settings/tts", "PUT", original_tts)
        request("/settings/story-state", "PUT", original_story_state)
        restore_profile("prose_generation", original_prose_profile)
        if session_id:
            delete_story(session_id)


if __name__ == "__main__":
    raise SystemExit(main())
