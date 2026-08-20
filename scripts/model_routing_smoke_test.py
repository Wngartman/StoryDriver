from __future__ import annotations

import json
import os
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request_json(path: str, method: str = "GET", payload: dict | None = None, timeout: int = 20) -> object:
    base_url = os.environ.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001").rstrip("/")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def update_payload(profile: dict) -> dict:
    allowed = {
        "lm_studio_url",
        "model",
        "temperature",
        "top_p",
        "max_tokens",
        "seed",
        "top_k",
        "min_p",
        "repeat_penalty",
        "presence_penalty",
        "frequency_penalty",
        "timeout_seconds",
        "streaming",
        "notes",
    }
    return {key: profile.get(key) for key in allowed}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    load_env(ROOT / ".env")
    load_env(ROOT / "backend" / ".env")
    print("StoryDriver model routing smoke test")

    health = request_json("/health", timeout=8)
    assert_true(isinstance(health, dict) and health.get("ok"), "Backend health is not OK.")

    model_settings = request_json("/settings/model", timeout=10)
    assert_true(
        "writing_length_mode" in model_settings,
        "Model settings did not expose writing_length_mode.",
    )
    routing = request_json("/settings/task-model-profiles", timeout=10)
    assert_true(isinstance(routing, dict), "Task routing response was not JSON object.")

    task_ids = {item.get("id") for item in routing.get("task_types", [])}
    expected = {
        "story_foundation_generation",
        "scene_planning",
        "prose_generation",
        "rewrite_revision",
        "scene_quality_review",
        "story_state_extraction",
        "summary_generation",
        "title_generation",
        "utility",
    }
    assert_true(expected.issubset(task_ids), f"Missing task types: {sorted(expected - task_ids)}")

    profiles = routing.get("profiles", {})
    for task in expected:
        assert_true(
            bool(str(profiles.get(task, {}).get("notes") or "").strip()),
            f"Task notes default is empty for {task}.",
        )
    originals = {
        task: update_payload(profiles.get(task, {}))
        for task in ("story_state_extraction", "utility")
    }
    try:
        state_payload = {
            **originals["story_state_extraction"],
            "timeout_seconds": 66,
            "notes": "Smoke test state extraction routing round trip.",
        }
        routing = request_json(
            "/settings/task-model-profiles/story_state_extraction",
            method="PUT",
            payload=state_payload,
            timeout=10,
        )
        assert_true(
            routing["profiles"]["story_state_extraction"]["timeout_seconds"] == 66,
            "State extraction timeout did not persist.",
        )

        routing = request_json("/settings/task-model-profiles/utility", method="DELETE", timeout=10)
        utility_resolved = routing["resolved"]["utility"]
        assert_true(utility_resolved["uses_global_model"], "Reset utility task did not fall back to global model.")
        assert_true(
            utility_resolved["lm_studio_url"] == model_settings["lm_studio_url"],
            "Utility fallback did not use global LM Studio URL.",
        )

        diagnostics = request_json("/diagnostics", timeout=30)
        assert_true(
            "model_routing" in diagnostics and "story_state_extraction" in diagnostics["model_routing"],
            "Diagnostics did not include model routing.",
        )
    finally:
        for task, payload in originals.items():
            request_json(f"/settings/task-model-profiles/{task}", method="PUT", payload=payload, timeout=10)

    print("[OK] Task profile defaults load.")
    print("[OK] Writing length mode is available in model settings.")
    print("[OK] Default task notes are present.")
    print("[OK] Story State profile settings persist.")
    print("[OK] Utility reset falls back to global settings.")
    print("[OK] Diagnostics expose task routing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
