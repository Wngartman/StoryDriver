from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "LMSTUDIO_CURRENT_SPEED_TEST_REPORT.md"
LATEST_JSON_PATH = LOG_DIR / "LMSTUDIO_CURRENT_SPEED_TEST_LATEST.json"
HISTORY_PATH = LOG_DIR / "LMSTUDIO_CURRENT_SPEED_TEST_HISTORY.jsonl"
ROOT_CAUSE_REPORT_PATH = LOG_DIR / "SYSTEM_SPEED_ROOT_CAUSE_REPORT.md"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"

import sys

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT / "scripts"))

from app.utils.openssl_dlls import add_openssl_dll_directory  # noqa: E402

add_openssl_dll_directory()

from app.config import settings  # noqa: E402
from app.routes.sessions import (  # noqa: E402
    build_prompt_diagnostics,
    generation_parameters,
    prepare_generation,
    prose_task_notes_for_prompt,
)
from app.schemas import GenerateSceneRequest  # noqa: E402
from app.generation.router import resolve_task_model_settings  # noqa: E402
from app.generation.prompt_builder import build_scene_prompt, resolve_writing_length  # noqa: E402
from lmstudio_speed_diagnostics import (  # noqa: E402
    StreamResult,
    capped_parameters,
    choose_model,
    estimate_tokens,
    loaded_instances_from_payload,
    safe_json,
    stream_lmstudio_chat,
)
from system_speed_diagnostics import runtime_metadata  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8", errors="replace")) if raw else None


def backend_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
) -> Any:
    return request_json(f"{base_url.rstrip('/')}{path}", method=method, payload=payload, timeout=timeout)


def get_or_create_speed_session(backend_url: str) -> str:
    title = "StoryDriver System Speed Test"
    sessions = backend_json(backend_url, "/sessions", timeout=20)
    if isinstance(sessions, list):
        existing = next((item for item in sessions if item.get("title") == title), None)
        if existing and existing.get("id"):
            return str(existing["id"])
    created = backend_json(backend_url, "/sessions", method="POST", payload={"title": title}, timeout=20)
    return str(created["id"])


def stream_storydriver_generation(backend_url: str, note: str) -> dict[str, Any]:
    try:
        session_id = get_or_create_speed_session(backend_url)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"Could not create/use diagnostic story: {error}"}
    started = time.perf_counter()
    first_status: float | None = None
    first_delta: float | None = None
    chunks = 0
    chars = 0
    stages: list[str] = []
    final_scene: dict[str, Any] | None = None
    try:
        req = urllib.request.Request(
            f"{backend_url.rstrip('/')}/sessions/{session_id}/generate-stream",
            data=json.dumps({"director_note": note, "mode": "continue"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=420) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "status":
                    stages.append(str(event.get("stage") or event.get("message") or "status"))
                    if first_status is None:
                        first_status = time.perf_counter()
                elif event.get("type") == "delta":
                    text = str(event.get("text") or "")
                    if first_delta is None:
                        first_delta = time.perf_counter()
                    chunks += 1
                    chars += len(text)
                elif event.get("type") == "scene":
                    final_scene = event.get("scene")
                elif event.get("type") == "error":
                    return {
                        "ok": False,
                        "error": str(event.get("detail") or "StoryDriver stream error"),
                        "stages": stages,
                        "wall_seconds": round(time.perf_counter() - started, 3),
                    }
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        return {"ok": False, "error": f"HTTP {error.code}: {detail[:800]}", "stages": stages}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error), "stages": stages}

    stats: dict[str, Any] = {}
    if final_scene:
        versions = final_scene.get("versions") or []
        stats = (versions[-1].get("generation_stats") if versions else final_scene.get("generation_stats")) or {}
    return {
        "ok": bool(final_scene),
        "session_id": session_id,
        "scene_id": final_scene.get("id") if final_scene else None,
        "first_status_seconds": round(first_status - started, 3) if first_status else None,
        "first_delta_seconds": round(first_delta - started, 3) if first_delta else None,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "chunks": chunks,
        "streamed_chars": chars,
        "streamed_tokens_estimated": estimate_tokens("x" * chars) if chars else 0,
        "stages": stages,
        "generation_stats": stats,
    }


