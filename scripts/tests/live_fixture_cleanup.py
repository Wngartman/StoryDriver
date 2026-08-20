from __future__ import annotations

import atexit
import json
import time
import urllib.request


def _request(base_url: str, path: str, method: str = "GET") -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}", method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def delete_test_session(base_url: str, session_id: str, timeout_seconds: int = 120) -> None:
    try:
        result = _request(base_url, f"/sessions/{session_id}?permanent=true", "DELETE")
        job_id = result.get("job_id") or result.get("id")
        if not job_id:
            return
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            job = _request(base_url, f"/sessions/delete-jobs/{job_id}")
            if job.get("status") == "completed":
                return
            if job.get("status") in {"failed", "cancelled"}:
                raise RuntimeError(f"fixture deletion {job.get('status')}: {job.get('error') or job.get('message')}")
            time.sleep(0.5)
        raise TimeoutError(f"fixture deletion timed out for {session_id}")
    except Exception as error:
        print(f"[WARN] Failed to clean disposable smoke session {session_id}: {error}")


def register_test_session_cleanup(base_url: str, session_id: str) -> None:
    atexit.register(delete_test_session, base_url, session_id)
