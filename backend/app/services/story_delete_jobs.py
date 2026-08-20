from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from app.config import DATA_DIR
from app.database import get_connection
from app.services.generated_file_cleanup import (
    collect_story_generated_file_references,
    delete_generated_file_references,
)


logger = logging.getLogger(__name__)

BATCH_SIZE = 250
BATCH_PAUSE_SECONDS = 0.004
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running"}
RECOVERABLE_STAGES = {"stale_after_restart", "failed", "queued", "running"}

SESSION_DELETE_TABLES = [
    "scene_image_prompts",
    "scene_image_settings",
    "generated_images",
    "session_quality_notes",
    "scene_quality_checklists",
    "story_state_runs",
    "character_live_state",
    "character_state_events",
    "relationship_state_events",
    "relationship_state",
    "emotional_memories",
    "world_live_state",
    "scene_live_state",
    "object_state",
    "plot_threads",
    "state_snapshots",
    "story_memories",
    "session_summaries",
    "session_image_settings",
    "world_notes",
    "session_characters",
    "scene_versions",
    "scenes",
]


class SessionDeleteNotFoundError(Exception):
    pass


class SessionDeleteAlreadyRunningError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _table_exists(db, table_name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _count_table_rows(db, table_name: str, session_id: str) -> int:
    if not _table_exists(db, table_name):
        return 0
    row = db.execute(
        f"SELECT COUNT(*) AS count FROM {table_name} WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return int(row["count"] if row else 0)


def count_story_rows(db, session_id: str) -> dict[str, int]:
    session_row = db.execute("SELECT COUNT(*) AS count FROM sessions WHERE id = ?", (session_id,)).fetchone()
    counts = {"sessions": int(session_row["count"] if session_row else 0)}
    for table_name in SESSION_DELETE_TABLES:
        counts[table_name] = _count_table_rows(db, table_name, session_id)
    counts["generated_audio"] = 0
    return counts


@dataclass
class StoryDeleteJob:
    job_id: str
    session_id: str
    title: str
    permanent: bool
    status: str = "queued"
    stage: str = "queued"
    message: str = "Deletion queued."
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str = field(default_factory=utc_now)
    error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    deleted_counts: dict[str, int] = field(default_factory=dict)
    referenced_files: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)
    deleted_file_bytes: int = 0
    current_table: str | None = None
    tables_completed: int = 0
    total_tables: int = field(default=len(SESSION_DELETE_TABLES))
    rows_deleted: int = 0
    referenced_file_count: int = 0
    audit_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        duration_seconds = None
        if self.finished_at and self.started_at:
            try:
                start = datetime.fromisoformat(self.started_at)
                finish = datetime.fromisoformat(self.finished_at)
                duration_seconds = max(0.0, (finish - start).total_seconds())
            except ValueError:
                duration_seconds = None
        return {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "title": self.title,
            "permanent": self.permanent,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "created_at": self.created_at,
            "started_at": self.started_at or self.created_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
            "duration_seconds": duration_seconds,
            "error": self.error,
            "recoverable": self.status == "failed" and self.stage in RECOVERABLE_STAGES,
            "counts": dict(self.counts),
            "deleted_counts": dict(self.deleted_counts),
            "files_deleted": list(self.files_deleted),
            "files_skipped": list(self.files_skipped),
            "deleted_file_bytes": self.deleted_file_bytes,
            "current_table": self.current_table,
            "tables_completed": self.tables_completed,
            "total_tables": self.total_tables,
            "rows_deleted": self.rows_deleted,
            "referenced_file_count": self.referenced_file_count,
            "audit_path": self.audit_path,
        }


_jobs_lock = threading.RLock()
_jobs: dict[str, StoryDeleteJob] = {}
_jobs_by_session: dict[str, str] = {}


def _job_from_row(row) -> StoryDeleteJob:
    return StoryDeleteJob(
        job_id=row["job_id"],
        session_id=row["session_id"],
        title=row["title"],
        permanent=bool(row["permanent"]),
        status=row["status"],
        stage=row["stage"],
        message=row["message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
        error=row["error"],
        counts=_json_loads(row["counts_json"], {}),
        deleted_counts=_json_loads(row["deleted_counts_json"], {}),
        referenced_files=_json_loads(row["referenced_files_json"], []),
        files_deleted=_json_loads(row["files_deleted_json"], []),
        files_skipped=_json_loads(row["files_skipped_json"], []),
        deleted_file_bytes=int(row["deleted_file_bytes"] or 0),
        current_table=row["current_table"],
        tables_completed=int(row["tables_completed"] or 0),
        total_tables=int(row["total_tables"] or len(SESSION_DELETE_TABLES)),
        rows_deleted=int(row["rows_deleted"] or 0),
        referenced_file_count=int(row["referenced_file_count"] or 0),
        audit_path=row["audit_path"] or "",
    )


def _upsert_job(db, job: StoryDeleteJob) -> None:
    db.execute(
        """
        INSERT INTO story_delete_jobs (
            job_id, session_id, title, permanent, status, stage, message,
            created_at, started_at, finished_at, updated_at, error,
            counts_json, deleted_counts_json, referenced_files_json,
            files_deleted_json, files_skipped_json, deleted_file_bytes,
            current_table, tables_completed, total_tables, rows_deleted,
            referenced_file_count, audit_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            session_id = excluded.session_id,
            title = excluded.title,
            permanent = excluded.permanent,
            status = excluded.status,
            stage = excluded.stage,
            message = excluded.message,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            updated_at = excluded.updated_at,
            error = excluded.error,
            counts_json = excluded.counts_json,
            deleted_counts_json = excluded.deleted_counts_json,
            referenced_files_json = excluded.referenced_files_json,
            files_deleted_json = excluded.files_deleted_json,
            files_skipped_json = excluded.files_skipped_json,
            deleted_file_bytes = excluded.deleted_file_bytes,
            current_table = excluded.current_table,
            tables_completed = excluded.tables_completed,
            total_tables = excluded.total_tables,
            rows_deleted = excluded.rows_deleted,
            referenced_file_count = excluded.referenced_file_count,
            audit_path = excluded.audit_path
        """,
        (
            job.job_id,
            job.session_id,
            job.title,
            1 if job.permanent else 0,
            job.status,
            job.stage,
            job.message,
            job.created_at,
            job.started_at,
            job.finished_at,
            job.updated_at,
            job.error,
            _json_dumps(job.counts),
            _json_dumps(job.deleted_counts),
            _json_dumps(job.referenced_files),
            _json_dumps(job.files_deleted),
            _json_dumps(job.files_skipped),
            int(job.deleted_file_bytes or 0),
            job.current_table,
            int(job.tables_completed or 0),
            int(job.total_tables or 0),
            int(job.rows_deleted or 0),
            int(job.referenced_file_count or 0),
            job.audit_path or "",
        ),
    )


def _persist_job(job: StoryDeleteJob) -> None:
    with closing(get_connection()) as db, db:
        _upsert_job(db, job)


def _load_job(job_id: str) -> StoryDeleteJob | None:
    with closing(get_connection()) as db, db:
        row = db.execute("SELECT * FROM story_delete_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _job_from_row(row) if row else None


def _load_latest_job_for_session(session_id: str, statuses: set[str] | None = None) -> StoryDeleteJob | None:
    params: list[Any] = [session_id]
    status_clause = ""
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        status_clause = f" AND status IN ({placeholders})"
        params.extend(sorted(statuses))
    with closing(get_connection()) as db, db:
        row = db.execute(
            f"""
            SELECT *
            FROM story_delete_jobs
            WHERE session_id = ?{status_clause}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    return _job_from_row(row) if row else None


def _cache_job(job: StoryDeleteJob) -> StoryDeleteJob:
    with _jobs_lock:
        _jobs[job.job_id] = job
        _jobs_by_session[job.session_id] = job.job_id
    return job


def _uncache_job_if_terminal(job: StoryDeleteJob) -> None:
    if job.status not in TERMINAL_STATUSES:
        return
    with _jobs_lock:
        _jobs_by_session.pop(job.session_id, None)


def get_delete_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            return job.to_dict()
    job = _load_job(job_id)
    return job.to_dict() if job else None


def list_delete_jobs(*, limit: int = 20, include_completed: bool = True) -> list[dict[str, Any]]:
    clause = "" if include_completed else "WHERE status != 'completed'"
    with closing(get_connection()) as db, db:
        rows = db.execute(
            f"""
            SELECT *
            FROM story_delete_jobs
            {clause}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 20), 100)),),
        ).fetchall()
    return [_job_from_row(row).to_dict() for row in rows]


