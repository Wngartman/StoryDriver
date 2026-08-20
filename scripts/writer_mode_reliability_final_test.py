from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "backend" / "data" / "logs" / "WRITER_MODE_RELIABILITY_FINAL_REPORT.md"
BASE_URL = "http://localhost:8001"


def request_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"{method} {path} failed with HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"{method} {path} could not reach StoryDriver: {error}") from error


def append_report(lines: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    section = "\n".join(lines).rstrip() + "\n"
    REPORT.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def is_generic_title(title: str | None) -> bool:
    normalized = (title or "").strip().lower()
    return normalized in {"", "untitled story", "new story", "untitled", "blank story"} or bool(
        re.match(r"^(untitled|new story)(\s+\d+)?$", normalized)
    )


def generate_stream(session_id: str, director_note: str, *, mode: str = "continue", target_scene_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    payload: dict[str, Any] = {
        "director_note": director_note,
        "mode": mode,
        "client_submitted_at": now_iso(),
    }
    if target_scene_id:
        payload["target_scene_id"] = target_scene_id
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    status_events: list[dict[str, Any]] = []
    warnings: list[str] = []
    first_delta_at: float | None = None
    deltas = 0
    streamed_chars = 0
    final_scene: dict[str, Any] | None = None
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=900) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type")
            if event_type == "status":
                status_events.append(event)
                print(f"[{mode}] {event.get('stage')}: {event.get('message')}")
            elif event_type == "warning":
                warnings.append(str(event.get("message") or event))
                print(f"[{mode} warning] {event.get('message')}")
            elif event_type == "delta":
                if first_delta_at is None:
                    first_delta_at = time.perf_counter()
                text = event.get("text") or ""
                deltas += 1
                streamed_chars += len(text)
            elif event_type == "scene":
                final_scene = event.get("scene")
            elif event_type == "error":
                raise RuntimeError(event.get("detail") or "generation stream failed")
    if not final_scene:
        raise RuntimeError(f"{mode} did not return a final scene event")
    return final_scene, {
        "mode": mode,
        "status_events": status_events,
        "warnings": warnings,
        "deltas": deltas,
        "streamed_chars": streamed_chars,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "first_visible_seconds": round(first_delta_at - started, 3) if first_delta_at else None,
    }


def poll(label: str, fetcher, predicate, *, timeout: float = 120.0, interval: float = 3.0) -> Any:
    deadline = time.perf_counter() + timeout
    latest = None
    while time.perf_counter() < deadline:
        latest = fetcher()
        if predicate(latest):
            print(f"[OK] {label}")
            return latest
        time.sleep(interval)
    print(f"[WARN] {label} not ready before timeout")
    return latest


def set_writing_length(mode: str) -> dict[str, Any]:
    settings = request_json("/settings/model", timeout=10)
    updated = dict(settings)
    updated["writing_length_mode"] = mode
    request_json("/settings/model", method="PUT", payload=updated, timeout=15)
    return settings


def restore_model_settings(settings: dict[str, Any]) -> None:
    request_json("/settings/model", method="PUT", payload=settings, timeout=15)


def wait_delete_job(job_id: str, *, timeout: float = 90.0) -> dict[str, Any]:
    return poll(
        f"delete job {job_id}",
        lambda: request_json(f"/sessions/delete-jobs/{job_id}", timeout=10),
        lambda job: isinstance(job, dict) and job.get("status") in {"completed", "failed", "cancelled"},
        timeout=timeout,
        interval=1.5,
    )


def delete_session(session_id: str, *, label: str) -> dict[str, Any]:
    response = request_json(f"/sessions/{session_id}?permanent=true", method="DELETE", timeout=20)
    job_id = response.get("job_id") or response.get("deletion_job_id")
    if not job_id:
        raise RuntimeError(f"{label} delete did not return a job id: {response}")
    job = wait_delete_job(job_id)
    if job.get("status") != "completed":
        raise RuntimeError(f"{label} delete job did not complete: {job}")
    return job


def scene_generation_stats(scene: dict[str, Any], stream: dict[str, Any]) -> dict[str, Any]:
    stats = scene.get("generation_stats") or {}
    return {
        "mode": stream["mode"],
        "word_count": word_count(scene.get("generated_text") or ""),
        "deltas": stream["deltas"],
        "elapsed_seconds": stream["elapsed_seconds"],
        "first_visible_seconds": stats.get("first_visible_token_seconds") or stream.get("first_visible_seconds"),
        "visible_tps": stats.get("visible_tokens_per_second") or stats.get("tokens_per_second"),
        "hidden_reasoning_chars": stats.get("hidden_reasoning_chars") or stats.get("reasoning_chars") or 0,
        "model": stats.get("model") or stats.get("active_model") or "",
    }


def main() -> int:
    print("StoryDriver writer-mode final reliability workflow")
    health = request_json("/health", timeout=8)
    if not health or not health.get("ok"):
        raise RuntimeError("Backend health is not OK")
    image_settings = request_json("/settings/image", timeout=10)
    if image_settings.get("image_generation_mode") != "paused":
        raise RuntimeError(f"Image generation is not paused: {image_settings.get('image_generation_mode')}")

    original_settings = request_json("/settings/model", timeout=10)
    created_session_ids: list[str] = []
    generated_rows: list[dict[str, Any]] = []
    results: dict[str, Any] = {
        "started_at": now_iso(),
        "images_paused": True,
        "failures": [],
        "warnings": [],
    }
    main_session_id = ""
    quick_delete_session_id = ""
    try:
        main_session = request_json("/sessions", method="POST", payload={"title": "Untitled Story"}, timeout=15)
        main_session_id = main_session["id"]
        created_session_ids.append(main_session_id)
        quick_delete = request_json(
            "/sessions",
            method="POST",
            payload={"title": "Writer Reliability Quick Delete Target"},
            timeout=15,
        )
        quick_delete_session_id = quick_delete["id"]
        created_session_ids.append(quick_delete_session_id)
        print(f"Main test story: {main_session_id}")
        print(f"Quick-delete target story: {quick_delete_session_id}")

        chapter_note = (
            "Write a long first chapter for a grounded modern-fantasy border-town mystery. "
            "Introduce adult sisters Mara Vale, Rina Vale, and Tessa Vale with distinct visual details, histories, "
            "opinions, and emotional stakes. Mara has the old brass station key, Rina carries a blue hospital keycard, "
            "and Tessa keeps their mother's torn map folded inside her jacket. They meet in a storm-lit abandoned train "
            "station to decide whether to rescue a missing girl from a militia checkpoint. Keep the action spatially clear, "
            "make the relationship history matter, and write the chapter as narrated prose only."
        )
        set_writing_length("chapter")
        first_scene, first_stream = generate_stream(main_session_id, chapter_note)
        generated_rows.append(scene_generation_stats(first_scene, first_stream))
        first_scene_id = first_scene["id"]
        first_version_id = first_scene.get("active_version_id") or ((first_scene.get("versions") or [{}])[-1].get("id"))

        titled = poll(
            "auto-title",
            lambda: request_json(f"/sessions/{main_session_id}", timeout=10),
            lambda payload: isinstance(payload, dict)
            and payload.get("auto_title_status") in {"generated", "user_set"}
            and not is_generic_title(payload.get("title")),
            timeout=120,
            interval=3,
        )
        results["title"] = titled.get("title") if isinstance(titled, dict) else ""
        results["auto_title_status"] = titled.get("auto_title_status") if isinstance(titled, dict) else ""
        if is_generic_title(results["title"]):
            results["failures"].append("Auto-title stayed generic after first scene.")

        set_writing_length("scene")
        continuation_notes = [
            "Continue with the sisters entering the records office. Preserve who carries the brass key, the keycard, and the torn map.",
            "Continue with a tense conversation where Rina admits what she knows about the missing girl's clinic visit.",
            "Continue with the group leaving the station and making a concrete plan for the checkpoint approach.",
        ]
        scenes = [first_scene]
        for note in continuation_notes:
            scene, stream = generate_stream(main_session_id, note, mode="continue")
            scenes.append(scene)
            generated_rows.append(scene_generation_stats(scene, stream))

        rewrite_scene, rewrite_stream = generate_stream(
            main_session_id,
            "Rewrite this scene to sharpen the sisters' emotional disagreement while preserving the same facts and object ownership.",
            mode="rewrite",
            target_scene_id=scenes[1]["id"],
        )
        generated_rows.append(scene_generation_stats(rewrite_scene, rewrite_stream))

        regenerate_scene, regenerate_stream = generate_stream(
            main_session_id,
            "Regenerate this scene with clearer spatial blocking and no assistant-style ending.",
            mode="regenerate",
            target_scene_id=scenes[2]["id"],
        )
        generated_rows.append(scene_generation_stats(regenerate_scene, regenerate_stream))

        scene_list = request_json(f"/sessions/{main_session_id}/scenes", timeout=15)
        results["scene_count_after_versions"] = len(scene_list)
        results["version_counts"] = {scene["id"]: len(scene.get("versions") or []) for scene in scene_list}
        if len(scene_list) != 4:
            results["failures"].append(f"Expected 4 scenes after continuations, found {len(scene_list)}.")
        if results["version_counts"].get(scenes[1]["id"], 0) < 2 or results["version_counts"].get(scenes[2]["id"], 0) < 2:
            results["failures"].append("Rewrite/regenerate did not create additional versions on target scenes.")

        state = poll(
            "story state extraction",
            lambda: request_json(f"/sessions/{main_session_id}/story-state", timeout=20),
            lambda payload: isinstance(payload, dict)
            and (payload.get("latest_run") or {}).get("status") in {"completed", "skipped", "failed"},
            timeout=180,
            interval=5,
        )
        latest_run = (state or {}).get("latest_run") or {}
        results["story_state_status"] = latest_run.get("status")
        results["story_state_error"] = latest_run.get("error")
        results["story_state_counts"] = {
            "prompt_item_count": (state or {}).get("prompt_item_count"),
            "objects": len((state or {}).get("objects") or []),
            "relationships": len((state or {}).get("relationships") or []),
            "emotional_memories": len((state or {}).get("emotional_memories") or []),
            "conflicts": len((state or {}).get("conflicts") or []),
        }
        if latest_run.get("status") == "failed":
            results["failures"].append(f"Story State extraction failed: {latest_run.get('error')}")
        if not results["story_state_counts"]["objects"]:
            results["warnings"].append("Story State did not expose object rows for the final test story.")
        if not (results["story_state_counts"]["relationships"] or results["story_state_counts"]["emotional_memories"]):
            results["warnings"].append("Story State relationship/emotional memory rows were thin for the final test story.")

        tts_payload = {
            "text": (first_scene.get("generated_text") or "")[:8000],
            "provider": "kokoro",
            "voice_profile_id": "natural_female_narrator",
            "session_id": main_session_id,
            "scene_id": first_scene_id,
            "version_id": first_version_id,
            "follow_mode": "sentence",
        }
        tts_started = time.perf_counter()
        tts_result = request_json("/tts/synthesize", method="POST", payload=tts_payload, timeout=180)
        results["tts"] = {
            "audio_url": tts_result.get("audio_url"),
            "cached": tts_result.get("cached"),
            "duration": tts_result.get("duration"),
            "synthesis_seconds": tts_result.get("synthesis_seconds"),
            "wall_seconds": round(time.perf_counter() - tts_started, 3),
        }
        if not tts_result.get("audio_url"):
            results["failures"].append("Kokoro long-scene synthesis did not return audio_url.")

        selected_before = request_json(f"/sessions/{main_session_id}", timeout=10)
        quick_job = delete_session(quick_delete_session_id, label="quick-delete target")
        selected_after = request_json(f"/sessions/{main_session_id}", timeout=10)
        results["quick_delete_non_selected"] = {
            "deleted_session_id": quick_delete_session_id,
            "job_id": quick_job.get("job_id"),
            "selected_story_still_available": selected_before.get("id") == selected_after.get("id") == main_session_id,
        }
        if not results["quick_delete_non_selected"]["selected_story_still_available"]:
            results["failures"].append("Deleting a non-selected test story affected the main test story.")

        long_delete_job = delete_session(main_session_id, label="long generated test story")
        results["long_test_story_delete"] = {
            "session_id": main_session_id,
            "job_id": long_delete_job.get("job_id"),
            "rows_deleted": long_delete_job.get("rows_deleted"),
            "deleted_counts": long_delete_job.get("deleted_counts"),
            "deleted_file_bytes": long_delete_job.get("deleted_file_bytes"),
            "files_deleted": len(long_delete_job.get("files_deleted") or []),
            "files_skipped": len(long_delete_job.get("files_skipped") or []),
        }
        gone_check = request_json("/sessions", timeout=15)
        if any(item.get("id") == main_session_id for item in gone_check):
            results["failures"].append("Long generated test story still appears in session list after delete.")

        results["generated"] = generated_rows
        results["finished_at"] = now_iso()
    finally:
        try:
            restore_model_settings(original_settings)
            results["settings_restored"] = True
        except Exception as error:
            results["settings_restored"] = False
            results.setdefault("failures", []).append(f"Could not restore model settings: {error}")
        for session_id in created_session_ids:
            try:
                sessions = request_json("/sessions", timeout=10)
                if any(item.get("id") == session_id for item in sessions):
                    delete_session(session_id, label=f"cleanup {session_id}")
            except Exception as error:
                results.setdefault("warnings", []).append(f"Cleanup for {session_id} did not complete: {error}")

    lines = [
        f"## End-to-End Writer Workflow Harness - {now_iso()}",
        "",
        f"- main_test_session_id: `{main_session_id}`",
        f"- quick_delete_target_session_id: `{quick_delete_session_id}`",
        f"- generated_title: `{results.get('title', '')}` (`{results.get('auto_title_status', '')}`)",
        f"- scene_count_after_versions: `{results.get('scene_count_after_versions')}`",
        f"- version_counts: `{json.dumps(results.get('version_counts', {}), sort_keys=True)}`",
        f"- story_state_status: `{results.get('story_state_status')}`",
        f"- story_state_counts: `{json.dumps(results.get('story_state_counts', {}), sort_keys=True)}`",
        f"- tts: `{json.dumps(results.get('tts', {}), sort_keys=True)}`",
        f"- quick_delete_non_selected: `{json.dumps(results.get('quick_delete_non_selected', {}), sort_keys=True)}`",
        f"- long_test_story_delete: `{json.dumps(results.get('long_test_story_delete', {}), sort_keys=True)}`",
        f"- settings_restored: `{results.get('settings_restored')}`",
        "",
        "### Generation Metrics",
    ]
    for row in generated_rows:
        lines.append(
            "- "
            + ", ".join(
                [
                    f"mode={row.get('mode')}",
                    f"words={row.get('word_count')}",
                    f"visible_tps={row.get('visible_tps')}",
                    f"first_visible={row.get('first_visible_seconds')}",
                    f"hidden_reasoning_chars={row.get('hidden_reasoning_chars')}",
                    f"model={row.get('model')}",
                ]
            )
        )
    failure_lines = [f"- {item}" for item in results.get("failures", [])] or ["- none"]
    warning_lines = [f"- {item}" for item in results.get("warnings", [])] or ["- none"]
    lines.extend(["", "### Failures", *failure_lines])
    lines.extend(["", "### Warnings", *warning_lines])
    append_report(lines)

    print(json.dumps(results, indent=2, sort_keys=True))
    return 1 if results.get("failures") else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[ABORTED] interrupted")
        sys.exit(130)
    except Exception as error:
        append_report(
            [
                f"## End-to-End Writer Workflow Harness Failure - {now_iso()}",
                "",
                f"- error: `{error}`",
            ]
        )
        print(f"[FAIL] {error}")
        sys.exit(1)
