from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND = "http://localhost:8001"
METRICS = ROOT / "backend" / "data" / "logs" / "WRITING_LENGTH_CONTRACT_LATEST.json"

MODES = {
    "beat": {
        "note": "Stay in one focused kitchen-table beat. Mara decides whether to show June the unopened letter. Deepen the hesitation and dialogue without leaving the room or advancing past this decision.",
        "expected": (300, 700),
        "accept": (220, 900),
    },
    "scene": {
        "note": "Write a developed scene in the apartment kitchen. Mara, June, and Ellis disagree about whether to call the landlord tonight. Keep clear blocking, distinct goals, interiority, and one meaningful decision without skipping ahead.",
        "expected": (800, 1400),
        "accept": (600, 1750),
    },
    "chapter": {
        "note": "Write the opening in the apartment before anyone contacts the landlord. Introduce Mara, June, and Ellis in detail, dramatize their conflicting plans, and deepen the relationships without moving beyond the same evening or resolving the housing problem.",
        "expected": (1800, 2600),
        "accept": (1400, 3200),
    },
    "custom": {
        "note": "Write the apartment meeting as a sustained scene. Keep all three adults in the room, complete the letter handoff, and stop when they agree on who will make the call.",
        "expected": (950, 1150),
        "accept": (750, 1450),
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def stream_generation(session_id: str, note: str, mode: str = "continue", target_scene_id: str | None = None) -> dict[str, Any]:
    payload = {"director_note": note, "mode": mode, "target_scene_id": target_scene_id}
    req = urllib.request.Request(
        f"{BACKEND}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    started = time.perf_counter()
    first_delta = None
    scene = None
    stages: list[str] = []
    with urllib.request.urlopen(req, timeout=900) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "status":
                stage = str(event.get("stage") or "")
                if stage:
                    stages.append(stage)
                    print(f"  {mode}: {stage}", flush=True)
            elif event.get("type") == "delta" and first_delta is None:
                first_delta = time.perf_counter()
            elif event.get("type") == "scene":
                scene = event.get("scene") or event
            elif event.get("type") == "error":
                raise RuntimeError(event.get("detail") or "generation failed")
    if not scene:
        raise RuntimeError("generation returned no saved scene")
    text = str(scene.get("generated_text") or "")
    stats = scene.get("generation_stats") or {}
    return {
        "scene_id": scene.get("id"),
        "version_id": scene.get("active_version_id"),
        "version_count": scene.get("version_count"),
        "word_count": len(re.findall(r"\b[\w'-]+\b", text)),
        "first_visible_seconds": round((first_delta - started), 3) if first_delta else None,
        "total_seconds": round(time.perf_counter() - started, 3),
        "planner_seconds": stats.get("planner_seconds") or stats.get("planning_seconds"),
        "prose_seconds": stats.get("prose_seconds"),
        "review_seconds": stats.get("review_seconds"),
        "repair_applied": bool(stats.get("repair_applied") or stats.get("repair_used")),
        "visible_tps": stats.get("visible_prose_tokens_per_second") or stats.get("tokens_per_second"),
        "writing_length": stats.get("writing_length") or {},
        "stages": stages,
    }


def delete_story(session_id: str) -> dict[str, Any]:
    result = request(f"/sessions/{session_id}?permanent=true", "DELETE") or {}
    job_id = result.get("job_id") or result.get("id")
    if not job_id:
        return result
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        job = request(f"/sessions/delete-jobs/{job_id}") or {}
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(1)
    return {"status": "timeout", "job_id": job_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=1, choices=range(1, 6))
    args = parser.parse_args()
    original = request("/settings/model")
    created: list[str] = []
    result: dict[str, Any] = {"started_at": utc_now(), "samples_per_mode": args.samples, "modes": {}, "overrides": {}, "versions": [], "errors": []}
    try:
        baseline = {
            **original,
            "active_preset_id": None,
            "model": original.get("model") or "gemma4-26b-a4b-uncensored-hauhaucs-balanced",
            "max_tokens": max(8000, int(original.get("max_tokens") or 0)),
            "streaming": True,
            "writing_path": "deliberate_pipeline",
            "writing_process_mode": "deliberate",
            "app_planning_enabled": True,
            "chapter_extension_enabled": True,
        }
        for mode, contract in MODES.items():
            configured = {
                **baseline,
                "writing_length_mode": mode,
                "custom_word_min": 950 if mode == "custom" else None,
                "custom_word_max": 1150 if mode == "custom" else None,
            }
            request("/settings/model", "PUT", configured)
            reloaded = request("/settings/model")
            if reloaded["writing_length_mode"] != mode:
                raise AssertionError(f"{mode} did not persist")
            session = request("/sessions", "POST", {"title": f"LENGTH CONTRACT {mode.upper()} DISPOSABLE"})
            session_id = session["id"]
            created.append(session_id)
            preview = request(
                f"/sessions/{session_id}/prose-prompt-preview",
                "POST",
                {"director_note": contract["note"], "mode": "continue"},
            )
            expected_min, expected_max = contract["expected"]
            if (preview["writing_length"]["min_words"], preview["writing_length"]["max_words"]) != (expected_min, expected_max):
                raise AssertionError(f"{mode} preview used the wrong word range: {preview['writing_length']}")
            scene_contract = (preview.get("scene_plan") or {}).get("scene_contract") or {}
            required_contract_keys = {"start_time", "allowed_duration", "start_location", "end_boundary", "required_beats", "must_not_occur_yet"}
            if not required_contract_keys.issubset(scene_contract):
                raise AssertionError(f"{mode} planner preview lost Scene Contract fields")
            if int(preview["parameters"]["max_tokens"]) < 768:
                raise AssertionError(f"{mode} max_tokens was not length-aware")
            samples = []
            for sample_index in range(args.samples):
                print(f"[{mode}] live sample {sample_index + 1}/{args.samples}", flush=True)
                generated = stream_generation(session_id, contract["note"])
                low, high = contract["accept"]
                generated["within_acceptance"] = low <= generated["word_count"] <= high
                samples.append(generated)
            result["modes"][mode] = {
                "configured_range": [expected_min, expected_max],
                "accepted_test_range": list(contract["accept"]),
                "preview": {"writing_length": preview["writing_length"], "max_tokens": preview["parameters"]["max_tokens"]},
                "samples": samples,
            }

        request("/settings/model", "PUT", {**baseline, "writing_length_mode": "custom", "custom_word_min": 950, "custom_word_max": 1150})
        custom_session_id = created[-1]
        target = result["modes"]["custom"]["samples"][0]
        target_scene_id = target["scene_id"]
        for version_mode, note in (
            ("rewrite", "Rewrite the same apartment meeting with closer Mara interiority. Preserve the exact events, time boundary, participants, and selected passage length; add no later story beat."),
            ("revise", "Revise this same scene for clearer blocking and more distinct dialogue. Preserve its scope and selected passage length."),
            ("regenerate", "Regenerate this same target scene with equivalent events and the selected passage length. Do not continue the story."),
        ):
            version_result = stream_generation(custom_session_id, note, version_mode, target_scene_id)
            version_result["mode"] = version_mode
            version_result["same_scene"] = version_result["scene_id"] == target_scene_id
            version_result["length_preserved"] = 750 <= version_result["word_count"] <= 1450
            result["versions"].append(version_result)

        override_session = request("/sessions", "POST", {"title": "LENGTH OVERRIDE DISPOSABLE"})
        override_id = override_session["id"]
        created.append(override_id)
        request("/settings/model", "PUT", {**baseline, "writing_length_mode": "scene"})
        for label, note, expected_mode, expected_range in (
            ("brief", "Write a brief moment at the door before Mara enters.", "beat", (300, 700)),
            ("reading_time", "Write an 8-12 minute chapter that stays in the apartment this evening.", "chapter", (1800, 2600)),
            ("explicit_words", "Write exactly 500 words about the letter handoff.", "custom", (450, 550)),
            ("explicit_range", "Write 1200-1500 words and do not leave the room.", "custom", (1200, 1500)),
        ):
            preview = request(f"/sessions/{override_id}/prose-prompt-preview", "POST", {"director_note": note})
            actual = preview["writing_length"]
            passed = actual["mode"] == expected_mode and (actual["min_words"], actual["max_words"]) == expected_range
            result["overrides"][label] = {"passed": passed, "actual": actual, "expected": [expected_mode, *expected_range]}

        mode_failures = [
            f"{mode} sample {index + 1}: {sample['word_count']} words"
            for mode, data in result["modes"].items()
            for index, sample in enumerate(data["samples"])
            if not sample["within_acceptance"]
        ]
        version_failures = [item["mode"] for item in result["versions"] if not item["same_scene"] or not item["length_preserved"]]
        override_failures = [name for name, item in result["overrides"].items() if not item["passed"]]
        result["errors"] = mode_failures + [f"version contract: {name}" for name in version_failures] + [f"override: {name}" for name in override_failures]
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        request("/settings/model", "PUT", original)
        result["settings_restored"] = request("/settings/model") == original
        result["deletions"] = [delete_story(session_id) for session_id in reversed(created)]
        result["finished_at"] = utc_now()
        result["passed"] = not result["errors"] and result["settings_restored"] and all(item.get("status") == "completed" for item in result["deletions"])
        METRICS.parent.mkdir(parents=True, exist_ok=True)
        METRICS.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Metrics: {METRICS}")
    print("PASS" if result["passed"] else "FAIL")
    for error in result["errors"]:
        print(f"  {error}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