def _update_job(job_id: str, **patch: Any) -> StoryDeleteJob:
    with _jobs_lock:
        job = _jobs[job_id]
        for key, value in patch.items():
            setattr(job, key, value)
        job.updated_at = utc_now()
        snapshot = job
    _persist_job(snapshot)
    return snapshot


def _mutate_job(job_id: str, mutator: Callable[[StoryDeleteJob], None]) -> StoryDeleteJob:
    with _jobs_lock:
        job = _jobs[job_id]
        mutator(job)
        job.updated_at = utc_now()
        snapshot = job
    _persist_job(snapshot)
    return snapshot


def _launch_job_thread(job: StoryDeleteJob) -> None:
    _cache_job(job)
    thread = threading.Thread(target=_run_delete_job, args=(job.job_id,), name=f"story-delete-{job.job_id[:8]}", daemon=True)
    thread.start()


def _is_job_active_in_memory(job_id: str | None) -> bool:
    if not job_id:
        return False
    with _jobs_lock:
        job = _jobs.get(job_id)
        return bool(job and job.status in ACTIVE_STATUSES)


def start_session_delete_job(session_id: str, permanent: bool = True) -> dict[str, Any]:
    with _jobs_lock:
        existing_job_id = _jobs_by_session.get(session_id)
        existing_job = _jobs.get(existing_job_id or "")
        if existing_job and existing_job.status in ACTIVE_STATUSES:
            return existing_job.to_dict()

    persisted_active = _load_latest_job_for_session(session_id, ACTIVE_STATUSES)
    if persisted_active:
        if not _is_job_active_in_memory(persisted_active.job_id):
            _launch_job_thread(persisted_active)
        return persisted_active.to_dict()

    job_id = str(uuid4())
    created_at = utc_now()
    with closing(get_connection()) as db, db:
        existing = db.execute(
            """
            SELECT id, title, deletion_status, deletion_job_id
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if existing is None:
            raise SessionDeleteNotFoundError(session_id)
        if existing["deletion_status"] == "deleting" and existing["deletion_job_id"]:
            stale_job = _load_job(existing["deletion_job_id"])
            if stale_job and stale_job.status in ACTIVE_STATUSES:
                if not _is_job_active_in_memory(stale_job.job_id):
                    _launch_job_thread(stale_job)
                return stale_job.to_dict()
        job = StoryDeleteJob(
            job_id=job_id,
            session_id=session_id,
            title=existing["title"],
            permanent=permanent,
            created_at=created_at,
            started_at=created_at,
            updated_at=created_at,
        )
        _upsert_job(db, job)
        db.execute(
            """
            UPDATE sessions
            SET deletion_status = 'deleting',
                deletion_job_id = ?,
                deletion_started_at = ?,
                deletion_finished_at = NULL,
                deletion_error = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (job_id, created_at, session_id),
        )

    _launch_job_thread(job)
    return job.to_dict()


