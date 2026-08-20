from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "COMFYUI_HARD_SLEEP_REPORT.md"
LATEST_JSON_PATH = LOG_DIR / "COMFYUI_HARD_SLEEP_LATEST.json"

import sys

sys.path.insert(0, str(ROOT / "scripts"))

from vram_speed_diagnostics import (  # noqa: E402
    bytes_label,
    collect_gpu_memory_counters,
    direct_result,
    load_env,
    request_json,
    run_speed_test,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def queue_busy(queue: Any) -> bool:
    if not isinstance(queue, dict):
        return False
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return bool((isinstance(running, list) and running) or (isinstance(pending, list) and pending))


def queue_counts(queue: Any) -> tuple[Any, Any]:
    if not isinstance(queue, dict):
        return "unknown", "unknown"
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return (
        len(running) if isinstance(running, list) else "unknown",
        len(pending) if isinstance(pending, list) else "unknown",
    )


def gpu_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = snapshot.get("adapter_summary") or {}
    return {
        "available": snapshot.get("available"),
        "dedicated_usage_bytes": summary.get("dedicated_usage_bytes"),
        "shared_usage_bytes": summary.get("shared_usage_bytes"),
    }


def write_report(run: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    before_speed = direct_result(((run.get("speed_before") or {}).get("latest") or {}))
    after_speed = direct_result(((run.get("speed_after") or {}).get("latest") or {}))
    before_gpu = gpu_summary(run.get("gpu_before") or {})
    after_gpu = gpu_summary(run.get("gpu_after") or {})
    queue_before = run.get("queue_before")
    queue_after = run.get("queue_after")
    running_before, pending_before = queue_counts(queue_before)
    running_after, pending_after = queue_counts(queue_after)
    delta_tps = None
    if before_speed.get("visible_tokens_per_second_estimated") is not None and after_speed.get("visible_tokens_per_second_estimated") is not None:
        delta_tps = round(float(after_speed["visible_tokens_per_second_estimated"]) - float(before_speed["visible_tokens_per_second_estimated"]), 2)
    lines = [
        "# ComfyUI Hard Sleep Report",
        "",
        f"Generated: {run.get('generated_at')}",
        "",
        "## Action",
        "",
        "- Kept ComfyUI backend running.",
        "- Used supported ComfyUI `/free` payload: `{\"unload_models\": true, \"free_memory\": true}`.",
        f"- Free passes requested: {run.get('passes')}",
        f"- Free attempted: {run.get('free_attempted')}",
        f"- Free succeeded: {run.get('free_succeeded')}",
        f"- Error: {run.get('error') or 'none'}",
        "",
        "## Queue",
        "",
        f"- Before: running {running_before}, pending {pending_before}",
        f"- After: running {running_after}, pending {pending_after}",
        "",
        "## GPU Counters",
        "",
        f"- Before dedicated: {bytes_label(before_gpu.get('dedicated_usage_bytes'))}",
        f"- Before shared: {bytes_label(before_gpu.get('shared_usage_bytes'))}",
        f"- After dedicated: {bytes_label(after_gpu.get('dedicated_usage_bytes'))}",
        f"- After shared: {bytes_label(after_gpu.get('shared_usage_bytes'))}",
        "",
        "## LM Studio Speed",
        "",
        f"- Before hard sleep: {before_speed.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
        f"- After hard sleep: {after_speed.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
        f"- Delta: {delta_tps if delta_tps is not None else 'not measured'} est. tok/s",
        f"- Hidden reasoning after: {after_speed.get('reasoning_chars', 'not measured')}",
        "",
        "## Interpretation",
        "",
    ]
    if run.get("skipped"):
        lines.append(f"- Hard sleep skipped: {run.get('skip_reason')}")
    elif delta_tps is not None and delta_tps >= 15:
        lines.append("- ComfyUI hard sleep materially improved LM Studio speed in this run.")
    elif delta_tps is not None and abs(delta_tps) < 10:
        lines.append("- ComfyUI hard sleep did not materially improve LM Studio speed in this run.")
    else:
        lines.append("- Hard sleep ran, but speed impact could not be measured clearly.")
    if after_gpu.get("shared_usage_bytes") and float(after_gpu["shared_usage_bytes"]) > 1024**3:
        lines.append("- Shared GPU memory remained above 1 GiB after hard sleep, so memory pressure may still be present.")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_hard_sleep(*, label: str, passes: int = 2, measure_before: bool = True, measure_after: bool = True) -> dict[str, Any]:
    env = load_env()
    comfy_url = env.get("COMFYUI_BASE_URL", "http://localhost:8188").rstrip("/")
    queue_before, queue_error = request_json(f"{comfy_url}/queue", timeout=12)
    stats_before, stats_before_error = request_json(f"{comfy_url}/system_stats", timeout=12)
    gpu_before = collect_gpu_memory_counters()
    speed_before = run_speed_test(f"{label}_before_hard_sleep", skip_storydriver=True) if measure_before else None
    run: dict[str, Any] = {
        "generated_at": utc_now(),
        "label": label,
        "comfyui_url": comfy_url,
        "passes": passes,
        "queue_before": queue_before,
        "queue_before_error": queue_error,
        "stats_before": stats_before,
        "stats_before_error": stats_before_error,
        "gpu_before": gpu_before,
        "speed_before": speed_before,
        "free_attempted": False,
        "free_succeeded": False,
        "free_results": [],
        "skipped": False,
        "skip_reason": None,
        "error": None,
    }
    if queue_error:
        run.update({"skipped": True, "skip_reason": f"queue unavailable: {queue_error}"})
        return run
    if queue_busy(queue_before):
        run.update({"skipped": True, "skip_reason": "comfyui_queue_busy"})
        return run
    for index in range(max(1, passes)):
        payload, error = request_json(
            f"{comfy_url}/free",
            method="POST",
            payload={"unload_models": True, "free_memory": True},
            timeout=35,
        )
        run["free_attempted"] = True
        run["free_results"].append({"pass": index + 1, "ok": error is None, "error": error, "payload": payload})
        if error:
            run["error"] = error
            break
        time.sleep(5)
    time.sleep(8)
    queue_after, queue_after_error = request_json(f"{comfy_url}/queue", timeout=12)
    stats_after, stats_after_error = request_json(f"{comfy_url}/system_stats", timeout=12)
    gpu_after = collect_gpu_memory_counters()
    speed_after = run_speed_test(f"{label}_after_hard_sleep", skip_storydriver=True) if measure_after else None
    run.update(
        {
            "queue_after": queue_after,
            "queue_after_error": queue_after_error,
            "stats_after": stats_after,
            "stats_after_error": stats_after_error,
            "gpu_after": gpu_after,
            "speed_after": speed_after,
            "free_succeeded": bool(run["free_attempted"] and all(item.get("ok") for item in run["free_results"])),
        }
    )
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description="Put ComfyUI into a hard-sleep/free state without closing the backend.")
    parser.add_argument("--label", default="comfyui_hard_sleep")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--skip-before-speed", action="store_true")
    parser.add_argument("--skip-after-speed", action="store_true")
    args = parser.parse_args()
    run = run_hard_sleep(
        label=args.label,
        passes=args.passes,
        measure_before=not args.skip_before_speed,
        measure_after=not args.skip_after_speed,
    )
    write_report(run)
    after_speed = direct_result(((run.get("speed_after") or {}).get("latest") or {}))
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH),
                "latest_json": str(LATEST_JSON_PATH),
                "free_attempted": run.get("free_attempted"),
                "free_succeeded": run.get("free_succeeded"),
                "skipped": run.get("skipped"),
                "skip_reason": run.get("skip_reason"),
                "after_direct_minimal_tps": after_speed.get("visible_tokens_per_second_estimated"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