def build_storydriver_like_prompt() -> tuple[str, dict[str, Any], dict[str, Any], Any, Any]:
    resolved_settings, resolved = resolve_task_model_settings("prose_generation")
    prompt_mode = getattr(resolved_settings, "prose_prompt_mode", "standard")
    task_notes = prose_task_notes_for_prompt(resolved.notes or "", prompt_mode)
    director_note = (
        "Beat: Write a grounded medieval prose scene around 450 words. "
        "Three sisters sit at a farm table before dawn and decide whether to rescue a captured child. "
        "No magic, no headings, no explanation, prose only."
    )
    writing_length = resolve_writing_length(director_note=director_note, configured_mode=resolved_settings.writing_length_mode)
    try:
        prepared = prepare_generation(
            "__speed_diagnostic_unscoped__",
            GenerateSceneRequest(director_note=director_note, mode="continue"),
        )
        user_prompt = build_scene_prompt(
            session_id="__speed_diagnostic_unscoped__",
            director_note=prepared["director_note"],
            mode="continue",
            recent_scenes=prepared.get("recent_scenes") or [],
            session_summary=prepared.get("session_summary"),
            target_scene=prepared.get("target_scene"),
            task_notes=task_notes,
            writing_length=prepared.get("writing_length") or writing_length,
        )
    except Exception:
        user_prompt = build_scene_prompt(
            session_id="__speed_diagnostic_no_state__",
            director_note=director_note,
            mode="continue",
            recent_scenes=[],
            session_summary=None,
            memories=[],
            world_notes=None,
            active_characters=[],
            task_notes=task_notes,
            writing_length=writing_length,
        )
    prompt_diag = build_prompt_diagnostics(
        system_prompt=resolved_settings.system_prompt,
        user_prompt=user_prompt,
        task_notes=task_notes,
        director_note=director_note,
        session_summary=None,
        recent_scenes=[],
    )
    return user_prompt, prompt_diag, writing_length, resolved_settings, resolved


def value(value_: Any, suffix: str = "") -> str:
    if value_ is None:
        return "unknown"
    return f"{value_}{suffix}"


def result_lines(result: StreamResult) -> list[str]:
    if not result.ok:
        return [f"### {result.label}", f"- ERROR: {result.error or 'unknown'}"]
    return [
        f"### {result.label}",
        f"- First stream event: {value(result.first_any_seconds, 's')}",
        f"- First visible prose: {value(result.first_content_seconds, 's')}",
        f"- Hidden reasoning chars: {result.reasoning_chars}",
        f"- Visible output: {result.output_chars:,} chars / {result.output_tokens_estimated} est. tokens",
        f"- Visible prose speed: {value(result.visible_tokens_per_second_estimated, ' est. tok/s')}",
        f"- Raw stream speed: {value(result.tokens_per_second_estimated, ' est. tok/s')}",
        f"- Total wall time: {value(result.total_seconds, 's')}",
        f"- Prompt estimate: {result.prompt_chars:,} chars / {result.prompt_tokens_estimated} est. tokens",
    ]