def resume_delete_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        memory_job = _jobs.get(job_id)
        if memory_job and memory_job.status in ACTIVE_STATUSES:
            return memory_job.to_dict()
    job = _load_job(job_id)
    if job is None:
        raise SessionDeleteNotFoundError(job_id)
    if job.status == "completed":
        return job.to_dict()

    now = utc_now()
    job.status = "queued"
    job.stage = "resume_queued"
    job.message = "Deletion resume queued."
    job.error = None
    job.finished_at = None
    job.started_at = job.started_at or now
    job.updated_at = now
    with closing(get_connection()) as db, db:
        _upsert_job(db, job)
        db.execute(
            """
            UPDATE sessions
            SET deletion_status = 'deleting',
                deletion_job_id = ?,
                deletion_error = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (job.job_id, job.session_id),
        )

    _launch_job_thread(job)
    return job.to_dict()


def _delete_table_batches(job_id: str, table_name: str, session_id: str) -> int:
    deleted_total = 0
    while True:
        with closing(get_connection()) as db, db:
            if not _table_exists(db, table_name):
                return deleted_total
            cursor = db.execute(
                f"""
                DELETE FROM {table_name}
                WHERE rowid IN (
                    SELECT rowid
                    FROM {table_name}
                    WHERE session_id = ?
                    LIMIT ?
                )
                """,
                (session_id, BATCH_SIZE),
            )
            deleted = int(cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0)
        if deleted <= 0:
            return deleted_total

        deleted_total += deleted

        def update_counts(job: StoryDeleteJob) -> None:
            job.rows_deleted += deleted
            job.deleted_counts[table_name] = job.deleted_counts.get(table_name, 0) + deleted

        _mutate_job(job_id, update_counts)
        time.sleep(BATCH_PAUSE_SECONDS)


def _mark_failed(job: StoryDeleteJob, error: str, *, stage: str = "failed") -> None:
    finished_at = utc_now()
    with closing(get_connection()) as db, db:
        db.execute(
            """
            UPDATE sessions
            SET deletion_status = 'delete_failed',
                deletion_finished_at = ?,
                deletion_error = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (finished_at, error[:2000], job.session_id),
        )
    _update_job(
        job.job_id,
        status="failed",
        stage=stage,
        message="Deletion failed. Retry is available from maintenance status.",
        finished_at=finished_at,
        error=error,
    )
    _uncache_job_if_terminal(job)


