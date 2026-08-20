from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "backend" / "data" / "logs" / "LATENCY_OPTIMIZATION_REPORT.md"
BASE_URL = "http://localhost:8001"
SMOKE_TITLE = "StoryDriver Latency Smoke Test"

FAST_NOTE = (
    "Write a grounded medieval scene in 700-1000 words. Mara and Elias wait in a rain-dark farm kitchen "
    "while a messenger explains that a bandit camp has taken a child. Keep it prose only, no magic, no assistant ending."
)

CHAPTER_NOTE = (
    "Write a long first chapter, 7-12 minutes of reading time, about three sisters on a poor farm outside a major town. "
    "Their parents died young, they survived as stealthy street outcasts, and no magic exists in this world. "
    "They hear a bandit camp has captured a little girl. Introduce each sister properly and show them debating whether "
    "to rescue the girl. They are smart and tactical but have never truly killed or fought before."
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def api_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
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
        raise RuntimeError(f"{method} {path} failed HTTP {error.code}: {detail}") from error


def get_or_create_session() -> dict[str, Any]:
    sessions = api_json("/sessions", timeout=20)
    if isinstance(sessions, list):
        for session in sessions:
            if session.get("title") == SMOKE_TITLE:
                return session
    return api_json("/sessions", method="POST", payload={"title": SMOKE_TITLE}, timeout=20)


def selected_version(scene: dict[str, Any]) -> dict[str, Any]:
    versions = scene.get("versions") or []
    version_id = scene.get("active_version_id")
    for version in versions:
        if version.get("id") == version_id:
            return version
    return versions[-1] if versions else {}


def scene_stats(scene: dict[str, Any]) -> dict[str, Any]:
    version = selected_version(scene)
    return version.get("generation_stats") or scene.get("generation_stats") or {}


def generate_stream(session_id: str, director_note: str, *, label: str, timeout: float) -> dict[str, Any]:
    submitted_at = datetime.now(timezone.utc).isoformat()
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps(
            {
                "director_note": director_note,
                "mode": "continue",
                "client_submitted_at": submitted_at,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    first_status_at: float | None = None
    first_delta_at: float | None = None
    final_scene_at: float | None = None
    final_scene: dict[str, Any] | None = None
    status_events: list[dict[str, Any]] = []
    warnings: list[str] = []
    delta_count = 0
    delta_chars = 0
    print(f"[latency] generating {label}...")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type")
            elapsed = time.perf_counter() - started
            if event_type == "status":
                if first_status_at is None:
                    first_status_at = elapsed
                status_events.append(event)
                print(f"[status:{label}] {event.get('stage') or ''} {event.get('message') or ''}")
            elif event_type == "delta":
                if first_delta_at is None:
                    first_delta_at = elapsed
                text = event.get("text") or ""
                delta_count += 1
                delta_chars += len(text)
                if delta_count % 25 == 0:
                    print(f"[stream:{label}] {delta_chars} chars")
            elif event_type == "warning":
                warnings.append(event.get("message") or "")
            elif event_type == "scene":
                final_scene_at = elapsed
                final_scene = event.get("scene") or {}
            elif event_type == "error":
                raise RuntimeError(event.get("detail") or "Generation stream returned an error.")
    if not final_scene:
        raise RuntimeError(f"No final scene returned for {label}.")
    stats = scene_stats(final_scene)
    return {
        "label": label,
        "scene": final_scene,
        "stats": stats,
        "status_events": status_events,
        "warnings": warnings,
        "delta_count": delta_count,
        "delta_chars": delta_chars,
        "submit_to_first_status_seconds": round(first_status_at, 3) if first_status_at is not None else None,
        "submit_to_first_delta_seconds": round(first_delta_at, 3) if first_delta_at is not None else None,
        "submit_to_scene_event_seconds": round(final_scene_at, 3) if final_scene_at is not None else None,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "submitted_at": submitted_at,
    }


def synthesize_latency(text: str) -> dict[str, Any]:
    settings = api_json("/settings/tts", timeout=15)
    if not isinstance(settings, dict) or settings.get("tts_provider") != "kokoro":
        return {"skipped": True, "reason": "TTS provider is not Kokoro."}
    sample = (text or "").strip()
    if len(sample) > 1600:
        sample = sample[:1600].rsplit(" ", 1)[0]
    if not sample:
        return {"skipped": True, "reason": "No generated text to synthesize."}
    payload = {
        "text": sample,
        "voice": settings.get("tts_voice"),
        "speed": settings.get("tts_speed") or 1,
        "provider": "kokoro",
        "temperature": settings.get("kokoro_temperature"),
        "top_p": settings.get("kokoro_top_p"),
        "exaggeration": settings.get("kokoro_exaggeration"),
        "style": settings.get("kokoro_style"),
        "cfg": settings.get("kokoro_cfg"),
    }
    started = time.perf_counter()
    try:
        response = api_json("/tts/synthesize", method="POST", payload=payload, timeout=240)
    except Exception as error:
        return {"ok": False, "error": str(error), "wall_seconds": round(time.perf_counter() - started, 3)}
    return {
        "ok": True,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "cached": response.get("cached"),
        "synthesis_seconds": response.get("synthesis_seconds"),
        "audio_url": response.get("audio_url"),
        "cache_key": response.get("cache_key"),
    }


def fetch_image_prompt(session_id: str, scene: dict[str, Any]) -> dict[str, Any] | None:
    scene_id = scene.get("id")
    version = selected_version(scene)
    version_id = version.get("id") or scene.get("active_version_id")
    if not scene_id:
        return None
    query = urllib.parse.urlencode({"version_id": version_id}) if version_id else ""
    path = f"/sessions/{session_id}/scenes/{scene_id}/image-prompt"
    if query:
        path = f"{path}?{query}"
    deadline = time.perf_counter() + 180
    latest = None
    while time.perf_counter() < deadline:
        try:
            latest = api_json(path, timeout=30)
            if isinstance(latest, dict) and (latest.get("prompt") or "").strip():
                return latest
        except Exception:
            pass
        time.sleep(4)
    return latest if isinstance(latest, dict) else None


def run_image_latency(session_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    workflows = api_json(f"/image-workflows?session_id={urllib.parse.quote(session_id)}", timeout=30)
    workflow_items = (workflows or {}).get("workflows") if isinstance(workflows, dict) else workflows
    ready_workflow = next((item for item in workflow_items or [] if item.get("status") == "ready"), None)
    if not ready_workflow:
        return {"skipped": True, "reason": "No ready image workflow."}
    version = selected_version(scene)
    payload = {
        "session_id": session_id,
        "scene_id": scene.get("id"),
        "version_id": version.get("id") or scene.get("active_version_id"),
        "workflow_id": ready_workflow.get("id"),
        "open_preview": False,
    }
    started = time.perf_counter()
    try:
        image = api_json("/images/generate", method="POST", payload=payload, timeout=1200)
    except Exception as error:
        return {"ok": False, "error": str(error), "wall_seconds": round(time.perf_counter() - started, 3)}
    actions = image.get("resource_actions") or {}
    return {
        "ok": True,
        "workflow": ready_workflow.get("filename") or ready_workflow.get("id"),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "performance_total_seconds": actions.get("performance_total_seconds"),
        "comfyui_generation_seconds": actions.get("comfyui_generation_seconds"),
        "lm_unload_seconds": actions.get("lm_unload_seconds"),
        "lm_reload_seconds": actions.get("lm_reload_seconds"),
        "comfyui_free_seconds": actions.get("comfyui_free_seconds"),
        "warnings": image.get("resource_warnings") or [],
        "image_id": image.get("id"),
    }


def format_value(value: Any) -> str:
    if value is None:
        return "not recorded"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def format_seconds(value: Any) -> str:
    if value is None:
        return "not recorded"
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return str(value)


def generation_summary(result: dict[str, Any]) -> list[str]:
    stats = result.get("stats") or {}
    latency = stats.get("latency") or {}
    prompt_diag = stats.get("prompt_diagnostics") or {}
    return [
        f"- Label: {result['label']}",
        f"- Submit to first visible delta: {format_seconds(result.get('submit_to_first_delta_seconds'))}",
        f"- Submit to scene event: {format_seconds(result.get('submit_to_scene_event_seconds'))}",
        f"- Backend request to first token: {format_seconds(latency.get('request_to_first_token_seconds'))}",
        f"- LM request to first token: {format_seconds(latency.get('lm_request_to_first_token_seconds') or stats.get('first_token_latency_seconds'))}",
        f"- Scene generation time: {format_seconds(latency.get('scene_generation_seconds') or stats.get('elapsed_seconds'))}",
        f"- Scene save time: {format_seconds(latency.get('scene_save_seconds'))}",
        f"- Estimated tokens/sec: {format_value(stats.get('tokens_per_second'))}",
        f"- Estimated tokens: {format_value(stats.get('tokens_generated'))}",
        f"- Words: {format_value(stats.get('word_count'))}",
        f"- Model: `{stats.get('model') or 'unknown'}`",
        f"- Task profile: `{stats.get('task_profile') or 'unknown'}`",
        f"- Prompt estimated tokens: {format_value(prompt_diag.get('prompt_estimated_tokens'))}",
        f"- Prompt total chars: {format_value(prompt_diag.get('total_prompt_chars'))}",
        f"- Prompt warnings: {', '.join(stats.get('prompt_size_warnings') or []) or 'none'}",
        f"- Stream chunks/chars: {result.get('delta_count')} chunks / {result.get('delta_chars')} chars",
        f"- Stream statuses: {', '.join(event.get('stage', '') for event in result.get('status_events') or []) or 'none'}",
        f"- Warnings: {', '.join(result.get('warnings') or []) or 'none'}",
    ]


def write_report(
    *,
    diagnostics: dict[str, Any],
    model_settings: dict[str, Any],
    routing: dict[str, Any],
    resource_status: dict[str, Any] | None,
    session: dict[str, Any],
    fast_result: dict[str, Any],
    chapter_result: dict[str, Any] | None,
    tts_result: dict[str, Any],
    image_prompt: dict[str, Any] | None,
    image_result: dict[str, Any] | None,
) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    loaded_models = (
        ((resource_status or {}).get("lm_studio") or {}).get("loaded_instances")
        or ((resource_status or {}).get("lm_studio") or {}).get("loaded_models")
        or ((diagnostics or {}).get("lm_studio") or {}).get("loaded_instances")
        or ((diagnostics or {}).get("lm_studio") or {}).get("loaded_models")
        or []
    )
    resolved = routing.get("resolved") if isinstance(routing, dict) else {}
    prose = (resolved or {}).get("prose_generation") or {}
    state = (resolved or {}).get("story_state_extraction") or {}
    summary = (resolved or {}).get("summary_generation") or {}
    lines = [
        "# StoryDriver Latency Optimization Report",
        "",
        f"Updated: {now_iso()}",
        "",
        "## Rollback Checkpoint",
        "- Local checkpoint commit: `0c41023`",
        "- Local checkpoint branch: `pre_latency_optimization_20260607`",
        "- App database backup: `D:\\StoryDriver\\backend\\data\\backups\\app_pre_latency_optimization_20260607.db`",
        "- Roll back code with `git reset --hard 0c41023` after saving any wanted work.",
        "",
        "## Model Routing",
        f"- Global model: `{model_settings.get('model') or 'auto-select loaded model'}`",
        f"- Prose model: `{prose.get('model') or 'global fallback'}`",
        f"- State extraction model: `{state.get('model') or 'global fallback'}`",
        f"- Summary model: `{summary.get('model') or 'global fallback'}`",
        f"- Loaded LM Studio instances: {len(loaded_models) if isinstance(loaded_models, list) else 'unknown'}",
        "",
        "## Fast Prose Timing",
        *generation_summary(fast_result),
        "",
        "## Chapter Timing",
    ]
    if chapter_result:
        lines.extend(generation_summary(chapter_result))
    else:
        lines.append("- Skipped. Run `scripts\\latency_smoke_test.bat --chapter` to include it.")
    lines.extend(["", "## Narration Warm-Up"])
    if tts_result.get("skipped"):
        lines.append(f"- Skipped: {tts_result.get('reason')}")
    elif tts_result.get("ok"):
        lines.extend(
            [
                f"- Kokoro request wall time: {format_seconds(tts_result.get('wall_seconds'))}",
                f"- Kokoro reported synth time: {format_seconds(tts_result.get('synthesis_seconds'))}",
                f"- Cache hit: {'yes' if tts_result.get('cached') else 'no'}",
                f"- Audio URL: `{tts_result.get('audio_url') or 'none'}`",
            ]
        )
    else:
        lines.append(f"- Failed: {tts_result.get('error')}")
    lines.extend(["", "## Image Prompt Cache"])
    if image_prompt:
        lines.extend(
            [
                f"- Prompt cached: {'yes' if (image_prompt.get('prompt') or '').strip() else 'no'}",
                f"- Visual beat: {str(image_prompt.get('visual_beat') or '')[:240] or 'none'}",
                f"- Confidence: {image_prompt.get('confidence')}",
            ]
        )
    else:
        lines.append("- Prompt cache was not ready before timeout.")
    lines.extend(["", "## Image Timing"])
    if not image_result:
        lines.append("- Skipped. Run `scripts\\latency_smoke_test.bat --auto-priority` to include a real image job.")
    elif image_result.get("skipped"):
        lines.append(f"- Skipped: {image_result.get('reason')}")
    elif image_result.get("ok"):
        lines.extend(
            [
                f"- Workflow: `{image_result.get('workflow')}`",
                f"- Wall time: {format_seconds(image_result.get('wall_seconds'))}",
                f"- Performance total: {format_seconds(image_result.get('performance_total_seconds'))}",
                f"- ComfyUI generation: {format_seconds(image_result.get('comfyui_generation_seconds'))}",
                f"- LM unload: {format_seconds(image_result.get('lm_unload_seconds'))}",
                f"- ComfyUI free: {format_seconds(image_result.get('comfyui_free_seconds'))}",
                f"- LM reload: {format_seconds(image_result.get('lm_reload_seconds'))}",
                f"- Warnings: {', '.join(image_result.get('warnings') or []) or 'none'}",
            ]
        )
    else:
        lines.append(f"- Failed: {image_result.get('error')}")
    lines.extend(
        [
            "",
            "## Recommendations",
            "- Use Gemma for prose and other fast visible tasks when it is loaded in LM Studio.",
            "- Keep Story State, summaries, title generation, and image prompt caching in background so first visible text is not delayed.",
            "- Use Kokoro pre-synthesis or first-chunk-fast narration when the goal is fastest click-to-audio.",
            "- Keep ComfyUI running, but use its cleanup settings so models are freed when writing speed matters.",
            "- Use `image_performance_smoke_test.bat --auto-priority` for the heavier real Z-Image timing pass.",
        ]
    )
    REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure StoryDriver end-to-end latency.")
    parser.add_argument("--no-chapter", action="store_true", help="Skip the chapter timing run.")
    parser.add_argument("--chapter", action="store_true", help="Force chapter timing run. Enabled by default unless --no-chapter is used.")
    parser.add_argument("--auto-priority", action="store_true", help="Also run one real image generation job using current image settings.")
    args = parser.parse_args()

    health = api_json("/health", timeout=10)
    if not isinstance(health, dict) or not health.get("ok"):
        raise RuntimeError("Backend health is not OK.")
    diagnostics = api_json("/diagnostics", timeout=30)
    model_settings = api_json("/settings/model", timeout=20)
    routing = api_json("/settings/task-model-profiles", timeout=20)
    try:
        resource_status = api_json("/resource-status", timeout=20)
    except Exception:
        resource_status = None
    session = get_or_create_session()
    session_id = session["id"]
    fast_result = generate_stream(session_id, FAST_NOTE, label="fast_scene", timeout=900)
    chapter_result = None
    if not args.no_chapter:
        chapter_result = generate_stream(session_id, CHAPTER_NOTE, label="chapter", timeout=1200)
    text_for_tts = (fast_result["scene"].get("generated_text") or "").strip()
    tts_result = synthesize_latency(text_for_tts)
    image_prompt = fetch_image_prompt(session_id, fast_result["scene"])
    image_result = run_image_latency(session_id, fast_result["scene"]) if args.auto_priority else None
    write_report(
        diagnostics=diagnostics or {},
        model_settings=model_settings or {},
        routing=routing or {},
        resource_status=resource_status,
        session=session,
        fast_result=fast_result,
        chapter_result=chapter_result,
        tts_result=tts_result,
        image_prompt=image_prompt,
        image_result=image_result,
    )
    print(f"[OK] report written: {REPORT}")
    print(
        "[OK] fast scene first delta: "
        f"{fast_result.get('submit_to_first_delta_seconds')}s, "
        f"scene event: {fast_result.get('submit_to_scene_event_seconds')}s"
    )
    if chapter_result:
        print(
            "[OK] chapter first delta: "
            f"{chapter_result.get('submit_to_first_delta_seconds')}s, "
            f"scene event: {chapter_result.get('submit_to_scene_event_seconds')}s"
        )
    if tts_result.get("ok"):
        print(f"[OK] Kokoro synth wall time: {tts_result.get('wall_seconds')}s cached={tts_result.get('cached')}")
    elif tts_result.get("skipped"):
        print(f"[WARN] TTS skipped: {tts_result.get('reason')}")
    else:
        print(f"[WARN] TTS failed: {tts_result.get('error')}")
    if image_result:
        print(f"[INFO] image result: {image_result}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            "# StoryDriver Latency Optimization Report\n\n"
            f"Updated: {now_iso()}\n\n"
            "## Failure\n"
            f"- {error}\n",
            encoding="utf-8",
        )
        print(f"[FAIL] {error}")
        sys.exit(1)
