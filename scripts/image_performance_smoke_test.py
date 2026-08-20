from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from uuid import uuid4
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "IMAGE_PERFORMANCE_REPORT.md"
TUNING_REPORT_PATH = ROOT / "backend" / "data" / "logs" / "COMFYUI_PERFORMANCE_TUNING_REPORT.md"
SMOKE_TITLE = "StoryDriver Image Smoke Test"
SMOKE_SCENE = "A lone traveler stands before a ruined chapel at night, rain falling through pale moonlight."
DB_PATH = ROOT / "backend" / "data" / "app.db"


def api_json(base_url: str, method: str, path: str, payload: dict | None = None, timeout: int = 30):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc


def append_report(lines: list[str]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else "# StoryDriver Image Performance Report\n"
    block = "\n\n## Smoke Test Run\n\n" + "\n".join(lines) + "\n"
    report_text = existing.rstrip() + block
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    tuning_existing = (
        TUNING_REPORT_PATH.read_text(encoding="utf-8")
        if TUNING_REPORT_PATH.exists()
        else "# ComfyUI Performance Tuning Report\n"
    )
    TUNING_REPORT_PATH.write_text(tuning_existing.rstrip() + block, encoding="utf-8")


def find_ready_workflow(base_url: str, workflow_id: str | None = None) -> dict:
    workflows = api_json(base_url, "GET", "/image-workflows", timeout=20)
    ready = [item for item in workflows.get("workflows", []) if item.get("status") == "ready"]
    if workflow_id:
        selected = next((item for item in ready if item.get("id") == workflow_id), None)
        if selected:
            return selected
        raise RuntimeError(f"Requested workflow is not ready or was not found: {workflow_id}")
    selected_id = workflows.get("selected_workflow_id")
    if selected_id:
        selected = next((item for item in ready if item.get("id") == selected_id), None)
        if selected:
            return selected
    if ready:
        return ready[0]
    raise RuntimeError("No ready image workflow found. Open Image Settings, import API JSON, auto-detect nodes, save, and validate.")


def find_or_create_smoke_scene(base_url: str) -> tuple[str, str, str]:
    sessions = api_json(base_url, "GET", "/sessions", timeout=20)
    session = next((item for item in sessions if item.get("title") == SMOKE_TITLE), None)
    if not session:
        session = api_json(base_url, "POST", "/sessions", {"title": SMOKE_TITLE}, timeout=20)
    session_id = session["id"]
    scenes = api_json(base_url, "GET", f"/sessions/{session_id}/scenes", timeout=20)
    if not scenes:
        scene_id = str(uuid4())
        version_id = str(uuid4())
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with sqlite3.connect(DB_PATH) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(
                """
                INSERT INTO scenes (id, session_id, director_note, generated_text, mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'continue', ?, ?)
                """,
                (scene_id, session_id, "StoryDriver image performance smoke test.", SMOKE_SCENE, now, now),
            )
            db.execute(
                """
                INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index, created_at)
                VALUES (?, ?, ?, ?, ?, 'continue', 1, ?)
                """,
                (version_id, scene_id, session_id, "StoryDriver image performance smoke test.", SMOKE_SCENE, now),
            )
            db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        return session_id, scene_id, version_id
    else:
        scene = scenes[-1]
    version_id = scene.get("active_version_id") or (scene.get("versions") or [{}])[-1].get("id")
    if not version_id:
        raise RuntimeError("Smoke test scene has no scene version.")
    return session_id, scene["id"], version_id


def set_resource_mode(base_url: str, mode: str) -> dict:
    settings = api_json(base_url, "GET", "/settings/image", timeout=20)
    updated = dict(settings)
    updated["image_resource_mode"] = mode
    api_json(base_url, "PUT", "/settings/image", updated, timeout=20)
    return settings


def restore_settings(base_url: str, settings: dict | None) -> None:
    if not settings:
        return
    try:
        api_json(base_url, "PUT", "/settings/image", settings, timeout=20)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not restore image settings: {exc}")


def run_image(base_url: str, *, session_id: str, scene_id: str, version_id: str, workflow_id: str, mode: str, run_index: int) -> dict:
    previous_settings = None
    try:
        previous_settings = set_resource_mode(base_url, mode)
        start = time.perf_counter()
        image = api_json(
            base_url,
            "POST",
            "/images/generate",
            {
                "session_id": session_id,
                "scene_id": scene_id,
                "version_id": version_id,
                "workflow_id": workflow_id,
                "open_preview": False,
            },
            timeout=900,
        )
        duration = time.perf_counter() - start
        status = api_json(base_url, "GET", "/images/job-status", timeout=20)
        return {"ok": True, "mode": mode, "run_index": run_index, "duration": duration, "image": image, "status": status}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mode": mode, "error": str(exc)}
    finally:
        restore_settings(base_url, previous_settings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a StoryDriver image performance smoke test.")
    parser.add_argument("--base-url", default="http://localhost:8001", help="StoryDriver backend URL")
    parser.add_argument(
        "--mode",
        default="image_priority",
        choices=["image_priority", "balanced", "manual", "experimental_auto_swap"],
        help="Resource mode to test. Defaults to Auto Image Priority.",
    )
    parser.add_argument("--include-balanced", action="store_true", help="Also run an explicit Balanced test.")
    parser.add_argument("--repeat", type=int, default=1, help="Number of runs for the selected mode.")
    parser.add_argument("--compare-two", action="store_true", help="Run the selected mode twice for warm/cold comparison.")
    parser.add_argument("--auto-priority", action="store_true", help="Legacy alias; keeps the default Auto Image Priority mode.")
    parser.add_argument("--workflow-id", default="", help="Ready workflow id to test instead of the selected global workflow.")
    args = parser.parse_args()
    repeat = max(2 if args.compare_two else args.repeat, 1)

    report_lines: list[str] = [f"- Started: {time.strftime('%Y-%m-%d %H:%M:%S')}"]
    try:
        health = api_json(args.base_url, "GET", "/health", timeout=10)
        report_lines.append(f"- Backend health: {health}")
        workflow = find_ready_workflow(args.base_url, args.workflow_id.strip() or None)
        report_lines.append(f"- Workflow: {workflow.get('name')} (`{workflow.get('id')}`)")
        metadata = workflow.get("metadata") or {}
        report_lines.append(f"- Workflow type: {metadata.get('workflow_type_label') or metadata.get('workflow_type') or 'unknown'}")
        report_lines.append(f"- Diffusion models: {', '.join(metadata.get('diffusion_model_names') or metadata.get('model_names') or []) or 'unknown'}")
        report_lines.append(f"- Upscale models: {', '.join(metadata.get('upscale_model_names') or []) or 'none detected'}")
        report_lines.append(f"- Auxiliary models: {', '.join(metadata.get('auxiliary_model_names') or []) or 'none detected'}")
        report_lines.append(f"- Complexity: {metadata.get('workflow_complexity') or 'unknown'}")
        session_id, scene_id, version_id = find_or_create_smoke_scene(args.base_url)
        report_lines.append(f"- Session: {session_id}")
        report_lines.append(f"- Scene/version: {scene_id} / {version_id}")

        modes = ["balanced"] if args.include_balanced and args.mode != "balanced" else []
        modes.append(args.mode)
        exit_code = 0
        for mode in modes:
            runs_for_mode = repeat if mode == args.mode else 1
            for run_index in range(1, runs_for_mode + 1):
                result = run_image(
                    args.base_url,
                    session_id=session_id,
                    scene_id=scene_id,
                    version_id=version_id,
                    workflow_id=workflow["id"],
                    mode=mode,
                    run_index=run_index,
                )
                label_suffix = f" run {run_index}" if runs_for_mode > 1 else ""
                if result["ok"]:
                    image = result["image"]
                    actions = image.get("resource_actions") or {}
                    timings = actions.get("performance_timings") or {}
                    effective_mode = image.get("resource_mode") or actions.get("mode") or mode
                    requested = actions.get("requested_resource_mode") or mode
                    mode_label = effective_mode if effective_mode == requested else f"{requested} -> {effective_mode}"
                    report_lines.append(f"- {mode_label}{label_suffix}: OK, total {result['duration']:.1f}s, image `{image.get('id')}`")
                    report_lines.append(f"  - Backend measured total: {actions.get('performance_total_seconds', 'unknown')}s")
                    report_lines.append(f"  - ComfyUI total: {timings.get('comfyui_queue_to_image_retrieved', 'unknown')}s")
                    report_lines.append(f"  - Queue wait: {timings.get('comfyui_queue_wait', 'unknown')}s")
                    report_lines.append(f"  - Execution wait: {timings.get('comfyui_execution_wait', 'unknown')}s")
                    report_lines.append(f"  - LM unload policy: {actions.get('lm_unload_policy', 'unknown')}")
                    report_lines.append(f"  - LM models loaded before: {actions.get('loaded_model_count_before', 'unknown')}")
                    report_lines.append(f"  - LM models planned for unload: {actions.get('planned_unload_count', 'unknown')}")
                    report_lines.append(f"  - LM unload confirmed: {actions.get('unload_confirmed', False)}")
                    report_lines.append(f"  - LM models remaining before ComfyUI: {actions.get('loaded_model_count_after_unload', 'unknown')}")
                    report_lines.append(f"  - LM reload policy: {actions.get('lm_reload_policy', 'unknown')}")
                    reload_targets = ", ".join(actions.get("reload_targets") or []) or "none"
                    if actions.get("lm_reload_delayed_for_regenerate"):
                        reload_label = "deferred for fast regenerate window"
                    elif actions.get("reload_attempted"):
                        reload_label = f"{actions.get('reload_duration_seconds', 'not recorded')}s"
                    else:
                        reload_label = "not attempted"
                    if actions.get("comfyui_free_delayed_for_regenerate"):
                        free_label = "deferred for fast regenerate window"
                        alive_label = "kept warm"
                    elif actions.get("comfyui_free_attempted"):
                        free_label = str(actions.get("comfyui_free_succeeded", False))
                        alive_label = str(actions.get("comfyui_alive_after_free", False))
                    else:
                        free_label = "not attempted"
                        alive_label = "not checked"
                    report_lines.append(f"  - LM reload targets: {reload_targets}")
                    report_lines.append(f"  - LM reload: {reload_label}")
                    report_lines.append(f"  - ComfyUI free: {free_label}")
                    report_lines.append(f"  - ComfyUI status after image: {alive_label}")
                else:
                    report_lines.append(f"- {mode}{label_suffix}: FAILED - {result['error']}")
                    exit_code = 1
        append_report(report_lines)
        print("\n".join(report_lines))
        return exit_code
    except Exception as exc:  # noqa: BLE001
        report_lines.append(f"- Smoke setup failed: {exc}")
        append_report(report_lines)
        print("\n".join(report_lines))
        return 1


if __name__ == "__main__":
    sys.exit(main())
