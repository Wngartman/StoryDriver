from __future__ import annotations

import json
import sqlite3
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "backend" / "data" / "app.db"
REPORT = ROOT / "backend" / "data" / "logs" / "product_polish" / "backend_query_profile.json"
BASE_URL = "http://127.0.0.1:8001"


QUERIES = {
    "story_list": """
        SELECT id, title, title_source, auto_title_status, created_at, updated_at
        FROM sessions
        WHERE archived_at IS NULL AND COALESCE(deletion_status, '') != 'deleting'
        ORDER BY updated_at DESC, created_at DESC
    """,
    "story_header": "SELECT id, title, updated_at FROM sessions WHERE id = ?",
    "active_characters": """
        SELECT c.id, c.name, sc.is_active
        FROM session_characters sc JOIN characters c ON c.id = sc.character_id
        WHERE sc.session_id = ? ORDER BY lower(c.name)
    """,
    "story_scenes": "SELECT id, mode, created_at, updated_at FROM scenes WHERE session_id = ? ORDER BY created_at",
    "world_note": "SELECT setting, tone, rules, locations FROM world_notes WHERE session_id = ?",
}


def profile_query(db: sqlite3.Connection, sql: str, params: tuple[str, ...], iterations: int = 400) -> dict:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        db.execute(sql, params).fetchall()
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    plan = [row[3] for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()]
    return {
        "mean_ms": round(statistics.mean(samples), 4),
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 4),
        "plan": plan,
    }


def profile_endpoint(path: str, iterations: int = 12) -> dict:
    samples = []
    response_bytes = 0
    for _ in range(iterations):
        started = time.perf_counter()
        with urllib.request.urlopen(BASE_URL + path, timeout=10) as response:
            body = response.read()
            if response.status != 200:
                raise AssertionError(f"{path} returned HTTP {response.status}")
        samples.append((time.perf_counter() - started) * 1000)
        response_bytes = len(body)
    ordered = sorted(samples)
    return {
        "mean_ms": round(statistics.mean(samples), 3),
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 3),
        "response_bytes": response_bytes,
    }


def main() -> int:
    with sqlite3.connect(DB) as db:
        db.execute("PRAGMA query_only = ON")
        db.execute("PRAGMA foreign_keys = ON")
        session = db.execute(
            "SELECT id FROM sessions WHERE archived_at IS NULL AND COALESCE(deletion_status, '') != 'deleting' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        session_id = session[0] if session else "missing-session"
        query_results = {
            name: profile_query(db, sql, () if name == "story_list" else (session_id,))
            for name, sql in QUERIES.items()
        }
        story_count = db.execute(
            "SELECT count(*) FROM sessions WHERE archived_at IS NULL AND COALESCE(deletion_status, '') != 'deleting'"
        ).fetchone()[0]
        unfinished = {
            "story_delete_jobs": db.execute("SELECT count(*) FROM story_delete_jobs WHERE status IN ('queued', 'running')").fetchone()[0],
            "generation_runs": db.execute("SELECT count(*) FROM generation_runs WHERE status IN ('queued', 'running')").fetchone()[0],
            "narration_jobs": db.execute("SELECT count(*) FROM narration_jobs WHERE status IN ('queued', 'running')").fetchone()[0],
        }
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = len(db.execute("PRAGMA foreign_key_check").fetchall())

    endpoint_results = {
        "health": profile_endpoint("/health"),
        "story_list": profile_endpoint("/sessions"),
        "model_settings": profile_endpoint("/settings/model"),
        "ui_settings": profile_endpoint("/settings/ui"),
    }
    if integrity != "ok" or foreign_keys:
        raise AssertionError(f"database validation failed: integrity={integrity}, foreign_keys={foreign_keys}")
    if any(result["p95_ms"] >= 25 for result in query_results.values()):
        raise AssertionError(f"SQLite query exceeded 25 ms p95: {query_results}")
    if endpoint_results["story_list"]["p95_ms"] >= 250:
        raise AssertionError(f"story list endpoint exceeded 250 ms p95: {endpoint_results['story_list']}")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": True,
        "story_count": story_count,
        "database_bytes": DB.stat().st_size,
        "queries": query_results,
        "endpoints": endpoint_results,
        "unfinished_jobs": unfinished,
        "integrity": integrity,
        "foreign_key_violations": foreign_keys,
        "decision": "No backend query/index change: measured queries are already far below the acceptance threshold.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("backend query profile contract: PASS")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
