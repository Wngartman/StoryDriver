from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "backend" / "data" / "logs" / "UX_CONTROL_FIX_REPORT.md"
BASE_URL = "http://localhost:8001"


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 30.0):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def generate_stream(session_id: str) -> tuple[dict, dict]:
    note = (
        "Beat: Write a concise grounded medieval scene, around 300 words. "
        "Two sisters repair a wagon wheel in the rain and argue quietly about whether to help a captured child. "
        "No magic, no headings, prose only."
    )
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps({"director_note": note, "mode": "continue"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    status_events: list[dict] = []
    delta_count = 0
    streamed_chars = 0
    final_scene = None
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=240) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "status":
                status_events.append(event)
            elif event.get("type") == "delta":
                delta_count += 1
                streamed_chars += len(event.get("text") or "")
            elif event.get("type") == "scene":
                final_scene = event.get("scene")
            elif event.get("type") == "error":
                raise RuntimeError(event.get("detail") or "Generation failed.")
    if not final_scene:
        raise RuntimeError("Stream ended without a saved scene event.")
    return final_scene, {
        "delta_count": delta_count,
        "streamed_chars": streamed_chars,
        "wall_seconds": round(time.monotonic() - started, 3),
        "stages": [event.get("stage") for event in status_events if event.get("stage")],
    }


def main() -> int:
    health = request_json("/health", timeout=10)
    if not health or not health.get("ok"):
        raise RuntimeError("StoryDriver backend health is not OK.")

    session = request_json(
        "/sessions",
        method="POST",
        payload={"title": "StoryDriver UX Control Smoke Test"},
        timeout=10,
    )
    scene, stream = generate_stream(session["id"])
    versions = scene.get("versions") or []
    version = versions[-1] if versions else {}
    stats = version.get("generation_stats") or scene.get("generation_stats") or {}

    checks = {
        "streaming_deltas_arrived": stream["delta_count"] > 3 and stream["streamed_chars"] > 100,
        "status_stages_reported": {"preparing_context", "planning_scene", "writing_scene", "checking_continuity"}.issubset(set(stream["stages"])),
        "generation_stats_saved": bool(stats.get("elapsed_seconds") and stats.get("tokens_per_second")),
        "stats_include_model": bool(stats.get("model")),
        "stats_include_task_profile": stats.get("task_profile") == "prose_generation",
    }

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# UX Control Fix Report",
        "",
        "## Streaming / Generation Stats Smoke",
        f"- Session: `{session['id']}`",
        f"- Scene: `{scene.get('id')}`",
        f"- Stream deltas: {stream['delta_count']} chunks / {stream['streamed_chars']} chars",
        f"- Stream stages: {', '.join(stream['stages']) or 'none'}",
        f"- Wall time: {stream['wall_seconds']}s",
        f"- Stats: {json.dumps(stats, ensure_ascii=False)}",
        "",
        "## Checks",
        *[f"- {'PASS' if ok else 'FAIL'} {name}" for name, ok in checks.items()],
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name, ok in checks.items():
        print(f"[{'OK' if ok else 'FAIL'}] {name}")
    print(f"Report written: {REPORT}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
