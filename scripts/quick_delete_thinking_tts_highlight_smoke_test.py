import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import DB_PATH  # noqa: E402
from app.database import init_db  # noqa: E402


BASE_URL = "http://localhost:8001"
REPORT = ROOT / "backend" / "data" / "logs" / "QUICK_DELETE_THINKING_TTS_HIGHLIGHT_REPORT.md"


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 20.0):
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


def wait_delete_job(delete_result: dict, *, timeout: float = 90.0) -> dict:
    job_id = delete_result.get("job_id")
    if not job_id:
        return delete_result
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = request_json(f"/sessions/delete-jobs/{urllib.parse.quote(job_id, safe='')}", timeout=10)
        if job.get("status") == "completed":
            return {
                **delete_result,
                "counts": job.get("counts") or {},
                "files_deleted": job.get("files_deleted") or [],
                "files_skipped": job.get("files_skipped") or [],
                "deleted_file_bytes": job.get("deleted_file_bytes", 0),
            }
        if job.get("status") == "failed":
            raise RuntimeError(f"Delete job failed: {job.get('error')}")
        time.sleep(0.5)
    raise TimeoutError(f"Delete job {job_id} did not finish within {timeout} seconds.")


def request_expect_http(path: str, *, status_code: int, method: str = "GET", timeout: float = 10.0) -> None:
    request = urllib.request.Request(f"{BASE_URL}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != status_code:
                raise RuntimeError(f"Expected HTTP {status_code}, got {response.status} for {method} {path}")
    except urllib.error.HTTPError as error:
        if error.code != status_code:
            detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
            raise RuntimeError(f"Expected HTTP {status_code}, got {error.code} for {method} {path}: {detail}") from error


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def insert_temp_story_rows(session_id: str) -> dict[str, str]:
    ids = {
        "scene_id": f"smoke-scene-{uuid4()}",
        "version_id": f"smoke-version-{uuid4()}",
        "image_id": f"smoke-image-{uuid4()}",
        "summary_id": f"smoke-summary-{uuid4()}",
        "memory_id": f"smoke-memory-{uuid4()}",
        "quality_id": f"smoke-quality-{uuid4()}",
        "checklist_id": f"smoke-checklist-{uuid4()}",
        "state_run_id": f"smoke-state-run-{uuid4()}",
        "character_state_id": f"smoke-character-state-{uuid4()}",
        "relationship_id": f"smoke-relationship-{uuid4()}",
        "world_state_id": f"smoke-world-state-{uuid4()}",
        "scene_state_id": f"smoke-scene-state-{uuid4()}",
        "object_state_id": f"smoke-object-state-{uuid4()}",
        "plot_id": f"smoke-plot-{uuid4()}",
        "snapshot_id": f"smoke-snapshot-{uuid4()}",
        "image_settings_id": f"smoke-scene-image-settings-{uuid4()}",
        "image_prompt_id": f"smoke-image-prompt-{uuid4()}",
    }
    image_path = str(ROOT / "backend" / "data" / "generated_images" / f"{ids['image_id']}.png")
    ids["image_path"] = image_path
    Path(image_path).parent.mkdir(parents=True, exist_ok=True)
    Path(image_path).write_bytes(b"quick-delete-smoke-image")
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                ids["scene_id"],
                session_id,
                "Smoke test director note.",
                "The smoke test scene exists only to verify delete cascades.",
                "continue",
            ),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["version_id"],
                ids["scene_id"],
                session_id,
                "Smoke test director note.",
                "The smoke test version exists only to verify delete cascades.",
                "continue",
                1,
            ),
        )
        db.execute(
            """
            INSERT INTO generated_images (id, session_id, scene_id, version_id, workflow_id, workflow_name, prompt, image_path, image_url, status, is_primary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["image_id"],
                session_id,
                ids["scene_id"],
                ids["version_id"],
                "smoke-workflow",
                "Smoke Workflow",
                "Smoke image prompt",
                image_path,
                f"/generated-images/{Path(image_path).name}",
                "accepted",
                1,
            ),
        )
        db.execute(
            "INSERT INTO session_image_settings (session_id, selected_workflow_id) VALUES (?, ?)",
            (session_id, "smoke-workflow"),
        )
        db.execute(
            """
            INSERT INTO scene_image_settings (id, session_id, scene_id, version_id, selected_workflow_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ids["image_settings_id"], session_id, ids["scene_id"], ids["version_id"], "smoke-workflow"),
        )
        db.execute(
            """
            INSERT INTO scene_image_prompts (id, session_id, scene_id, version_id, workflow_id, workflow_name, visual_beat, prompt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["image_prompt_id"],
                session_id,
                ids["scene_id"],
                ids["version_id"],
                "smoke-workflow",
                "Smoke Workflow",
                "Smoke visual beat",
                "Smoke prompt",
            ),
        )
        db.execute(
            """
            INSERT INTO session_summaries (id, session_id, summary_text, from_scene_id, to_scene_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ids["summary_id"], session_id, "Smoke summary", ids["scene_id"], ids["scene_id"]),
        )
        db.execute(
            """
            INSERT INTO story_memories (id, session_id, memory_type, title, content, importance, keywords_json, source_scene_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["memory_id"],
                session_id,
                "note",
                "Smoke memory",
                "Temporary memory",
                1,
                "[]",
                ids["scene_id"],
            ),
        )
        db.execute(
            """
            INSERT INTO session_quality_notes (id, session_id, scene_id, version_id, note_type, severity, status, note_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ids["quality_id"], session_id, ids["scene_id"], ids["version_id"], "other", "low", "open", "Smoke note"),
        )
        db.execute(
            """
            INSERT INTO scene_quality_checklists (id, session_id, scene_id, version_id, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ids["checklist_id"], session_id, ids["scene_id"], ids["version_id"], "Smoke checklist"),
        )
        db.execute(
            """
            INSERT INTO story_state_runs (id, session_id, scene_id, version_id, status, raw_response)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ids["state_run_id"], session_id, ids["scene_id"], ids["version_id"], "completed", "{}"),
        )
        db.execute(
            """
            INSERT INTO character_live_state (id, session_id, character_name, state_type, key, value, source_scene_id, source_version_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["character_state_id"],
                session_id,
                "Smoke",
                "general",
                "status",
                "temporary",
                ids["scene_id"],
                ids["version_id"],
            ),
        )
        db.execute(
            """
            INSERT INTO relationship_state (id, session_id, character_a_name, character_b_name, relationship_key, content, source_scene_id, source_version_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["relationship_id"],
                session_id,
                "Smoke A",
                "Smoke B",
                "relationship",
                "temporary",
                ids["scene_id"],
                ids["version_id"],
            ),
        )
        db.execute(
            """
            INSERT INTO world_live_state (id, session_id, state_type, key, value, source_scene_id, source_version_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ids["world_state_id"], session_id, "world", "weather", "temporary", ids["scene_id"], ids["version_id"]),
        )
        db.execute(
            """
            INSERT INTO scene_live_state (id, session_id, scene_id, version_id, state_type, key, value, source_scene_id, source_version_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["scene_state_id"],
                session_id,
                ids["scene_id"],
                ids["version_id"],
                "scene",
                "location",
                "temporary",
                ids["scene_id"],
                ids["version_id"],
            ),
        )
        db.execute(
            """
            INSERT INTO object_state (id, session_id, object_key, name, state_type, value, source_scene_id, source_version_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["object_state_id"],
                session_id,
                "smoke_object",
                "Smoke Object",
                "object",
                "temporary",
                ids["scene_id"],
                ids["version_id"],
            ),
        )
        db.execute(
            """
            INSERT INTO plot_threads (id, session_id, thread_key, title, status, content, source_scene_id, source_version_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ids["plot_id"],
                session_id,
                "smoke_thread",
                "Smoke Thread",
                "active",
                "temporary",
                ids["scene_id"],
                ids["version_id"],
            ),
        )
        db.execute(
            "INSERT INTO state_snapshots (id, session_id, run_id, snapshot_json) VALUES (?, ?, ?, ?)",
            (ids["snapshot_id"], session_id, ids["state_run_id"], "{}"),
        )
    return ids