def _merge_references(existing: list[str], discovered: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for reference in [*existing, *discovered]:
        if not reference:
            continue
        key = str(reference)
        if key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def _run_delete_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        job = _load_job(job_id)
        if job is None:
            logger.error("Story delete job %s was missing from memory and storage", job_id)
            return
        _cache_job(job)

    try:
        _update_job(job_id, status="running", stage="counting", message="Counting story records.")
        with closing(get_connection()) as db, db:
            counts = count_story_rows(db, job.session_id)
            discovered_files = collect_story_generated_file_references(db, job.session_id)
            orphan_character_candidates = [
                row["character_id"]
                for row in db.execute(
                    """
                    SELECT sc.character_id
                    FROM session_characters sc
                    JOIN characters c ON c.id = sc.character_id
                    WHERE sc.session_id = ? AND c.auto_created = 1
                    """,
                    (job.session_id,),
                ).fetchall()
            ]

        referenced_files = _merge_references(job.referenced_files, discovered_files)
        _update_job(
            job_id,
            counts=counts,
            referenced_files=referenced_files,
            referenced_file_count=len(referenced_files),
            stage="deleting_rows",
            message="Deleting story database records in safe batches.",
        )

        for index, table_name in enumerate(SESSION_DELETE_TABLES, start=1):
            _update_job(job_id, current_table=table_name, message=f"Deleting {table_name}.")
            _delete_table_batches(job_id, table_name, job.session_id)
            _update_job(job_id, tables_completed=index)

        _update_job(job_id, current_table="sessions", message="Removing story shell.")
        with closing(get_connection()) as db, db:
            cursor = db.execute("DELETE FROM sessions WHERE id = ?", (job.session_id,))
            deleted = int(cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0)
            deleted_orphan_characters = 0
            if orphan_character_candidates:
                placeholders = ",".join("?" for _ in orphan_character_candidates)
                character_cursor = db.execute(
                    f"""
                    DELETE FROM characters
                    WHERE auto_created = 1
                      AND id IN ({placeholders})
                      AND NOT EXISTS (
                          SELECT 1 FROM session_characters sc WHERE sc.character_id = characters.id
                      )
                    """,
                    orphan_character_candidates,
                )
                deleted_orphan_characters = int(
                    character_cursor.rowcount
                    if character_cursor.rowcount and character_cursor.rowcount > 0
                    else 0
                )

        def update_session_delete(job: StoryDeleteJob) -> None:
            job.deleted_counts["sessions"] = job.deleted_counts.get("sessions", 0) + deleted
            job.deleted_counts["orphan_auto_characters"] = (
                job.deleted_counts.get("orphan_auto_characters", 0) + deleted_orphan_characters
            )
            job.rows_deleted += deleted + deleted_orphan_characters

        _mutate_job(job_id, update_session_delete)

        _update_job(job_id, stage="deleting_files", current_table=None, message="Deleting story-owned generated files.")
        current_job = _jobs[job_id]
        file_result = delete_generated_file_references(current_job.referenced_files)

        def update_files(job: StoryDeleteJob) -> None:
            job.files_deleted = file_result["files_deleted"]
            job.files_skipped = file_result["files_skipped"] or (
                ["No story-owned generated image/audio files were found for deletion."]
                if not job.referenced_files
                else []
            )
            job.deleted_file_bytes = int(file_result["deleted_bytes"])
            job.deleted_counts["generated_image_files_deleted"] = int(file_result["deleted_counts"].get("image", 0))
            job.deleted_counts["generated_audio_files_deleted"] = int(file_result["deleted_counts"].get("audio", 0))
            job.counts["generated_image_files_deleted"] = job.deleted_counts["generated_image_files_deleted"]
            job.counts["generated_audio_files_deleted"] = job.deleted_counts["generated_audio_files_deleted"]

        job = _mutate_job(job_id, update_files)

        finished_at = utc_now()
        _update_job(
            job_id,
            status="completed",
            stage="completed",
            message="Story deletion completed.",
            finished_at=finished_at,
            current_table=None,
        )
        audit_path = append_deleted_story_audit(get_delete_job(job_id) or job.to_dict())
        _update_job(job_id, audit_path=str(audit_path))
        _uncache_job_if_terminal(_jobs[job_id])
    except Exception as exc:  # noqa: BLE001 - background worker must never crash the API process
        logger.exception("Story delete job failed for %s", job.session_id)
        try:
            _mark_failed(_jobs.get(job_id) or job, str(exc))
        except Exception:
            logger.exception("Could not mark story delete job as failed")


def recover_stale_delete_jobs_on_startup() -> dict[str, int]:
    now = utc_now()
    stale_jobs: list[StoryDeleteJob] = []
    with closing(get_connection()) as db, db:
        rows = db.execute(
            """
            SELECT *
            FROM story_delete_jobs
            WHERE status IN ('queued', 'running')
            """
        ).fetchall()
        stale_jobs = [_job_from_row(row) for row in rows]
        for job in stale_jobs:
            job.status = "failed"
            job.stage = "stale_after_restart"
            job.message = "Backend restarted during deletion. Retry/resume this job to continue safely."
            job.error = "Backend restarted while the delete job was queued or running."
            job.finished_at = now
            job.updated_at = now
            _upsert_job(db, job)
            db.execute(
                """
                UPDATE sessions
                SET deletion_status = 'delete_failed',
                    deletion_finished_at = ?,
                    deletion_error = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND (deletion_status = 'deleting' OR deletion_job_id = ?)
                """,
                (now, job.error, job.session_id, job.job_id),
            )

        session_rows = db.execute(
            """
            SELECT id, title, deletion_job_id
            FROM sessions
            WHERE deletion_status = 'deleting'
            """
        ).fetchall()
        repaired_sessions = 0
        for row in session_rows:
            job_id = row["deletion_job_id"] or str(uuid4())
            existing = db.execute("SELECT 1 FROM story_delete_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing is None:
                job = StoryDeleteJob(
                    job_id=job_id,
                    session_id=row["id"],
                    title=row["title"],
                    permanent=True,
                    status="failed",
                    stage="stale_after_restart",
                    message="Story was marked deleting without a persisted job. Retry/resume to continue safely.",
                    created_at=now,
                    started_at=now,
                    finished_at=now,
                    updated_at=now,
                    error="Backend found a deleting story without a persisted delete job.",
                )
                _upsert_job(db, job)
                repaired_sessions += 1
            db.execute(
                """
                UPDATE sessions
                SET deletion_status = 'delete_failed',
                    deletion_job_id = ?,
                    deletion_finished_at = ?,
                    deletion_error = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (
                    job_id,
                    now,
                    "Backend restarted while story deletion was in progress. Retry/resume from maintenance status.",
                    row["id"],
                ),
            )

    if stale_jobs:
        logger.warning("Marked %s stale StoryDriver delete job(s) recoverable after startup", len(stale_jobs))
    return {"stale_jobs": len(stale_jobs), "repaired_sessions": repaired_sessions if "repaired_sessions" in locals() else 0}


def append_deleted_story_audit(record: dict[str, Any]) -> str:
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    audit_path = log_dir / "DELETED_STORIES_AUDIT.md"
    duration = record.get("duration_seconds")
    lines = [
        f"## {record.get('finished_at') or utc_now()} - permanent story deletion",
        "",
        f"- story_id: `{record.get('session_id') or record.get('id')}`",
        f"- job_id: `{record.get('job_id')}`",
        f"- permanent: `{record.get('permanent')}`",
        f"- status: `{record.get('status')}`",
        f"- started_at: `{record.get('started_at')}`",
        f"- finished_at: `{record.get('finished_at')}`",
        f"- duration_seconds: `{duration}`",
        f"- rows_deleted: `{record.get('rows_deleted', 0)}`",
        "- rows_counted_before_delete:",
    ]
    counts = record.get("counts") or {}
    lines.extend(f"  - {key}: {value}" for key, value in sorted(counts.items()))
    deleted_counts = record.get("deleted_counts") or {}
    if deleted_counts:
        lines.append("- deleted_counts:")
        lines.extend(f"  - {key}: {value}" for key, value in sorted(deleted_counts.items()))
    lines.append(f"- deleted_file_bytes: `{record.get('deleted_file_bytes', 0)}`")
    files_deleted = record.get("files_deleted") or []
    if files_deleted:
        lines.append("- files_deleted:")
        lines.extend(f"  - `{path}`" for path in files_deleted)
    else:
        lines.append("- files_deleted: none")
    files_skipped = record.get("files_skipped") or []
    if files_skipped:
        lines.append("- files_skipped:")
        lines.extend(f"  - {path}" for path in files_skipped)
    if record.get("error"):
        lines.append(f"- error: {record['error']}")
    lines.append("")
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return str(audit_path)
