import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import DATA_DIR  # noqa: E402
from app.database import get_connection, init_db  # noqa: E402
from app.services.story_delete_jobs import (  # noqa: E402
    SESSION_DELETE_TABLES,
    get_delete_job,
    recover_stale_delete_jobs_on_startup,
    resume_delete_job,
    start_session_delete_job,
)


REPORT = DATA_DIR / "logs" / "SAFE_LONG_STORY_DELETE_REPORT.md"
PERSISTENCE_REPORT = DATA_DIR / "logs" / "DELETE_JOB_PERSISTENCE_REPORT.md"
GENERATED_IMAGE_DIR = DATA_DIR / "generated_images"
GENERATED_AUDIO_DIR = DATA_DIR / "generated_audio"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def backend_health_available() -> bool | None:
    request = urllib.request.Request("http://localhost:8001/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return None


def write_report(lines: list[str]) -> None:
    for report_path in (REPORT, PERSISTENCE_REPORT):
        report_path.parent.mkdir(parents=True, exist_ok=True)
        existing = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        section = "\n".join(lines).rstrip() + "\n"
        report_path.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")


def write_persistence_report(lines: list[str]) -> None:
    PERSISTENCE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    existing = PERSISTENCE_REPORT.read_text(encoding="utf-8") if PERSISTENCE_REPORT.exists() else ""
    section = "\n".join(lines).rstrip() + "\n"
    PERSISTENCE_REPORT.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")


def count_story_rows(session_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with get_connection() as db:
        row = db.execute("SELECT COUNT(*) AS count FROM sessions WHERE id = ?", (session_id,)).fetchone()
        counts["sessions"] = int(row["count"] if row else 0)
        for table_name in SESSION_DELETE_TABLES:
            row = db.execute(
                f"SELECT COUNT(*) AS count FROM {table_name} WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            counts[table_name] = int(row["count"] if row else 0)
    return counts


def create_synthetic_story(scenes: int, versions_per_scene: int) -> dict:
    GENERATED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    session_id = f"delete-stress-{uuid4()}"
    keep_session_id = f"delete-keep-{uuid4()}"
    character_id = f"delete-stress-character-{uuid4()}"
    created_files: list[Path] = []
    target_files: list[Path] = []

    unrelated_generated_file = GENERATED_IMAGE_DIR / f"delete_stress_unrelated_{uuid4().hex}.png"
    unrelated_generated_file.write_bytes(b"not story owned")
    created_files.append(unrelated_generated_file)

    keep_image = GENERATED_IMAGE_DIR / f"delete_stress_keep_{uuid4().hex}.png"
    keep_image.write_bytes(b"keep")
    created_files.append(keep_image)

    with get_connection() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, "Delete Stress Target"))
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (keep_session_id, "Delete Stress Keep"))
        db.execute(
            "INSERT INTO characters (id, name, role) VALUES (?, ?, ?)",
            (character_id, "Delete Stress Character", "synthetic cleanup test"),
        )
        db.execute(
            "INSERT INTO session_characters (session_id, character_id, is_active) VALUES (?, ?, ?)",
            (session_id, character_id, 1),
        )
        db.execute(
            "INSERT INTO world_notes (session_id, setting, tone) VALUES (?, ?, ?)",
            (session_id, "Synthetic delete stress setting", "temporary"),
        )
        db.execute(
            "INSERT INTO session_image_settings (session_id, selected_workflow_id) VALUES (?, ?)",
            (session_id, "delete-stress-workflow"),
        )
        db.execute(
            "INSERT INTO session_image_settings (session_id, selected_workflow_id) VALUES (?, ?)",
            (keep_session_id, "delete-stress-keep-workflow"),
        )

        keep_scene_id = f"delete-keep-scene-{uuid4()}"
        keep_version_id = f"delete-keep-version-{uuid4()}"
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, ?)",
            (keep_scene_id, keep_session_id, "Keep note", "Keep text", "continue"),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (keep_version_id, keep_scene_id, keep_session_id, "Keep note", "Keep version", "continue", 1),
        )
        db.execute(
            """
            INSERT INTO generated_images (id, session_id, scene_id, version_id, workflow_id, workflow_name, prompt, image_path, image_url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"delete-keep-image-{uuid4()}",
                keep_session_id,
                keep_scene_id,
                keep_version_id,
                "delete-stress-keep-workflow",
                "Delete Stress Keep Workflow",
                "keep prompt",
                str(keep_image),
                f"/generated-images/{keep_image.name}",
                "accepted",
            ),
        )

        for scene_index in range(scenes):
            scene_id = f"delete-stress-scene-{scene_index:04d}-{uuid4()}"
            db.execute(
                "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, ?)",
                (
                    scene_id,
                    session_id,
                    f"Synthetic director note {scene_index}.",
                    f"Synthetic generated prose for delete stress scene {scene_index}.",
                    "continue",
                ),
            )
            db.execute(
                "INSERT INTO session_quality_notes (id, session_id, scene_id, note_text) VALUES (?, ?, ?, ?)",
                (f"delete-stress-quality-{uuid4()}", session_id, scene_id, "Temporary QA note."),
            )
            db.execute(
                "INSERT INTO story_memories (id, session_id, memory_type, title, content, importance, source_scene_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"delete-stress-memory-{uuid4()}",
                    session_id,
                    "note",
                    "Delete stress memory",
                    "Synthetic memory for deletion.",
                    1,
                    scene_id,
                ),
            )
            db.execute(
                "INSERT INTO session_summaries (id, session_id, summary_text, from_scene_id, to_scene_id) VALUES (?, ?, ?, ?, ?)",
                (f"delete-stress-summary-{uuid4()}", session_id, "Synthetic summary.", scene_id, scene_id),
            )

            for version_index in range(1, versions_per_scene + 1):
                version_id = f"delete-stress-version-{scene_index:04d}-{version_index}-{uuid4()}"
                db.execute(
                    """
                    INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        scene_id,
                        session_id,
                        f"Synthetic director note {scene_index}.",
                        f"Synthetic version {version_index} for scene {scene_index}.",
                        "continue" if version_index == 1 else "rewrite",
                        version_index,
                    ),
                )

                image_path = GENERATED_IMAGE_DIR / f"delete_stress_{scene_index:04d}_{version_index}_{uuid4().hex}.png"
                image_path.write_bytes(b"synthetic image")
                created_files.append(image_path)
                target_files.append(image_path)
                audio_path = GENERATED_AUDIO_DIR / f"kokoro_delete_stress_{scene_index:04d}_{version_index}_{uuid4().hex}.mp3"
                manifest_path = GENERATED_AUDIO_DIR / f"tts_manifest_delete_stress_{scene_index:04d}_{version_index}_{uuid4().hex}.json"
                audio_path.write_bytes(b"synthetic audio")
                manifest_path.write_text(
                    json.dumps(
                        {
                            "session_id": session_id,
                            "scene_id": scene_id,
                            "version_id": version_id,
                            "cache_key": f"delete-stress-{scene_index}-{version_index}",
                            "audio_path": str(audio_path),
                            "audio_url": f"/audio/{audio_path.name}",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                created_files.extend([audio_path, manifest_path])
                target_files.extend([audio_path, manifest_path])

                db.execute(
                    """
                    INSERT INTO generated_images (id, session_id, scene_id, version_id, workflow_id, workflow_name, prompt, image_path, image_url, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"delete-stress-image-{uuid4()}",
                        session_id,
                        scene_id,
                        version_id,
                        "delete-stress-workflow",
                        "Delete Stress Workflow",
                        "synthetic prompt",
                        str(image_path),
                        f"/generated-images/{image_path.name}",
                        "accepted",
                    ),
                )
                db.execute(
                    """
                    INSERT INTO scene_quality_checklists (id, session_id, scene_id, version_id, notes)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (f"delete-stress-checklist-{uuid4()}", session_id, scene_id, version_id, "Temporary checklist."),
                )
                run_id = f"delete-stress-run-{uuid4()}"
                db.execute(
                    """
                    INSERT INTO story_state_runs (id, session_id, scene_id, version_id, status, raw_response)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, session_id, scene_id, version_id, "completed", "{}"),
                )
                db.execute(
                    """
                    INSERT INTO character_live_state (id, session_id, character_id, character_name, key, value, source_scene_id, source_version_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"delete-stress-live-{uuid4()}",
                        session_id,
                        character_id,
                        "Delete Stress Character",
                        "status",
                        "temporary",
                        scene_id,
                        version_id,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO character_state_events (id, session_id, character_id, character_name, key, value, run_id, source_scene_id, source_version_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"delete-stress-event-{uuid4()}",
                        session_id,
                        character_id,
                        "Delete Stress Character",
                        "status",
                        "temporary",
                        run_id,
                        scene_id,
                        version_id,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO relationship_state (id, session_id, character_a_id, character_a_name, character_b_name, relationship_key, content, source_scene_id, source_version_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"delete-stress-relationship-{uuid4()}",
                        session_id,
                        character_id,
                        "Delete Stress Character",
                        "Other",
                        "temporary",
                        "Synthetic relationship.",
                        scene_id,
                        version_id,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO emotional_memories (id, session_id, memory_text, run_id, source_scene_id, source_version_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (f"delete-stress-emotion-{uuid4()}", session_id, "Synthetic emotion.", run_id, scene_id, version_id),
                )
                db.execute(
                    "INSERT INTO world_live_state (id, session_id, state_type, key, value, source_scene_id, source_version_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"delete-stress-world-{uuid4()}", session_id, "world", "weather", "temporary", scene_id, version_id),
                )
                db.execute(
                    "INSERT INTO scene_live_state (id, session_id, scene_id, version_id, state_type, key, value, source_scene_id, source_version_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"delete-stress-scene-state-{uuid4()}",
                        session_id,
                        scene_id,
                        version_id,
                        "scene",
                        "location",
                        "temporary",
                        scene_id,
                        version_id,
                    ),
                )
                db.execute(
                    "INSERT INTO object_state (id, session_id, object_key, name, state_type, value, source_scene_id, source_version_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"delete-stress-object-{uuid4()}",
                        session_id,
                        "synthetic_object",
                        "Synthetic Object",
                        "object",
                        "temporary",
                        scene_id,
                        version_id,
                    ),
                )
                db.execute(
                    "INSERT INTO plot_threads (id, session_id, thread_key, title, status, content, source_scene_id, source_version_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"delete-stress-thread-{uuid4()}",
                        session_id,
                        "synthetic_thread",
                        "Synthetic Thread",
                        "active",
                        "temporary",
                        scene_id,
                        version_id,
                    ),
                )
                db.execute(
                    "INSERT INTO state_snapshots (id, session_id, run_id, snapshot_json) VALUES (?, ?, ?, ?)",
                    (f"delete-stress-snapshot-{uuid4()}", session_id, run_id, "{}"),
                )

    return {
        "session_id": session_id,
        "keep_session_id": keep_session_id,
        "character_id": character_id,
        "target_files": target_files,
        "created_files": created_files,
        "keep_image": keep_image,
        "unrelated_generated_file": unrelated_generated_file,
    }


def wait_for_delete_job(job_id: str, timeout_seconds: float = 120.0) -> tuple[dict, dict]:
    started = time.perf_counter()
    health_checks = 0
    health_ok = 0
    last_rows_deleted = 0
    while time.perf_counter() - started < timeout_seconds:
        job = get_delete_job(job_id)
        if not job:
            raise RuntimeError(f"Delete job {job_id} disappeared.")
        health = backend_health_available()
        if health is not None:
            health_checks += 1
            if health:
                health_ok += 1
        last_rows_deleted = int(job.get("rows_deleted") or last_rows_deleted)
        if job["status"] in {"completed", "failed"}:
            return job, {
                "duration_seconds": round(time.perf_counter() - started, 3),
                "health_checks": health_checks,
                "health_ok": health_ok,
                "last_rows_deleted": last_rows_deleted,
            }
        time.sleep(0.25)
    raise TimeoutError(f"Delete job {job_id} did not finish within {timeout_seconds} seconds.")


def cleanup_synthetic_records(payload: dict) -> None:
    for session_id in (payload.get("session_id"), payload.get("keep_session_id")):
        if session_id:
            try:
                with get_connection() as db:
                    db.execute("DELETE FROM story_delete_jobs WHERE session_id = ?", (session_id,))
                    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            except Exception:
                pass
    if payload.get("character_id"):
        try:
            with get_connection() as db:
                db.execute("DELETE FROM characters WHERE id = ?", (payload["character_id"],))
        except Exception:
            pass
    for path in payload.get("created_files") or []:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def create_recovery_story() -> dict:
    session_id = f"delete-recovery-{uuid4()}"
    scene_id = f"delete-recovery-scene-{uuid4()}"
    version_id = f"delete-recovery-version-{uuid4()}"
    with get_connection() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, "Delete Recovery Target"))
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, ?)",
            (scene_id, session_id, "Recovery note", "Recovery prose", "continue"),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, scene_id, session_id, "Recovery note", "Recovery version", "continue", 1),
        )
    return {"session_id": session_id, "scene_id": scene_id, "version_id": version_id, "created_files": []}


