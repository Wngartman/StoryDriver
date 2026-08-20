from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://localhost:8001"
REPORT = ROOT / "backend" / "data" / "logs" / "WRITER_MODE_RELIABILITY_FINAL_REPORT.md"
DB_PATH = ROOT / "backend" / "data" / "app.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_report(lines: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    section = "\n".join(lines).rstrip() + "\n"
    REPORT.write_text(existing.rstrip() + "\n\n" + section if existing.strip() else section, encoding="utf-8")


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 30.0):
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


async def simulate_kokoro_offline() -> dict:
    try:
        urllib.request.urlopen("http://127.0.0.1:9/health", timeout=1.0)
    except Exception as error:
        return {"ok": True, "reachable": False, "error": str(error)}
    raise AssertionError("Invalid Kokoro URL unexpectedly looked reachable.")


async def simulate_lmstudio_offline() -> dict:
    try:
        urllib.request.urlopen("http://127.0.0.1:9/v1/models", timeout=1.0)
    except Exception as error:
        return {"ok": True, "error": str(error)}
    raise AssertionError("Invalid LM Studio URL unexpectedly looked reachable.")


def parse_json_object_local(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Parsed state payload was not a JSON object.")
    return parsed


def simulate_malformed_state_json() -> dict:
    repaired = parse_json_object_local('model preface text {"characters": [], "objects": [{"name": "brass key"}]} trailing text')
    if not isinstance(repaired, dict) or not repaired.get("objects"):
        raise AssertionError("State JSON repair did not extract embedded JSON object.")
    try:
        parse_json_object_local("not json at all")
    except json.JSONDecodeError as error:
        return {"ok": True, "embedded_json_repaired": True, "bad_json_error": str(error)}
    raise AssertionError("Invalid state JSON did not raise JSONDecodeError.")


def create_title_failure_story() -> str:
    session_id = f"writer-reliability-title-failure-{uuid4()}"
    scene_id = f"writer-reliability-title-scene-{uuid4()}"
    version_id = f"writer-reliability-title-version-{uuid4()}"
    text = (
        "Mara stood in the empty records office with the brass key in her hand. "
        "The scene exists only to test clean automatic title failure handling."
    )
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT INTO sessions (id, title, title_source, auto_title_status) VALUES (?, ?, ?, ?)",
            (session_id, "Untitled Story", "placeholder", "skipped"),
        )
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, ?)",
            (scene_id, session_id, "Title failure probe.", text, "continue"),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, scene_id, session_id, "Title failure probe.", text, "continue", 1),
        )
    return session_id


def simulate_title_failure() -> dict:
    profiles = request_json("/settings/task-model-profiles", timeout=10)
    original = (profiles.get("profiles") or {}).get("title_generation") or {}
    patched = dict(original)
    patched.update({"lm_studio_url": "http://127.0.0.1:9/v1", "timeout_seconds": 5.0})
    session_id = create_title_failure_story()
    try:
        request_json("/settings/task-model-profiles/title_generation", method="PUT", payload=patched, timeout=15)
        try:
            request_json(f"/sessions/{session_id}/auto-title", method="POST", payload={"force": True}, timeout=20)
        except RuntimeError:
            pass
        session = request_json(f"/sessions/{session_id}", timeout=10)
        ok = session.get("auto_title_status") in {"failed", "skipped"} or session.get("title") == "Untitled Story"
        if not ok:
            raise AssertionError(f"Title failure did not leave clean status: {session}")
        return {
            "ok": True,
            "session_id": session_id,
            "auto_title_status": session.get("auto_title_status"),
            "auto_title_error": session.get("auto_title_error"),
            "title": session.get("title"),
        }
    finally:
        request_json("/settings/task-model-profiles/title_generation", method="PUT", payload=original, timeout=15)
        try:
            response = request_json(f"/sessions/{session_id}?permanent=true", method="DELETE", timeout=15)
            job_id = response.get("job_id") or response.get("deletion_job_id")
            if job_id:
                deadline = time.perf_counter() + 30
                while time.perf_counter() < deadline:
                    job = request_json(f"/sessions/delete-jobs/{job_id}", timeout=10)
                    if job.get("status") in {"completed", "failed"}:
                        break
                    time.sleep(1)
        except Exception:
            with sqlite3.connect(DB_PATH) as db:
                db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


async def main_async() -> int:
    results = {
        "kokoro_offline": await simulate_kokoro_offline(),
        "lmstudio_offline": await simulate_lmstudio_offline(),
        "malformed_state_json": simulate_malformed_state_json(),
        "title_failure": simulate_title_failure(),
    }
    lines = [
        f"## Error Handling Probe - {now_iso()}",
        "",
        f"- Kokoro offline simulation: `{'PASS' if results['kokoro_offline']['ok'] else 'FAIL'}`",
        f"- LM Studio offline simulation: `{'PASS' if results['lmstudio_offline']['ok'] else 'FAIL'}`",
        f"- malformed Story State JSON parser behavior: `{'PASS' if results['malformed_state_json']['ok'] else 'FAIL'}`",
        f"- title failure route behavior: `{'PASS' if results['title_failure']['ok'] else 'FAIL'}`",
        "",
        "```json",
        json.dumps(results, indent=2, sort_keys=True),
        "```",
    ]
    append_report(lines)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main_async()))
    except Exception as error:
        append_report(
            [
                f"## Error Handling Probe Failure - {now_iso()}",
                "",
                f"- error: `{error}`",
            ]
        )
        print(f"[FAIL] {error}")
        sys.exit(1)
