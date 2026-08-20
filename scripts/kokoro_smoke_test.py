from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.parse
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_status(url: str, timeout: int = 5) -> tuple[bool, int | None, str | None]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read(256)
            return True, response.status, None
    except urllib.error.HTTPError as error:
        return 400 <= error.code < 500, error.code, None
    except Exception as error:
        return False, None, str(error)


def speech_url(base_url: str, endpoint: str) -> str:
    if endpoint.lower().startswith(("http://", "https://")):
        return endpoint
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def port_listener(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return f"[PORT] Listener detected on {host}:{port}"
    except Exception:
        return f"[PORT] No listener detected on {host}:{port}"


def main() -> int:
    load_env(ROOT / ".env")
    load_env(ROOT / "backend" / ".env")
    load_env(ROOT / "scripts" / "storydriver.local.env")

    base_url = os.environ.get("KOKORO_BASE_URL", "http://localhost:8880").rstrip("/")
    endpoint = os.environ.get("KOKORO_SPEECH_ENDPOINT", "/v1/audio/speech")
    voice = os.environ.get("KOKORO_TEST_VOICE", os.environ.get("KOKORO_VOICE", "af_heart"))
    output_dir = ROOT / "backend" / "data" / "generated_audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "kokoro_smoke_test_latest.mp3"

    print("Testing Kokoro-FastAPI")
    print(f"  Base URL:        {base_url}")
    print(f"  Speech endpoint: {endpoint}")
    print(f"  Test voice:      {voice}")
    print(port_listener(base_url))
    print()

    reachable = False
    for path in ("", "/docs", "/health"):
        url = f"{base_url}{path}"
        ok, status, error = get_status(url)
        if ok:
            reachable = True
            print(f"[OK]   GET {url} -> HTTP {status}")
        else:
            print(f"[OFF]  GET {url} -> {error or f'HTTP {status}'}")

    target = speech_url(base_url, endpoint)
    ok, status, error = get_status(target)
    if ok:
        print(f"[HTTP] GET {target} -> HTTP {status} (POST endpoint probe)")
    else:
        print(f"[OFF]  GET {target} -> {error or f'HTTP {status}'}")

    if not reachable:
        print()
        print("Kokoro is not reachable. Start Kokoro-FastAPI or configure KOKORO_START_COMMAND.")
        return 0

    payload = {
        "model": "kokoro",
        "input": "StoryDriver Kokoro test.",
        "voice": voice,
        "response_format": "mp3",
        "speed": 1.0,
    }
    request = urllib.request.Request(
        target,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print()
    print(f"Testing POST {target}")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            output_file.write_bytes(response.read())
        print(f"[OK]   Test audio saved: {output_file} ({output_file.stat().st_size} bytes)")
        return 0
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else ""
        print(f"[FAIL] POST {target} -> HTTP {error.code}")
        if error.code == 404:
            print("Kokoro is reachable, but the configured speech endpoint was not found.")
        elif error.code in {400, 401, 403, 409, 415, 422}:
            print("Kokoro rejected the TTS request. Check voice, speed, or payload support.")
        elif detail:
            print(detail[:500])
        return 1
    except Exception as error:
        print(f"[FAIL] POST {target} -> {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
