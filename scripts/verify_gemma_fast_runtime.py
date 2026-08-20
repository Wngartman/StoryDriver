from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "VERIFY_GEMMA_FAST_RUNTIME_REPORT.md"
LATEST_JSON_PATH = LOG_DIR / "VERIFY_GEMMA_FAST_RUNTIME_LATEST.json"
LATEST_SPEED_PATH = LOG_DIR / "LMSTUDIO_CURRENT_SPEED_TEST_LATEST.json"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
LMS_EXE = Path.home() / ".lmstudio" / "bin" / "lms.exe"

sys.path.insert(0, str(ROOT / "scripts"))

from system_speed_diagnostics import collect_report, summarize_process  # noqa: E402
from vram_speed_diagnostics import bytes_label, collect_gpu_memory_counters, request_json  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(args: list[str], *, timeout: float = 300) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "command": args,
        }
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(error), "command": args}


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def lms_ps_json() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not LMS_EXE.exists():
        return [], {"ok": False, "stderr": f"lms.exe not found at {LMS_EXE}"}
    result = run_command([str(LMS_EXE), "ps", "--json"], timeout=30)
    if not result["ok"]:
        return [], result
    try:
        parsed = json.loads(result["stdout"] or "[]")
    except json.JSONDecodeError as error:
        result["ok"] = False
        result["stderr"] = f"Could not parse lms ps --json: {error}"
        return [], result
    return [item for item in parsed if isinstance(item, dict)], result


def select_gemma(instances: list[dict[str, Any]], model: str = GEMMA_MODEL) -> dict[str, Any] | None:
    target = model.lower()
    for item in instances:
        values = [
            item.get("identifier"),
            item.get("modelKey"),
            item.get("displayName"),
            item.get("path"),
            item.get("indexedModelIdentifier"),
        ]
        if any(target in str(value or "").lower() for value in values):
            return item
    return None


