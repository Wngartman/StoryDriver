from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "VRAM_SPEED_ROOT_CAUSE_REPORT.md"
LATEST_JSON_PATH = LOG_DIR / "VRAM_SPEED_RECOVERY_LATEST.json"
PREFLIGHT_REPORT_PATH = LOG_DIR / "VRAM_SPEED_RECOVERY_PREFLIGHT_REPORT.md"
PREFLIGHT_JSON_PATH = LOG_DIR / "VRAM_SPEED_RECOVERY_PREFLIGHT_LATEST.json"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
LMS_EXE = Path.home() / ".lmstudio" / "bin" / "lms.exe"
DEFAULT_CONTEXTS = [50749, 32768, 24576, 16384]

sys.path.insert(0, str(ROOT / "scripts"))

from comfyui_hard_sleep import run_hard_sleep  # noqa: E402
from vram_speed_diagnostics import (  # noqa: E402
    bytes_label,
    collect_gpu_memory_counters,
    collect_vram_report,
    direct_result,
    load_env,
    lms_ps_json,
    request_json,
    run_command,
    run_speed_test,
    select_gemma,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def queue_busy(queue: Any) -> bool:
    if not isinstance(queue, dict):
        return False
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return bool((isinstance(running, list) and running) or (isinstance(pending, list) and pending))


def preflight(force: bool = False) -> dict[str, Any]:
    env = load_env()
    backend_url = env.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001").rstrip("/")
    comfy_url = env.get("COMFYUI_BASE_URL", "http://localhost:8188").rstrip("/")
    image_status, image_error = request_json(f"{backend_url}/images/job-status", timeout=10)
    tts_status, tts_error = request_json(f"{backend_url}/tts/status", timeout=10)
    diagnostics, diagnostics_error = request_json(f"{backend_url}/diagnostics", timeout=20)
    queue, queue_error = request_json(f"{comfy_url}/queue", timeout=10)
    blockers: list[str] = []
    if isinstance(image_status, dict) and image_status.get("active"):
        blockers.append("StoryDriver image generation is active.")
    if queue_busy(queue):
        blockers.append("ComfyUI queue is busy.")
    generation_state = ((diagnostics or {}).get("images") or {}).get("generation_state") or {}
    if isinstance(generation_state, dict) and generation_state.get("story_generation_active"):
        blockers.append("StoryDriver story generation is active.")
    return {
        "ok": force or not blockers,
        "force": force,
        "blockers": blockers,
        "backend_url": backend_url,
        "comfy_url": comfy_url,
        "image_status": image_status,
        "image_error": image_error,
        "tts_status": tts_status,
        "tts_error": tts_error,
        "diagnostics_error": diagnostics_error,
        "queue": queue,
        "queue_error": queue_error,
    }


def load_help_flags() -> dict[str, Any]:
    if not LMS_EXE.exists():
        return {"ok": False, "error": f"lms.exe not found at {LMS_EXE}", "flags": {}}
    result = run_command([str(LMS_EXE), "load", "--help"], timeout=30)
    text = result.get("stdout") or ""
    flags = {
        "gpu": "--gpu" in text,
        "context_length": "--context-length" in text,
        "parallel": "--parallel" in text,
        "identifier": "--identifier" in text,
        "estimate_only": "--estimate-only" in text,
        "yes": "-y, --yes" in text or "--yes" in text,
    }
    result["flags"] = flags
    result["all_required"] = all(flags.values())
    return result


def lms_load_args(context_length: int, *, estimate_only: bool = False) -> list[str]:
    args = [
        str(LMS_EXE),
        "load",
        GEMMA_MODEL,
        "--gpu",
        "max",
        "--context-length",
        str(context_length),
        "--parallel",
        "1",
        "--identifier",
        GEMMA_MODEL,
        "-y",
    ]
    if estimate_only:
        args.append("--estimate-only")
    return args


def unload_all_models() -> dict[str, Any]:
    return run_command([str(LMS_EXE), "unload", "--all"], timeout=180)


def wait_for_no_loaded(timeout_seconds: float = 90) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_seconds
    last_instances: list[dict[str, Any]] = []
    while time.perf_counter() < deadline:
        instances, result = lms_ps_json()
        last_instances = instances
        if result.get("ok") and not instances:
            return {"ok": True, "instances": instances, "result": result}
        time.sleep(2)
    return {"ok": False, "instances": last_instances}


def wait_for_gemma_context(context_length: int, timeout_seconds: float = 180) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_seconds
    last_instances: list[dict[str, Any]] = []
    while time.perf_counter() < deadline:
        instances, result = lms_ps_json()
        last_instances = instances
        gemma = select_gemma(instances)
        if (
            result.get("ok")
            and gemma
            and safe_int(gemma.get("parallel")) == 1
            and safe_int(gemma.get("contextLength")) == context_length
        ):
            return {"ok": True, "instances": instances, "gemma": gemma, "result": result}
        time.sleep(2)
    return {"ok": False, "instances": last_instances, "gemma": select_gemma(last_instances)}


def estimate_memory(context_length: int) -> dict[str, Any]:
    return run_command(lms_load_args(context_length, estimate_only=True), timeout=180)


def test_context(context_length: int, *, label_prefix: str) -> dict[str, Any]:
    started = time.perf_counter()
    gpu_before = collect_gpu_memory_counters()
    estimate = estimate_memory(context_length)
    unload = unload_all_models()
    wait_unloaded = wait_for_no_loaded()
    load = run_command(lms_load_args(context_length), timeout=900)
    wait_loaded = wait_for_gemma_context(context_length)
    gpu_after_load = collect_gpu_memory_counters()
    speed = run_speed_test(f"{label_prefix}_{context_length}", skip_storydriver=True)
    latest = speed.get("latest") or {}
    minimal = direct_result(latest, "direct_minimal_current")
    story_like = direct_result(latest, "direct_storydriver_like_current")
    gemma = wait_loaded.get("gemma") or select_gemma((wait_loaded.get("instances") or []))
    return {
        "context_length": context_length,
        "estimate": estimate,
        "unload": unload,
        "wait_unloaded": wait_unloaded,
        "load": load,
        "wait_loaded": wait_loaded,
        "gemma": gemma,
        "gpu_before": gpu_before,
        "gpu_after_load": gpu_after_load,
        "speed": speed,
        "direct_minimal_tps": minimal.get("visible_tokens_per_second_estimated"),
        "direct_storydriver_like_tps": story_like.get("visible_tokens_per_second_estimated"),
        "first_visible_seconds": minimal.get("first_content_seconds"),
        "hidden_reasoning_chars": minimal.get("reasoning_chars"),
        "total_seconds": round(time.perf_counter() - started, 3),
    }


def best_context(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [item for item in results if item.get("direct_minimal_tps") is not None]
    if not valid:
        return None
    return max(valid, key=lambda item: float(item.get("direct_minimal_tps") or 0))


def adapter_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return snapshot.get("adapter_summary") or {}


def write_report(run: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    before = run.get("before") or {}
    before_gemma = before.get("gemma") or {}
    before_system = before.get("system") or {}
    before_classified = before_system.get("classified_processes") or {}
    before_speed = direct_result(((before.get("speed") or {}).get("latest") or {}))
    hard_sleep = run.get("hard_sleep") or {}
    hard_before = direct_result(((hard_sleep.get("speed_before") or {}).get("latest") or {}))
    hard_after = direct_result(((hard_sleep.get("speed_after") or {}).get("latest") or {}))
    results = run.get("context_results") or []
    best = run.get("best_context") or {}
    final_instances = run.get("final_lms_instances") or []
    final_gemma = select_gemma(final_instances) or {}
    lines = [
        "# VRAM Speed Root Cause Report",
        "",
        f"Generated: {run.get('generated_at')}",
        "",
        "## Current LM Studio Speed",
        "",
        f"- Target speed: 120-130+ t/s",
        f"- Initial direct minimal speed: {before_speed.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
        f"- Initial hidden reasoning chars: {before_speed.get('reasoning_chars', 'not measured')}",
        f"- Initial Gemma context: {before_gemma.get('contextLength', 'unknown')}",
        f"- Initial Gemma parallel: {before_gemma.get('parallel', 'unknown')}",
        "",
        "## GPU / Shared Memory Evidence",
        "",
    ]
    before_gpu = ((before.get("gpu_memory") or {}).get("adapter_summary") or {})
    lines.extend(
        [
            f"- Windows GPU dedicated usage: {bytes_label(before_gpu.get('dedicated_usage_bytes'))}",
            f"- Windows GPU shared usage: {bytes_label(before_gpu.get('shared_usage_bytes'))}",
            "- User manual Task Manager observation: dedicated about 19.7/24.0 GB, shared about 6.5/30.8 GB, total about 26.2/54.8 GB.",
        ]
    )
    lines.extend(
        [
            "",
            "## ComfyUI Findings",
            "",
            f"- Hard sleep skipped: {hard_sleep.get('skipped')}",
            f"- Hard sleep succeeded: {hard_sleep.get('free_succeeded')}",
            f"- Speed before hard sleep: {hard_before.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
            f"- Speed after hard sleep: {hard_after.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
            f"- Hard sleep error: {hard_sleep.get('error') or hard_sleep.get('skip_reason') or 'none'}",
            f"- ComfyUI process count: {len(before_classified.get('comfyui') or [])}",
            f"- Active 8188 backend PID: {before_classified.get('active_comfyui_8188_pid') or 'not detected'}",
            f"- Duplicate/stale ComfyUI candidates: {len(before_classified.get('duplicate_comfyui_candidates') or [])}",
            "",
            "## Context-Size Speed Table",
            "",
            "| context | estimated memory | direct t/s | StoryDriver-like t/s | first visible | hidden reasoning | notes |",
            "| ---: | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for item in results:
        estimate_stdout = ((item.get("estimate") or {}).get("stdout") or "") + "\n" + ((item.get("estimate") or {}).get("stderr") or "")
        memory_line = "unknown"
        for line in estimate_stdout.splitlines():
            if "Estimated GPU Memory" in line:
                memory_line = line.split(":", 1)[-1].strip()
                break
        notes: list[str] = []
        gemma = item.get("gemma") or {}
        if safe_int(gemma.get("parallel")) != 1:
            notes.append("parallel not 1")
        if safe_int(item.get("hidden_reasoning_chars")) > 50:
            notes.append("reasoning returned")
        if not (item.get("load") or {}).get("ok"):
            notes.append("load failed")
        lines.append(
            f"| {item.get('context_length')} | {memory_line} | {item.get('direct_minimal_tps') or 'n/a'} | "
            f"{item.get('direct_storydriver_like_tps') or 'n/a'} | {item.get('first_visible_seconds') or 'n/a'} | "
            f"{item.get('hidden_reasoning_chars') if item.get('hidden_reasoning_chars') is not None else 'n/a'} | {', '.join(notes) or 'ok'} |"
        )
    lines.extend(
        [
            "",
            "## Final Loaded Runtime",
            "",
            f"- Final Gemma loaded: {'yes' if final_gemma else 'no'}",
            f"- Final context: {final_gemma.get('contextLength', 'unknown')}",
            f"- Final parallel: {final_gemma.get('parallel', 'unknown')}",
            f"- Best tested context: {best.get('context_length', 'unknown')}",
            f"- Best direct speed: {best.get('direct_minimal_tps', 'unknown')} est. tok/s",
            f"- Kept best context loaded: {run.get('kept_best_context_loaded')}",
            "",
            "## Root Cause Assessment",
            "",
        ]
    )
    if safe_int(before_gemma.get("parallel")) > 1:
        lines.append("- Gemma had again been loaded with parallel/concurrency greater than 1, which is a proven speed killer for single-scene writing.")
    if before_gpu.get("shared_usage_bytes") and float(before_gpu["shared_usage_bytes"]) > 1024**3:
        lines.append("- Windows counters confirm significant shared GPU memory use, supporting the VRAM spill/pressure theory.")
    if hard_after.get("visible_tokens_per_second_estimated") and hard_before.get("visible_tokens_per_second_estimated"):
        delta = float(hard_after["visible_tokens_per_second_estimated"]) - float(hard_before["visible_tokens_per_second_estimated"])
        if delta >= 15:
            lines.append("- ComfyUI hard sleep materially improved LM Studio speed.")
        else:
            lines.append("- ComfyUI hard sleep alone did not materially restore LM Studio speed.")
    if before_classified.get("duplicate_comfyui_candidates"):
        lines.append(
            f"- {len(before_classified.get('duplicate_comfyui_candidates') or [])} duplicate/stale ComfyUI candidate was detected; the active 8188 backend was preserved."
        )
    if best and float(best.get("direct_minimal_tps") or 0) >= 100:
        lines.append("- A lower/clean context profile restored near-target speed without a reboot.")
    elif best:
        lines.append("- No tested context restored the earlier 120-130+ t/s target; LM Studio/runtime/driver state may still be degraded.")
    lines.extend(
        [
            "",
            "## Recommended No-Reboot Recovery Steps",
            "",
            "1. Run `D:\\StoryDriver\\scripts\\comfyui_hard_sleep.bat` while ComfyUI is idle.",
            "2. Run `D:\\StoryDriver\\scripts\\recover_gemma_speed_no_reboot.bat --confirm`.",
            "3. If the context table shows a lower context reaches 100+ t/s, rerun with `--keep-best-context` to keep that runtime loaded for fast writing.",
            "4. If no context recovers, run the manual ComfyUI-closed isolation script only when you are ready to temporarily stop and restart ComfyUI.",
            "5. If the manual ComfyUI-closed test still stays slow, the remaining issue is likely LM Studio/AMD runtime state rather than ComfyUI-resident models.",
            "",
            "## Daily Workflow",
            "",
            "- Writing mode: keep only Gemma loaded, parallel 1, no-thinking template, and use the lowest context that preserves enough continuity for the session.",
            "- Image mode: let Auto Image Priority unload LM, generate one image, keep ComfyUI warm for the regenerate window, then hard-sleep ComfyUI before writing.",
            "- If Task Manager shows shared GPU memory climbing during writing, hard-sleep ComfyUI and reload Gemma before continuing.",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_preflight_report(run: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PREFLIGHT_JSON_PATH.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    before = run.get("before") or {}
    before_gemma = before.get("gemma") or {}
    before_gpu = adapter_summary(before.get("gpu_memory") or {})
    before_speed = direct_result(((before.get("speed") or {}).get("latest") or {}))
    pre = run.get("preflight") or {}
    lines = [
        "# VRAM Speed Recovery Preflight",
        "",
        f"Generated: {run.get('generated_at')}",
        "",
        "- Diagnose-only mode. No LM Studio model was unloaded or reloaded.",
        f"- Preflight OK: {pre.get('ok')}",
        f"- Blockers: {', '.join(pre.get('blockers') or []) or 'none'}",
        f"- Gemma context: {before_gemma.get('contextLength', 'unknown')}",
        f"- Gemma parallel: {before_gemma.get('parallel', 'unknown')}",
        f"- Direct minimal speed: {before_speed.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
        f"- Hidden reasoning chars: {before_speed.get('reasoning_chars', 'not measured')}",
        f"- Windows GPU dedicated usage: {bytes_label(before_gpu.get('dedicated_usage_bytes'))}",
        f"- Windows GPU shared usage: {bytes_label(before_gpu.get('shared_usage_bytes'))}",
        "",
        f"Full recovery report stays at `{REPORT_PATH}`.",
    ]
    PREFLIGHT_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover Gemma speed without rebooting the PC.")
    parser.add_argument("--diagnose-only", action="store_true", help="Collect state and write the root-cause report without reloading.")
    parser.add_argument("--confirm", action="store_true", help="Run hard sleep and Gemma unload/reload tests.")
    parser.add_argument("--force", action="store_true", help="Ignore active-job blockers.")
    parser.add_argument("--contexts", default=",".join(str(item) for item in DEFAULT_CONTEXTS))
    parser.add_argument("--target-tps", type=float, default=100.0)
    parser.add_argument("--keep-best-context", action="store_true", help="Leave the fastest tested context loaded instead of restoring 50749.")
    args = parser.parse_args()
    contexts = [safe_int(item.strip()) for item in args.contexts.split(",") if safe_int(item.strip()) > 0]
    if not contexts:
        contexts = DEFAULT_CONTEXTS

    before = collect_vram_report(include_speed=True, label="vram_recovery_before")
    pre = preflight(force=args.force)
    run: dict[str, Any] = {
        "generated_at": utc_now(),
        "diagnose_only": args.diagnose_only,
        "confirmed": args.confirm,
        "preflight": pre,
        "before": before,
        "context_results": [],
        "best_context": None,
        "kept_best_context_loaded": False,
    }
    if args.diagnose_only or not args.confirm:
        run["note"] = "Diagnose-only mode. No reload was performed."
        instances, _ = lms_ps_json()
        run["final_lms_instances"] = instances
        write_preflight_report(run)
        print(json.dumps({"report": str(PREFLIGHT_REPORT_PATH), "ran_recovery": False, "preflight_ok": pre.get("ok")}, indent=2))
        return 0
    if not pre.get("ok"):
        run["error"] = "Preflight blocked recovery: " + "; ".join(pre.get("blockers") or [])
        instances, _ = lms_ps_json()
        run["final_lms_instances"] = instances
        write_report(run)
        print(json.dumps({"report": str(REPORT_PATH), "ran_recovery": False, "error": run["error"]}, indent=2))
        return 2
    help_result = load_help_flags()
    run["lms_load_help"] = help_result
    if not help_result.get("all_required"):
        run["error"] = "lms load does not expose every required safe flag."
        instances, _ = lms_ps_json()
        run["final_lms_instances"] = instances
        write_report(run)
        print(json.dumps({"report": str(REPORT_PATH), "ran_recovery": False, "error": run["error"]}, indent=2))
        return 2
    hard_sleep = run_hard_sleep(label="vram_recovery_hard_sleep", passes=2, measure_before=True, measure_after=True)
    run["hard_sleep"] = hard_sleep
    for context in contexts:
        result = test_context(context, label_prefix="vram_recovery_context")
        run["context_results"].append(result)
        if result.get("direct_minimal_tps") is not None and float(result["direct_minimal_tps"]) >= args.target_tps:
            break
    best = best_context(run["context_results"])
    run["best_context"] = best
    final_target = contexts[0]
    if args.keep_best_context and best and best.get("context_length"):
        final_target = int(best["context_length"])
        run["kept_best_context_loaded"] = True
    current_instances, _ = lms_ps_json()
    current_gemma = select_gemma(current_instances)
    if current_gemma and safe_int(current_gemma.get("contextLength")) != final_target:
        run["final_reload"] = test_context(final_target, label_prefix="vram_recovery_final")
    final_instances, _ = lms_ps_json()
    run["final_lms_instances"] = final_instances
    write_report(run)
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH),
                "ran_recovery": True,
                "best_context": (best or {}).get("context_length"),
                "best_direct_tps": (best or {}).get("direct_minimal_tps"),
                "kept_best_context_loaded": run.get("kept_best_context_loaded"),
                "final_context": (select_gemma(final_instances) or {}).get("contextLength"),
                "final_parallel": (select_gemma(final_instances) or {}).get("parallel"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