def create_stale_delete_job(session_id: str) -> str:
    job_id = f"delete-recovery-job-{uuid4()}"
    now = utc_now()
    with get_connection() as db:
        db.execute(
            """
            INSERT INTO story_delete_jobs (
                job_id, session_id, title, permanent, status, stage, message,
                created_at, started_at, updated_at, counts_json, deleted_counts_json,
                referenced_files_json, files_deleted_json, files_skipped_json,
                total_tables
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                session_id,
                "Delete Recovery Target",
                1,
                "running",
                "deleting_rows",
                "Simulated backend restart while deleting.",
                now,
                now,
                now,
                "{}",
                "{}",
                "[]",
                "[]",
                "[]",
                len(SESSION_DELETE_TABLES),
            ),
        )
        db.execute(
            """
            UPDATE sessions
            SET deletion_status = 'deleting',
                deletion_job_id = ?,
                deletion_started_at = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (job_id, now, session_id),
        )
    return job_id


def session_deletion_status(session_id: str) -> str:
    with get_connection() as db:
        row = db.execute("SELECT deletion_status FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row["deletion_status"] if row else ""


def run_delete_job_persistence_test() -> None:
    payload = create_recovery_story()
    session_id = payload["session_id"]
    try:
        job_id = create_stale_delete_job(session_id)
        recovery = recover_stale_delete_jobs_on_startup()
        stale_job = get_delete_job(job_id)
        assert_true(stale_job is not None, "Stale delete job was not persisted.")
        assert_true(stale_job["status"] == "failed", f"Stale delete job was not marked failed: {stale_job}")
        assert_true(stale_job.get("recoverable"), "Stale delete job was not marked recoverable.")
        assert_true(
            session_deletion_status(session_id) == "delete_failed",
            "Deleting story was not surfaced as delete_failed after startup recovery.",
        )
        resumed = resume_delete_job(job_id)
        assert_true(resumed["status"] in {"queued", "running"}, f"Resume did not restart job: {resumed}")
        completed_job, timing = wait_for_delete_job(job_id, timeout_seconds=60.0)
        assert_true(completed_job["status"] == "completed", f"Resumed delete job failed: {completed_job.get('error')}")
        after_counts = count_story_rows(session_id)
        assert_true(all(value == 0 for value in after_counts.values()), f"Rows remain after resumed delete: {after_counts}")
        write_persistence_report(
            [
                f"## Delete Job Persistence Recovery Test - {utc_now()}",
                "",
                "- result: PASS",
                f"- synthetic story id: `{session_id}`",
                f"- job id: `{job_id}`",
                f"- startup recovery result: `{json.dumps(recovery, sort_keys=True)}`",
                f"- resumed status: `{completed_job['status']}`",
                f"- resume wait seconds: {timing['duration_seconds']}",
                f"- rows deleted by worker: {completed_job.get('rows_deleted')}",
                "- stale running job detected on startup: PASS",
                "- resume completed without corrupting DB: PASS",
            ]
        )
    finally:
        cleanup_synthetic_records(payload)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def cleanup_synthetic_delete_job_rows() -> int:
    with get_connection() as db:
        cursor = db.execute(
            """
            DELETE FROM story_delete_jobs
            WHERE session_id GLOB 'delete-stress-*'
               OR session_id GLOB 'delete-recovery-*'
            """
        )
        return int(cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and delete a synthetic long StoryDriver story safely.")
    parser.add_argument("--scenes", type=int, default=80, help="Synthetic scene count.")
    parser.add_argument("--versions", type=int, default=2, help="Synthetic versions per scene.")
    parser.add_argument(
        "--cleanup-synthetic-jobs",
        action="store_true",
        help="Remove completed synthetic delete-job rows from previous stress-test runs.",
    )
    args = parser.parse_args()

    init_db()
    if args.cleanup_synthetic_jobs:
        deleted = cleanup_synthetic_delete_job_rows()
        print(f"[OK] removed {deleted} synthetic delete-job rows")
        return 0

    payload = create_synthetic_story(args.scenes, args.versions)
    session_id = payload["session_id"]
    print(f"Created synthetic delete stress story: {session_id}")
    before_counts = count_story_rows(session_id)
    started = time.perf_counter()
    try:
        job = start_session_delete_job(session_id, permanent=True)
        print(f"Started delete job: {job['job_id']}")
        completed_job, timing = wait_for_delete_job(job["job_id"])
        assert_true(completed_job["status"] == "completed", f"Delete job failed: {completed_job.get('error')}")
        after_counts = count_story_rows(session_id)
        assert_true(all(value == 0 for value in after_counts.values()), f"Rows remain after delete: {after_counts}")
        remaining_target_files = [str(path) for path in payload["target_files"] if Path(path).exists()]
        assert_true(not remaining_target_files, f"Story-owned generated files remain: {remaining_target_files[:5]}")
        assert_true(Path(payload["keep_image"]).exists(), "Unrelated generated file referenced by another story was deleted.")
        assert_true(
            Path(payload["unrelated_generated_file"]).exists(),
            "Unreferenced generated file unrelated to the deleted story was deleted.",
        )
        keep_counts = count_story_rows(payload["keep_session_id"])
        assert_true(keep_counts["sessions"] == 1, "Keep story was removed by target delete.")
        total_duration = round(time.perf_counter() - started, 3)
        lines = [
            f"## Long Story Delete Stress Test - {utc_now()}",
            "",
            "- result: PASS",
            f"- synthetic story id: `{session_id}`",
            f"- scenes: {args.scenes}",
            f"- versions per scene: {args.versions}",
            f"- pre-delete row counts: `{json.dumps(before_counts, sort_keys=True)}`",
            f"- post-delete row counts: `{json.dumps(after_counts, sort_keys=True)}`",
            f"- job id: `{completed_job['job_id']}`",
            f"- job duration seconds: {timing['duration_seconds']}",
            f"- total script delete wait seconds: {total_duration}",
            f"- rows deleted by worker: {completed_job.get('rows_deleted')}",
            f"- target generated files deleted: {len(payload['target_files'])}",
            f"- backend health checks during job: {timing['health_ok']}/{timing['health_checks']}",
            f"- deleted image files: {completed_job.get('deleted_counts', {}).get('generated_image_files_deleted', 0)}",
            f"- deleted audio files: {completed_job.get('deleted_counts', {}).get('generated_audio_files_deleted', 0)}",
            "- unrelated generated files preserved: PASS",
            "- keep story preserved: PASS",
        ]
        write_report(lines)
        run_delete_job_persistence_test()
        print("[OK] long-story delete stress test passed")
        return 0
    finally:
        cleanup_synthetic_records(payload)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[FAIL] {error}")
        sys.exit(1)
