from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any


BASE_URL = "http://localhost:8001"


def api_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 300.0):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-extract StoryDriver story state for a session scene.")
    parser.add_argument("session_id")
    parser.add_argument("--scene-id", default="", help="Scene id. Defaults to latest scene in the session.")
    parser.add_argument("--version-id", default="", help="Version id. Defaults to active/latest version.")
    args = parser.parse_args()

    scenes = api_json(f"/sessions/{args.session_id}/scenes", timeout=30)
    if not isinstance(scenes, list) or not scenes:
        raise RuntimeError("No scenes found for session.")
    scene = next((item for item in scenes if item.get("id") == args.scene_id), None) if args.scene_id else scenes[-1]
    if not scene:
        raise RuntimeError(f"Scene not found: {args.scene_id}")
    version_id = args.version_id or scene.get("active_version_id") or (scene.get("versions") or [{}])[-1].get("id") or ""
    suffix = f"?version_id={version_id}" if version_id else ""
    run = api_json(
        f"/sessions/{args.session_id}/scenes/{scene['id']}/story-state/extract{suffix}",
        method="POST",
        timeout=420,
    )
    overview = api_json(f"/sessions/{args.session_id}/story-state", timeout=30)
    latest = (overview or {}).get("latest_run") or {}
    print(
        json.dumps(
            {
                "scene_id": scene["id"],
                "version_id": version_id,
                "run": {
                    "id": run.get("id"),
                    "status": run.get("status"),
                    "error": run.get("error"),
                    "warnings": run.get("warnings"),
                },
                "latest": {
                    "id": latest.get("id"),
                    "status": latest.get("status"),
                    "error": latest.get("error"),
                    "warnings": latest.get("warnings"),
                },
            },
            indent=2,
        )
    )
    return 0 if (run or {}).get("status") in {"completed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