def write_reports(run: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    with HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")

    results = [StreamResult(**item) for item in run.get("direct_results", [])]
    storydriver = run.get("storydriver_result") or {}
    lm = run.get("lm") or {}
    runtime = lm.get("runtime_metadata") or {}
    loaded = lm.get("loaded_instances") or []
    prompt_diag = run.get("prompt_diagnostics") or {}
    lines = [
        "# LM Studio Current Speed Test",
        "",
        f"Generated: {run['generated_at']}",
        f"Run label: `{run['label']}`",
        "",
        "## Current Runtime",
        "",
        f"- Model tested: `{run.get('model') or 'unknown'}`",
        f"- Expected fast Gemma: `{GEMMA_MODEL}`",
        f"- Loaded LM Studio instances reported by REST: {len(loaded)}",
        f"- Runtime/offload metadata visible: {'yes' if runtime.get('runtime_info_visible') else 'no'}",
        f"- GPU/offload visible: {'yes' if runtime.get('gpu_offload_visible') else 'no'}",
        f"- Context length visible: {'yes' if runtime.get('context_length_visible') else 'no'}",
        f"- Flash/KV fields visible: {'yes' if runtime.get('flash_attention_visible') or runtime.get('kv_cache_visible') else 'no'}",
        f"- ComfyUI queue busy before test: {run.get('comfyui_queue_busy_before')}",
        "",
        "Loaded instances:",
    ]
    if loaded:
        for instance in loaded[:10]:
            lines.append(f"- `{instance.get('display_name') or instance.get('model_key') or instance.get('id')}` id `{instance.get('id') or instance.get('instance_id') or instance.get('instanceId') or ''}`")
    else:
        lines.append("- None reported by REST API.")
    if runtime.get("fields"):
        lines.extend(["", "Runtime fields exposed by REST:"])
        for field in runtime["fields"][:18]:
            lines.append(f"- `{field.get('path')}` = `{field.get('value')}`")
    lines.extend(
        [
            "",
            "## Prompt / Task Settings",
            "",
            f"- Task profile: `{run.get('task_profile')}` / {run.get('task_label')}",
            f"- Backend configured for prose: `{run.get('inference_backend')}`",
            f"- Reasoning mode configured for prose: `{run.get('reasoning_mode')}`",
            f"- Temperature/top_p: {run.get('temperature')} / {run.get('top_p')}",
            f"- Max tokens sent for diagnostics: {run.get('diagnostic_max_tokens')}",
            f"- Prompt chars: {prompt_diag.get('total_prompt_chars')}",
            f"- Prompt estimated tokens: {prompt_diag.get('prompt_estimated_tokens')}",
            "",
            "## Direct LM Studio OpenAI-Compatible Tests",
            "",
        ]
    )
    for result in results:
        lines.extend(result_lines(result))
        lines.append("")
    lines.extend(["## StoryDriver Generate-Stream Test", ""])
    if storydriver.get("ok"):
        stats = storydriver.get("generation_stats") or {}
        lines.extend(
            [
                f"- First frontend delta from backend stream: {value(storydriver.get('first_delta_seconds'), 's')}",
                f"- Wall time: {value(storydriver.get('wall_seconds'), 's')}",
                f"- Streamed chars/chunks: {storydriver.get('streamed_chars')} / {storydriver.get('chunks')}",
                f"- Saved visible t/s: {value(stats.get('visible_prose_tokens_per_second') or stats.get('tokens_per_second'), ' tok/s')}",
                f"- Saved first visible latency: {value(stats.get('first_visible_latency_seconds'), 's')}",
                f"- Saved hidden reasoning chars: {stats.get('reasoning_chars', 0)}",
                f"- Background jobs delayed until after save: {stats.get('background_jobs_delayed_until_after_save')}",
            ]
        )
    else:
        lines.append(f"- StoryDriver stream skipped/failed: {storydriver.get('error') or 'not run'}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            *interpretation_lines(run),
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_root_cause_report()


def interpretation_lines(run: dict[str, Any]) -> list[str]:
    results = run.get("direct_results") or []
    minimal = next((item for item in results if item.get("label") == "direct_minimal_current"), None)
    story_like = next((item for item in results if item.get("label") == "direct_storydriver_like_current"), None)
    lines: list[str] = []
    minimal_speed = (minimal or {}).get("visible_tokens_per_second_estimated")
    story_like_speed = (story_like or {}).get("visible_tokens_per_second_estimated")
    if minimal_speed is not None and float(minimal_speed) < 70:
        lines.append("- Direct LM Studio API minimal prompt is already below the previous 120-130+ t/s target, so the bottleneck is outside StoryDriver prompt building.")
    if minimal_speed and story_like_speed and float(story_like_speed) < float(minimal_speed) * 0.75:
        lines.append("- StoryDriver-like prompt/context is materially slower than the minimal prompt in this run.")
    if minimal and int(minimal.get("reasoning_chars") or 0) <= 20:
        lines.append("- Hidden reasoning is not the active bottleneck in this run.")
    if run.get("comfyui_queue_busy_before"):
        lines.append("- ComfyUI had an active/pending queue during the speed test; repeat when idle for a cleaner baseline.")
    if not lines:
        lines.append("- No single cause was proven by this current-speed run; compare with the system diagnostics and the after-/free run.")
    return lines


def read_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in HISTORY_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def speed_from_run(run: dict[str, Any], label: str = "direct_minimal_current") -> float | None:
    for item in run.get("direct_results") or []:
        if item.get("label") == label and item.get("visible_tokens_per_second_estimated") is not None:
            return float(item["visible_tokens_per_second_estimated"])
    return None


def write_root_cause_report() -> None:
    history = read_history()
    latest_system = LOG_DIR / "SYSTEM_SPEED_DIAGNOSTICS_LATEST.json"
    system: dict[str, Any] = {}
    if latest_system.exists():
        try:
            system = json.loads(latest_system.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            system = {}
    latest = history[-1] if history else {}
    as_is = next((run for run in reversed(history) if "as" in str(run.get("label", "")).lower()), None)
    after_free = next((run for run in reversed(history) if "free" in str(run.get("label", "")).lower()), None)
    as_is_speed = speed_from_run(as_is or {}) if as_is else None
    after_speed = speed_from_run(after_free or {}) if after_free else None
    free_delta = None
    if as_is_speed and after_speed:
        free_delta = round(after_speed - as_is_speed, 2)
    classified = system.get("classified_processes") or {}
    http = system.get("http") or {}
    lms_cli = system.get("lms_cli") or {}
    lms_ps = ((lms_cli.get("ps") or {}).get("stdout") or "").strip()
    free = http.get("comfyui_free_impact") or {}
    runtime = (latest.get("lm") or {}).get("runtime_metadata") or http.get("lm_runtime_metadata") or {}
    loaded = (latest.get("lm") or {}).get("loaded_instances") or http.get("lm_loaded_instances") or []
    duplicate_count = len(classified.get("duplicate_comfyui_candidates") or [])
    lines = [
        "# System Speed Root Cause Report",
        "",
        f"Updated: {utc_now()}",
        "",
        "## Current Finding",
        "",
        f"- Current direct minimal LM Studio speed: {value(speed_from_run(latest), ' est. visible tok/s')}",
        f"- As-is baseline speed: {value(as_is_speed, ' est. visible tok/s')}",
        f"- After ComfyUI /free speed: {value(after_speed, ' est. visible tok/s')}",
        f"- /free speed delta: {value(free_delta, ' est. tok/s')}",
        f"- Earlier expected target: 120-130+ t/s",
        f"- Hidden reasoning in latest minimal run: {next((item.get('reasoning_chars') for item in latest.get('direct_results') or [] if item.get('label') == 'direct_minimal_current'), 'unknown')}",
        "",
        "## Runtime / Process Evidence",
        "",
        f"- Loaded LM Studio instances: {len(loaded)}",
        f"- Model tested: `{latest.get('model') or 'unknown'}`",
        f"- Runtime/offload metadata visible: {'yes' if runtime.get('runtime_info_visible') else 'no'}",
        f"- GPU/offload visible through REST: {'yes' if runtime.get('gpu_offload_visible') else 'no'}",
        f"- Context length visible through REST: {'yes' if runtime.get('context_length_visible') else 'no'}",
        f"- Flash/KV visible through REST: {'yes' if runtime.get('flash_attention_visible') or runtime.get('kv_cache_visible') else 'no'}",
        f"- LM Studio CLI available: {'yes' if lms_cli.get('found') else 'no'}",
        f"- `lms ps`: `{lms_ps.splitlines()[-1].strip() if lms_ps.splitlines() else 'not available'}`",
        f"- ComfyUI processes found: {len(classified.get('comfyui') or [])}",
        f"- Duplicate ComfyUI candidates: {duplicate_count}",
        f"- Active ComfyUI 8188 PID: {classified.get('active_comfyui_8188_pid') or 'not detected'}",
        f"- ComfyUI /free attempted: {free.get('attempted') if free else 'not yet'}",
        f"- ComfyUI /free result: {free.get('ok') if free else 'not yet'}",
        f"- ComfyUI /free error/skip: {free.get('error') or free.get('skip_reason') or 'none'}",
        "",
        "## Background Contention",
        "",
        f"- StoryDriver image job status: {((http.get('backend_image_job_status') or {}).get('payload') or {}).get('status') or 'unknown'}",
        f"- TTS status reachable: {((http.get('backend_tts_status') or {}).get('ok')) if http else 'unknown'}",
        f"- ComfyUI queue busy before latest direct test: {latest.get('comfyui_queue_busy_before')}",
        "",
        "## Conclusion",
        "",
    ]
    conclusion: list[str] = []
    latest_speed = speed_from_run(latest)
    if latest_speed is not None and latest_speed < 70:
        conclusion.append("- The direct LM Studio API minimal prompt is slow too, so this run does not point at StoryDriver prompt size as the primary cause.")
    if free_delta is not None:
        if free_delta >= 15:
            conclusion.append("- ComfyUI /free materially improved direct LM Studio speed; enable throttled Free ComfyUI before writing and let the regenerate warm window expire or finish before prose.")
        elif abs(free_delta) < 10:
            conclusion.append("- ComfyUI /free did not materially improve direct LM Studio speed in this pair of runs.")
        else:
            conclusion.append("- ComfyUI /free made this direct run slower or noisier; repeat once before drawing a strong conclusion.")
    if duplicate_count:
        conclusion.append("- Duplicate/stale ComfyUI candidates were detected. Review the cleanup script dry-run before stopping anything.")
    if runtime and not runtime.get("gpu_offload_visible"):
        conclusion.append("- LM Studio did not expose enough GPU/offload/runtime detail through REST to prove whether the loaded runtime matches the fast UI runtime.")
    if " 4 " in f" {lms_ps} " or "    4" in lms_ps:
        conclusion.append("- LM Studio CLI shows the loaded Gemma instance has `parallel` set to 4; LM Studio documents that higher parallelism can reduce individual prediction speed. Reloading with `--parallel 1 --gpu max` is the safest next speed recovery test.")
    if latest.get("model") and GEMMA_MODEL not in str(latest.get("model")):
        conclusion.append("- The tested model does not match the preferred Gemma model name.")
    if not conclusion:
        conclusion.append("- Root cause remains unproven from available telemetry; next safe isolation is a manual Gemma reload, then optional manual ComfyUI-closed test.")
    lines.extend(conclusion)
    lines.extend(
        [
            "",
            "## Recommended Recovery Steps",
            "",
            "1. If direct LM Studio is still below 70 t/s after `/free`, unload and reload Gemma manually in LM Studio with the no-thinking StoryDriver template and the known-fast runtime/offload preset.",
            "2. Prefer a single-stream reload for prose speed: `lms load gemma4-26b-a4b-uncensored-hauhaucs-balanced --gpu max --context-length 50749 --parallel 1 --identifier gemma4-26b-a4b-uncensored-hauhaucs-balanced -y` if you are comfortable using the CLI.",
            "3. Keep only the Gemma model loaded while testing speed.",
            "4. Run `D:\\StoryDriver\\scripts\\lmstudio_current_speed_test.bat --label after_manual_gemma_reload`.",
            "5. If speed is still slow, run the prepared manual ComfyUI-closed test only when you are ready to temporarily stop and restart ComfyUI.",
            "6. If `/free` helped, enable StoryDriver's throttled Free ComfyUI before writing setting and keep the fast regenerate window finite.",
        ]
    )
    ROOT_CAUSE_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure current LM Studio speed without unloading/reloading the loaded model.")
    parser.add_argument("--label", default="as_is", help="Label for this run, e.g. as_is or after_comfy_free.")
    parser.add_argument("--openai-url", default=os.environ.get("LM_STUDIO_BASE_URL") or settings.lm_studio_base_url)
    parser.add_argument("--rest-url", default=os.environ.get("LM_STUDIO_REST_BASE_URL") or settings.lm_studio_rest_base_url)
    parser.add_argument("--backend-url", default=os.environ.get("STORYDRIVER_BACKEND_URL") or settings.storydriver_backend_url)
    parser.add_argument("--comfy-url", default=os.environ.get("COMFYUI_BASE_URL") or settings.comfyui_base_url)
    parser.add_argument("--max-output-tokens", type=int, default=420)
    parser.add_argument("--skip-storydriver", action="store_true")
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    load_env_file(ROOT / "backend" / ".env")

    openai_url = args.openai_url.rstrip("/")
    rest_url = args.rest_url.rstrip("/")
    comfy_url = args.comfy_url.rstrip("/")
    resolved_settings, resolved = resolve_task_model_settings("prose_generation")
    model = choose_model(openai_url, resolved.model or resolved_settings.model)
    storydriver_prompt, prompt_diag, writing_length, _, _ = build_storydriver_like_prompt()
    params = generation_parameters(resolved_settings, writing_length)
    diagnostic_params, configured_max_tokens = capped_parameters(params, args.max_output_tokens, False)

    rest_payload, rest_error = safe_json(f"{rest_url}/models", timeout=15)
    queue_payload, _queue_error = safe_json(f"{comfy_url}/queue", timeout=8)
    queue_busy = False
    if isinstance(queue_payload, dict):
        queue_busy = bool(queue_payload.get("queue_running") or queue_payload.get("queue_pending"))
    runtime = runtime_metadata(rest_payload)
    loaded = loaded_instances_from_payload(rest_payload)

    minimal = stream_lmstudio_chat(
        label="direct_minimal_current",
        base_url=openai_url,
        model=model,
        system_prompt="You are a fast local prose generator. Return prose only.",
        user_prompt="Write one grounded medieval paragraph about rain on a farm road. No heading, no explanation.",
        parameters=diagnostic_params,
        configured_max_tokens=configured_max_tokens,
        timeout=float(resolved.timeout_seconds or 120),
    )
    story_like = stream_lmstudio_chat(
        label="direct_storydriver_like_current",
        base_url=openai_url,
        model=model,
        system_prompt=resolved_settings.system_prompt,
        user_prompt=storydriver_prompt,
        parameters=diagnostic_params,
        configured_max_tokens=configured_max_tokens,
        timeout=float(resolved.timeout_seconds or 120),
    )
    storydriver_result = None
    if not args.skip_storydriver:
        storydriver_result = stream_storydriver_generation(
            args.backend_url,
            "Beat: System speed diagnostic. Write about three sisters leaving a farm before dawn, grounded prose only, no magic, around 350 words.",
        )
    run = {
        "generated_at": utc_now(),
        "label": args.label,
        "model": model,
        "openai_url": openai_url,
        "rest_url": rest_url,
        "backend_url": args.backend_url,
        "comfy_url": comfy_url,
        "task_profile": resolved.task_type,
        "task_label": resolved.label,
        "inference_backend": resolved.inference_backend,
        "reasoning_mode": resolved.reasoning_mode,
        "temperature": diagnostic_params.get("temperature"),
        "top_p": diagnostic_params.get("top_p"),
        "diagnostic_max_tokens": diagnostic_params.get("max_tokens"),
        "configured_max_tokens": configured_max_tokens,
        "comfyui_queue_busy_before": queue_busy,
        "lm": {
            "rest_error": rest_error,
            "runtime_metadata": runtime,
            "loaded_instances": loaded,
        },
        "prompt_diagnostics": prompt_diag,
        "direct_results": [minimal.as_dict(), story_like.as_dict()],
        "storydriver_result": storydriver_result,
    }
    write_reports(run)
    print(json.dumps({
        "report": str(REPORT_PATH),
        "root_cause_report": str(ROOT_CAUSE_REPORT_PATH),
        "label": args.label,
        "model": model,
        "direct_minimal_visible_tps": minimal.visible_tokens_per_second_estimated,
        "direct_storydriver_like_visible_tps": story_like.visible_tokens_per_second_estimated,
        "storydriver_visible_tps": ((storydriver_result or {}).get("generation_stats") or {}).get("visible_prose_tokens_per_second") if storydriver_result else None,
        "hidden_reasoning_chars": minimal.reasoning_chars,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