def run_speed_test(label: str, *, skip_storydriver: bool = True) -> dict[str, Any]:
    batch = ROOT / "scripts" / "lmstudio_current_speed_test.bat"
    args = ["cmd", "/c", str(batch), "--label", label]
    if skip_storydriver:
        args.append("--skip-storydriver")
    result = run_command(args, timeout=900)
    latest: dict[str, Any] = {}
    if LATEST_SPEED_PATH.exists():
        try:
            latest = json.loads(LATEST_SPEED_PATH.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            latest = {}
    result["latest_speed_json"] = latest
    return result


def direct_minimal_result(latest: dict[str, Any]) -> dict[str, Any]:
    for item in latest.get("direct_results") or []:
        if item.get("label") == "direct_minimal_current":
            return item
    return {}


def queue_busy(queue: Any) -> bool:
    if not isinstance(queue, dict):
        return False
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return bool((isinstance(running, list) and running) or (isinstance(pending, list) and pending))


def highest_severity(issues: list[dict[str, str]]) -> str:
    if any(item["severity"] == "FAIL" for item in issues):
        return "FAIL"
    if any(item["severity"] == "WARN" for item in issues):
        return "WARN"
    return "PASS"


def add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def evaluate(run: dict[str, Any], *, warn_tps: float, fail_tps: float, expected_tps: float) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    instances = run.get("instances") or []
    gemma = run.get("gemma") or {}
    minimal = run.get("direct_minimal") or {}
    classified = ((run.get("system_report") or {}).get("classified_processes") or {})
    duplicate_candidates = classified.get("duplicate_comfyui_candidates") or []
    queue = run.get("comfyui_queue")
    gpu_summary = ((run.get("gpu_memory") or {}).get("adapter_summary") or {})
    direct_tps = safe_float(minimal.get("visible_tokens_per_second_estimated"))
    hidden_reasoning = safe_int(minimal.get("reasoning_chars"))
    parallel = safe_int(gemma.get("parallel"))

    if not run.get("lms_ps_result", {}).get("ok"):
        add_issue(issues, "FAIL", f"lms ps failed: {run.get('lms_ps_result', {}).get('stderr') or 'unknown error'}")
    if not gemma:
        add_issue(issues, "FAIL", "Gemma is not loaded.")
    if len(instances) != 1:
        add_issue(issues, "FAIL" if len(instances) > 1 else "WARN", f"Loaded LM Studio model count is {len(instances)}; fastest writing expects only Gemma.")
    if parallel is None:
        add_issue(issues, "WARN", "LM Studio parallel/concurrency was not visible.")
    elif parallel != 1:
        add_issue(issues, "FAIL", f"Gemma is loaded with parallel={parallel}; fastest single-scene writing needs parallel=1.")
    if hidden_reasoning is None:
        add_issue(issues, "WARN", "Hidden reasoning count was not measured.")
    elif hidden_reasoning > 20:
        add_issue(issues, "FAIL", f"Hidden reasoning returned ({hidden_reasoning} chars); the no-thinking template may not be active.")
    if direct_tps is None:
        add_issue(issues, "WARN", "Direct minimal API speed was not measured.")
    elif direct_tps < fail_tps:
        add_issue(issues, "FAIL", f"Direct LM Studio speed is collapsed at {direct_tps:.1f} t/s.")
    elif direct_tps < warn_tps:
        add_issue(issues, "WARN", f"Direct LM Studio speed is low at {direct_tps:.1f} t/s.")
    elif direct_tps < expected_tps:
        add_issue(issues, "WARN", f"Direct LM Studio speed is usable but below the {expected_tps:.0f}+ t/s target ({direct_tps:.1f} t/s).")
    if run.get("comfyui_queue_error"):
        add_issue(issues, "WARN", f"ComfyUI queue could not be checked: {run['comfyui_queue_error']}")
    elif queue_busy(queue):
        add_issue(issues, "WARN", "ComfyUI queue is busy; do not hard-sleep/free while an image is running.")
    if duplicate_candidates:
        add_issue(issues, "WARN", f"{len(duplicate_candidates)} duplicate/stale ComfyUI process candidate(s) were detected.")
    shared = safe_float(gpu_summary.get("shared_usage_bytes"))
    speed_below_target = direct_tps is None or direct_tps < expected_tps
    if shared and shared > 1024**3 and speed_below_target:
        add_issue(issues, "WARN", f"Windows GPU shared-memory use is elevated ({bytes_label(shared)}).")
    if not issues:
        add_issue(issues, "PASS", "Gemma fast runtime guardrails passed.")
    return issues


def recovery_steps() -> list[str]:
    return [
        r"1. Run `D:\StoryDriver\scripts\comfyui_hard_sleep.bat` while ComfyUI is idle.",
        r"2. Run `D:\StoryDriver\scripts\cleanup_duplicate_comfyui.bat` and use `--apply` only if the candidates are clearly stale duplicates.",
        r"3. Run `D:\StoryDriver\scripts\reload_gemma_fast_parallel1.bat --confirm` if parallel is above 1 or hidden reasoning returns.",
        r"4. Run `D:\StoryDriver\scripts\recover_storydriver_speed.bat --confirm` for the combined no-reboot recovery workflow.",
        r"5. If speed is still low, run `D:\StoryDriver\scripts\manual_comfyui_closed_speed_test.bat --confirm-close-comfyui` when you are ready to temporarily isolate ComfyUI.",
        "6. If that still fails, restart the local AI stack or PC as the final recovery step.",
    ]


def write_report(run: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    gemma = run.get("gemma") or {}
    minimal = run.get("direct_minimal") or {}
    issues = run.get("issues") or []
    classified = ((run.get("system_report") or {}).get("classified_processes") or {})
    gpu_summary = ((run.get("gpu_memory") or {}).get("adapter_summary") or {})
    lines = [
        "# Verify Gemma Fast Runtime",
        "",
        f"Generated: {run.get('generated_at')}",
        f"Status: **{run.get('status')}**",
        "",
        "## Runtime",
        "",
        f"- Loaded LM instances: {len(run.get('instances') or [])}",
        f"- Gemma loaded: {'yes' if gemma else 'no'}",
        f"- Model: `{gemma.get('modelKey') or gemma.get('identifier') or 'not loaded'}`",
        f"- Identifier: `{gemma.get('identifier') or 'not loaded'}`",
        f"- Context length: {gemma.get('contextLength', 'unknown')}",
        f"- Parallel/concurrency: {gemma.get('parallel', 'unknown')}",
        f"- Status: {gemma.get('status', 'unknown')}",
        "",
        "## Speed",
        "",
        f"- Direct minimal visible speed: {minimal.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
        f"- First visible prose: {minimal.get('first_content_seconds', 'not measured')}s",
        f"- Hidden reasoning chars: {minimal.get('reasoning_chars', 'not measured')}",
        "",
        "## ComfyUI / GPU",
        "",
        f"- ComfyUI queue busy: {queue_busy(run.get('comfyui_queue'))}",
        f"- Duplicate/stale ComfyUI candidates: {len(classified.get('duplicate_comfyui_candidates') or [])}",
        f"- GPU dedicated usage: {bytes_label(gpu_summary.get('dedicated_usage_bytes'))}",
        f"- GPU shared usage: {bytes_label(gpu_summary.get('shared_usage_bytes'))}",
        "",
        "## Findings",
        "",
    ]
    for item in issues:
        lines.append(f"- {item.get('severity')}: {item.get('message')}")
    lines.extend(["", "## Recovery Steps", "", *recovery_steps()])
    if classified.get("duplicate_comfyui_candidates"):
        lines.extend(["", "## Duplicate Candidate Preview", ""])
        lines.extend(summarize_process(item) for item in classified.get("duplicate_comfyui_candidates")[:6])
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Gemma is loaded with fast single-stream runtime settings.")
    parser.add_argument("--label", default="verify_gemma_fast_runtime")
    parser.add_argument("--skip-speed-test", action="store_true")
    parser.add_argument("--include-storydriver", action="store_true")
    parser.add_argument("--expected-tps", type=float, default=100.0)
    parser.add_argument("--warn-tps", type=float, default=70.0)
    parser.add_argument("--fail-tps", type=float, default=40.0)
    args = parser.parse_args()

    instances, ps_result = lms_ps_json()
    speed_result = None
    if not args.skip_speed_test:
        speed_result = run_speed_test(args.label, skip_storydriver=not args.include_storydriver)
    latest_speed = (speed_result or {}).get("latest_speed_json") or {}
    system_report = collect_report(free_impact=False)
    env = {}
    comfy_url = "http://localhost:8188"
    try:
        for path in (ROOT / ".env", ROOT / "backend" / ".env"):
            if path.exists():
                for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if raw.strip().startswith("COMFYUI_BASE_URL="):
                        comfy_url = raw.split("=", 1)[1].strip().strip('"').strip("'")
        env["comfyui_base_url"] = comfy_url
    except OSError:
        pass
    comfyui_queue, comfyui_queue_error = request_json(f"{comfy_url.rstrip('/')}/queue", timeout=10)
    gpu_memory = collect_gpu_memory_counters()
    run: dict[str, Any] = {
        "generated_at": utc_now(),
        "label": args.label,
        "instances": instances,
        "gemma": select_gemma(instances),
        "lms_ps_result": ps_result,
        "speed_result": speed_result,
        "direct_minimal": direct_minimal_result(latest_speed),
        "system_report": system_report,
        "comfyui_queue": comfyui_queue,
        "comfyui_queue_error": comfyui_queue_error,
        "gpu_memory": gpu_memory,
        "env": env,
    }
    issues = evaluate(run, warn_tps=args.warn_tps, fail_tps=args.fail_tps, expected_tps=args.expected_tps)
    run["issues"] = issues
    run["status"] = highest_severity(issues)
    write_report(run)
    print(f"{run['status']}: see {REPORT_PATH}")
    print(
        json.dumps(
            {
                "status": run["status"],
                "report": str(REPORT_PATH),
                "gemma_loaded": bool(run["gemma"]),
                "loaded_lm_instances": len(instances),
                "parallel": (run["gemma"] or {}).get("parallel"),
                "context_length": (run["gemma"] or {}).get("contextLength"),
                "direct_minimal_visible_tps": run["direct_minimal"].get("visible_tokens_per_second_estimated"),
                "hidden_reasoning_chars": run["direct_minimal"].get("reasoning_chars"),
                "duplicate_comfyui_candidates": len(((system_report.get("classified_processes") or {}).get("duplicate_comfyui_candidates") or [])),
            },
            indent=2,
        )
    )
    return 1 if run["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