def story_row_counts(session_id: str) -> dict[str, int]:
    tables = [
        "sessions",
        "scenes",
        "scene_versions",
        "generated_images",
        "session_image_settings",
        "scene_image_settings",
        "scene_image_prompts",
        "session_summaries",
        "story_memories",
        "session_quality_notes",
        "scene_quality_checklists",
        "story_state_runs",
        "character_live_state",
        "relationship_state",
        "world_live_state",
        "scene_live_state",
        "object_state",
        "plot_threads",
        "state_snapshots",
    ]
    with db_connect() as db:
        counts = {}
        for table in tables:
            if table == "sessions":
                row = db.execute("SELECT COUNT(*) AS count FROM sessions WHERE id = ?", (session_id,)).fetchone()
            else:
                row = db.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE session_id = ?", (session_id,)).fetchone()
            counts[table] = int(row["count"] if row else 0)
    return counts


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def append_report(lines: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    section = "\n".join(lines).rstrip() + "\n"
    REPORT.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")


def main() -> int:
    print("StoryDriver quick-delete / writing mode / TTS highlight smoke test")
    init_db()
    health = request_json("/health", timeout=8)
    assert_true(isinstance(health, dict) and bool(health.get("ok")), "Backend health is not OK.")

    model_settings = request_json("/settings/model", timeout=10)
    assert_true(model_settings.get("writing_path") in {"direct_writer", "deliberate_pipeline"}, "Writing path missing from model settings.")
    assert_true(isinstance(model_settings.get("app_planning_enabled"), bool), "App Planning setting missing from model settings.")
    assert_true(isinstance(model_settings.get("chapter_extension_enabled"), bool), "Chapter Extension setting missing from model settings.")
    assert_true(model_settings.get("adherence_check_mode") in {"off", "warn", "retry_once"}, "Adherence Check setting missing from model settings.")

    tts_settings = request_json("/settings/tts", timeout=10)
    assert_true(tts_settings.get("tts_follow_mode") in {"off", "sentence", "phrase", "word_estimate", "exact_word"}, "TTS follow mode missing from TTS settings.")

    primary = request_json("/sessions", method="POST", payload={"title": "Smoke Keep Story"}, timeout=10)
    target = request_json("/sessions", method="POST", payload={"title": "Smoke Delete Story"}, timeout=10)
    primary_id = primary["id"]
    target_id = target["id"]
    print(f"[setup] keep={primary_id} delete={target_id}")
    inserted_ids = insert_temp_story_rows(target_id)

    try:
        pipeline_preview = request_json(
            f"/sessions/{primary_id}/prose-prompt-preview",
            method="POST",
            payload={
                "director_note": "Write a short grounded beat.",
                "mode": "continue",
                "app_planning_enabled_override": False,
            },
            timeout=20,
        )
        assert_true(pipeline_preview.get("writing_path") == "deliberate_pipeline", "Deliberate Pipeline preview did not resolve.")
        assert_true(pipeline_preview.get("app_planning_enabled") is True, "Structured planning should be mandatory in preview.")
        assert_true(bool(pipeline_preview.get("scene_plan")), "Deliberate Pipeline preview did not create a structured plan.")
        assert_true("STRUCTURED SCENE PLAN" in (pipeline_preview.get("user_prompt") or ""), "Deliberate Pipeline prompt missing structured plan.")

        planning_preview = request_json(
            f"/sessions/{primary_id}/prose-prompt-preview",
            method="POST",
            payload={
                "director_note": "Write a careful scene with visible planning.",
                "mode": "continue",
                "app_planning_enabled_override": True,
            },
            timeout=20,
        )
        assert_true(planning_preview.get("writing_process_mode") == "structured_plan_write_review_repair", "Deliberate Pipeline preview did not resolve.")
        assert_true(bool(planning_preview.get("scene_plan")), "Structured planning preview did not create a plan.")
        assert_true("STRUCTURED SCENE PLAN" in (planning_preview.get("user_prompt") or ""), "Structured planning prompt missing plan.")

        chapter_preview = request_json(
            f"/sessions/{primary_id}/prose-prompt-preview",
            method="POST",
            payload={
                "director_note": "Write the first chapter.",
                "mode": "continue",
                "writing_length_mode_override": "chapter",
                "app_planning_enabled_override": False,
            },
            timeout=20,
        )
        assert_true(chapter_preview.get("writing_process_mode") == "structured_plan_write_review_repair", "Chapter preview did not use the Deliberate Pipeline.")
        assert_true((chapter_preview.get("writing_length") or {}).get("mode") == "chapter", "Chapter preview did not use chapter length.")
        assert_true(bool(chapter_preview.get("scene_plan")), "Chapter length did not create a structured plan.")

        updated_tts = {**tts_settings, "tts_follow_mode": "phrase", "tts_follow_highlight": "phrase", "highlight_narration": True}
        saved_tts = request_json("/settings/tts", method="PUT", payload=updated_tts, timeout=10)
        assert_true(saved_tts.get("tts_follow_mode") == "phrase", "TTS follow mode did not persist.")

        before_counts = story_row_counts(target_id)
        delete_result = request_json(
            f"/sessions/{urllib.parse.quote(target_id, safe='')}?permanent=true",
            method="DELETE",
            timeout=20,
        )
        delete_result = wait_delete_job(delete_result)
        after_counts = story_row_counts(target_id)
        primary_counts = story_row_counts(primary_id)
        request_expect_http(f"/sessions/{urllib.parse.quote(target_id, safe='')}/scenes", status_code=404)

        assert_true(delete_result.get("id") == target_id, "Delete route returned the wrong story id.")
        assert_true(delete_result.get("permanent") is True, "Delete route did not report permanent deletion.")
        assert_true(delete_result.get("counts", {}).get("scenes", 0) >= 1, "Delete response did not count scene rows.")
        assert_true(delete_result.get("counts", {}).get("scene_versions", 0) >= 1, "Delete response did not count version rows.")
        assert_true(delete_result.get("counts", {}).get("generated_image_files_deleted", 0) >= 1, "Delete route did not delete story-owned generated image files.")
        assert_true(not Path(inserted_ids["image_path"]).exists(), "Story-owned generated image file still exists after permanent delete.")
        assert_true(all(value == 0 for value in after_counts.values()), f"Permanent delete left rows behind: {after_counts}")
        assert_true(primary_counts.get("sessions") == 1, "Deleting a different story removed the keep story.")

        lines = [
            f"## Smoke Test {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "- Quick-delete backend route: PASS",
            "- Permanent delete cascade counts: PASS",
            "- Non-target story preserved: PASS",
            "- Deliberate Pipeline preview contract: PASS",
            "- TTS Follow Mode persistence: PASS",
            "- Generated media file policy: DB records and story-owned generated files removed.",
            f"- Deleted story id: `{target_id}`",
            f"- Preserved story id: `{primary_id}`",
            f"- Inserted scene id: `{inserted_ids['scene_id']}`",
            f"- Pre-delete counts: `{json.dumps(before_counts, sort_keys=True)}`",
            f"- Post-delete counts: `{json.dumps(after_counts, sort_keys=True)}`",
        ]
        append_report(lines)
        print("[OK] targeted smoke test passed")
    finally:
        try:
            request_json("/settings/tts", method="PUT", payload=tts_settings, timeout=10)
        except Exception as error:
            print(f"[WARN] Could not restore original TTS settings: {error}")
        for session_id in (target_id, primary_id):
            try:
                cleanup_result = request_json(
                    f"/sessions/{urllib.parse.quote(session_id, safe='')}?permanent=true",
                    method="DELETE",
                    timeout=20,
                )
                wait_delete_job(cleanup_result, timeout=30)
            except Exception:
                with db_connect() as db:
                    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[FAIL] {error}")
        sys.exit(1)
