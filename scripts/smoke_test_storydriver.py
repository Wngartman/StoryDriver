from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def request_json(base_url: str, path: str, timeout: int = 20) -> tuple[bool, int | None, object | None, str | None]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            if not body:
                return True, response.status, None, None
            try:
                return True, response.status, json.loads(body.decode("utf-8")), None
            except json.JSONDecodeError:
                return True, response.status, body.decode("utf-8", errors="replace")[:300], None
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace") if error.fp else ""
        return False, error.code, None, body or str(error)
    except Exception as error:
        return False, None, None, str(error)


def main() -> int:
    load_env(ROOT / ".env")
    load_env(ROOT / "backend" / ".env")
    base_url = os.environ.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001")
    print("StoryDriver smoke test")
    print(f"Backend: {base_url}")
    print()

    failures: list[str] = []
    warnings: list[str] = []

    def probe(label: str, path: str, required: bool = True, timeout: int = 20) -> object | None:
        ok, status, data, error = request_json(base_url, path, timeout=timeout)
        if ok:
            print(f"[OK]   {label}: HTTP {status}")
            return data
        message = f"{label}: {error or f'HTTP {status}'}"
        if required:
            print(f"[FAIL] {message}")
            failures.append(message)
        else:
            print(f"[WARN] {message}")
            warnings.append(message)
        return None

    health = probe("Backend health", "/health", required=True, timeout=8)
    sessions = probe("Sessions list", "/sessions", required=True, timeout=12)
    diagnostics = probe("Diagnostics", "/diagnostics", required=True, timeout=30)
    probe("Model settings", "/settings/model", required=True, timeout=10)
    probe("Task model routing", "/settings/task-model-profiles", required=True, timeout=10)
    probe("TTS settings", "/settings/tts", required=True, timeout=10)
    probe("TTS status", "/tts/status", required=True, timeout=12)
    probe("Kokoro voices", "/tts/voices?provider=kokoro", required=True, timeout=12)
    probe("Story State settings", "/settings/story-state", required=True, timeout=10)
    probe("Characters", "/characters", required=True, timeout=12)

    session_items = sessions if isinstance(sessions, list) else sessions.get("sessions", []) if isinstance(sessions, dict) else []
    if session_items:
        session_id = session_items[0].get("id")
        if session_id:
            print()
            print(f"Session route checks: {session_id}")
            probe("Session scenes", f"/sessions/{session_id}/scenes", required=True, timeout=15)
            probe("Session characters", f"/sessions/{session_id}/characters", required=True, timeout=12)
            probe("World notes", f"/sessions/{session_id}/world", required=True, timeout=12)
            probe("Summary", f"/sessions/{session_id}/summary", required=True, timeout=12)
            probe("Memories", f"/sessions/{session_id}/memories", required=True, timeout=12)
            probe("Story State", f"/sessions/{session_id}/story-state", required=True, timeout=12)
    else:
        print("[WARN] No existing sessions found; session detail routes were skipped.")
        warnings.append("No existing sessions found; session detail routes were skipped.")

    if isinstance(diagnostics, dict):
        print()
        print("External service summary")
        lm = diagnostics.get("lm_studio", {})
        kokoro = diagnostics.get("kokoro", {})
        qwen = diagnostics.get("qwen", {})
        print(f"LM Studio OpenAI: {'OK' if lm.get('reachable') else 'offline'}")
        native = lm.get("native_chat") or {}
        print(f"LM Studio native route: {native.get('configured_backend') or 'not selected'}")
        print(f"Kokoro: {'OK' if kokoro.get('reachable') else 'offline'}")
        print(f"Qwen Premium worker: {qwen.get('state') or qwen.get('status') or 'stopped (on demand)'}")

    print()
    if failures:
        print(f"Smoke test failed with {len(failures)} core issue(s).")
        return 1
    if warnings:
        print(f"Smoke test passed with {len(warnings)} warning(s).")
    else:
        print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
