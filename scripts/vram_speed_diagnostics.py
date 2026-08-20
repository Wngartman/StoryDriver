from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "VRAM_SPEED_DIAGNOSTICS_REPORT.md"
LATEST_JSON_PATH = LOG_DIR / "VRAM_SPEED_DIAGNOSTICS_LATEST.json"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
LMS_EXE = Path.home() / ".lmstudio" / "bin" / "lms.exe"

import sys

sys.path.insert(0, str(ROOT / "scripts"))

from system_speed_diagnostics import collect_report, run_command as system_run_command  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(args: list[str], *, timeout: float = 120) -> dict[str, Any]:
    started = time.perf_counter()
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
            "duration_seconds": round(time.perf_counter() - started, 3),
            "command": args,
        }
    except Exception as error:  # noqa: BLE001
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(error),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "command": args,
        }


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 12) -> tuple[Any | None, str | None]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8", errors="replace")) if raw else None, None
    except Exception as error:  # noqa: BLE001
        return None, str(error)


def load_env() -> dict[str, str]:
    data = dict(os.environ)
    for path in (ROOT / ".env", ROOT / "backend" / ".env"):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return data


def parse_typeperf(stdout: str) -> dict[str, float]:
    csv_lines = [line for line in stdout.splitlines() if line.startswith('"')]
    if len(csv_lines) < 2:
        return {}
    try:
        rows = list(csv.reader(StringIO("\n".join(csv_lines))))
    except csv.Error:
        return {}
    if len(rows) < 2:
        return {}
    headers = rows[0][1:]
    values = rows[1][1:]
    result: dict[str, float] = {}
    for header, raw in zip(headers, values):
        try:
            result[header] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def collect_gpu_memory_counters() -> dict[str, Any]:
    adapter = run_command(
        [
            "typeperf",
            r"\GPU Adapter Memory(*)\Dedicated Usage",
            r"\GPU Adapter Memory(*)\Shared Usage",
            "-sc",
            "1",
        ],
        timeout=30,
    )
    process = run_command(
        [
            "typeperf",
            r"\GPU Process Memory(*)\Dedicated Usage",
            r"\GPU Process Memory(*)\Shared Usage",
            "-sc",
            "1",
        ],
        timeout=30,
    )
    adapter_values = parse_typeperf(adapter.get("stdout") or "")
    process_values = parse_typeperf(process.get("stdout") or "")
    adapter_summary = {
        "dedicated_usage_bytes": sum(value for key, value in adapter_values.items() if "Dedicated Usage" in key),
        "shared_usage_bytes": sum(value for key, value in adapter_values.items() if "Shared Usage" in key),
    }
    process_by_pid: dict[str, dict[str, float]] = {}
    for key, value in process_values.items():
        match = re.search(r"pid_(\d+)_", key)
        if not match:
            continue
        pid = match.group(1)
        item = process_by_pid.setdefault(pid, {"dedicated_usage_bytes": 0.0, "shared_usage_bytes": 0.0})
        if "Dedicated Usage" in key:
            item["dedicated_usage_bytes"] += value
        elif "Shared Usage" in key:
            item["shared_usage_bytes"] += value
    top_processes = sorted(
        (
            {"pid": int(pid), **values, "total_usage_bytes": values["dedicated_usage_bytes"] + values["shared_usage_bytes"]}
            for pid, values in process_by_pid.items()
        ),
        key=lambda item: item["total_usage_bytes"],
        reverse=True,
    )[:20]
    return {
        "adapter_counter": adapter,
        "process_counter": process,
        "adapter_values": adapter_values,
        "process_values_count": len(process_values),
        "adapter_summary": adapter_summary,
        "process_by_pid": process_by_pid,
        "top_gpu_processes": top_processes,
        "available": adapter.get("ok") and bool(adapter_values),
    }


def bytes_label(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    amount = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB"):
        if abs(amount) < 1024 or suffix == "GiB":
            return f"{amount:.2f} {suffix}" if suffix != "B" else f"{int(amount)} B"
        amount /= 1024
    return "unknown"


def lms_ps_json() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not LMS_EXE.exists():
        return [], {"ok": False, "stderr": f"lms.exe not found at {LMS_EXE}"}
    result = run_command([str(LMS_EXE), "ps", "--json"], timeout=30)
    if not result.get("ok"):
        return [], result
    try:
        parsed = json.loads(result.get("stdout") or "[]")
    except json.JSONDecodeError as error:
        result["ok"] = False
        result["stderr"] = f"Could not parse lms ps --json: {error}"
        return [], result
    return [item for item in parsed if isinstance(item, dict)], result


def select_gemma(instances: list[dict[str, Any]]) -> dict[str, Any] | None:
    target = GEMMA_MODEL.lower()
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
    args = ["cmd", "/c", str(ROOT / "scripts" / "lmstudio_current_speed_test.bat"), "--label", label]
    if skip_storydriver:
        args.append("--skip-storydriver")
    result = run_command(args, timeout=900)
    latest_path = LOG_DIR / "LMSTUDIO_CURRENT_SPEED_TEST_LATEST.json"
    latest: dict[str, Any] = {}
    if latest_path.exists():
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            latest = {}
    result["latest"] = latest
    return result


def direct_result(latest: dict[str, Any], label: str = "direct_minimal_current") -> dict[str, Any]:
    for item in latest.get("direct_results") or []:
        if item.get("label") == label:
            return item
    return {}


def collect_vram_report(*, include_speed: bool = False, label: str = "vram_speed_diagnostics") -> dict[str, Any]:
    env = load_env()
    system_report = collect_report(free_impact=False)
    gpu_memory = collect_gpu_memory_counters()
    lms_instances, lms_result = lms_ps_json()
    gemma = select_gemma(lms_instances)
    speed = run_speed_test(label, skip_storydriver=True) if include_speed else None
    comfy_url = env.get("COMFYUI_BASE_URL", "http://localhost:8188").rstrip("/")
    comfy_stats, comfy_stats_error = request_json(f"{comfy_url}/system_stats", timeout=12)
    comfy_queue, comfy_queue_error = request_json(f"{comfy_url}/queue", timeout=12)
    return {
        "generated_at": utc_now(),
        "label": label,
        "system": system_report,
        "gpu_memory": gpu_memory,
        "lms_ps_json": {"instances": lms_instances, "command": lms_result},
        "gemma": gemma,
        "speed": speed,
        "comfyui": {
            "base_url": comfy_url,
            "system_stats": comfy_stats,
            "system_stats_error": comfy_stats_error,
            "queue": comfy_queue,
            "queue_error": comfy_queue_error,
        },
    }


def process_name_map(system_report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows = ((system_report.get("process_collection") or {}).get("processes") or [])
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            result[int(row.get("pid"))] = row
        except (TypeError, ValueError):
            continue
    return result


def write_report(report: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    system_report = report.get("system") or {}
    classified = system_report.get("classified_processes") or {}
    gpu = report.get("gpu_memory") or {}
    adapter_summary = gpu.get("adapter_summary") or {}
    gemma = report.get("gemma") or {}
    speed_latest = ((report.get("speed") or {}).get("latest") or {})
    minimal = direct_result(speed_latest, "direct_minimal_current")
    story_like = direct_result(speed_latest, "direct_storydriver_like_current")
    process_map = process_name_map(system_report)
    lines = [
        "# VRAM Speed Diagnostics Report",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "## Current LM Studio Runtime",
        "",
        f"- Gemma loaded: {'yes' if gemma else 'no'}",
        f"- Model: `{gemma.get('modelKey') or 'not loaded'}`",
        f"- Identifier: `{gemma.get('identifier') or 'not loaded'}`",
        f"- Context length: {gemma.get('contextLength') if gemma else 'unknown'}",
        f"- Parallel/concurrency: {gemma.get('parallel') if gemma else 'unknown'}",
        f"- Status: {gemma.get('status') if gemma else 'unknown'}",
        "",
        "## Direct Speed",
        "",
        f"- Direct minimal t/s: {minimal.get('visible_tokens_per_second_estimated', 'not measured')}",
        f"- Direct StoryDriver-like t/s: {story_like.get('visible_tokens_per_second_estimated', 'not measured')}",
        f"- First visible prose: {minimal.get('first_content_seconds', 'not measured')}s",
        f"- Hidden reasoning chars: {minimal.get('reasoning_chars', 'not measured')}",
        "",
        "## Windows GPU Memory Counters",
        "",
    ]
    if gpu.get("available"):
        lines.extend(
            [
                f"- Adapter dedicated usage: {bytes_label(adapter_summary.get('dedicated_usage_bytes'))}",
                f"- Adapter shared usage: {bytes_label(adapter_summary.get('shared_usage_bytes'))}",
                "",
                "Top GPU process-memory users:",
            ]
        )
        for item in gpu.get("top_gpu_processes") or []:
            process = process_map.get(int(item.get("pid") or 0), {})
            label = process.get("name") or "unknown"
            lines.append(
                f"- PID {item.get('pid')} `{label}` dedicated {bytes_label(item.get('dedicated_usage_bytes'))}, shared {bytes_label(item.get('shared_usage_bytes'))}"
            )
    else:
        lines.extend(
            [
                "- GPU Adapter Memory counters were not available through `typeperf`.",
                "- Exact shared-memory telemetry must be read manually from Task Manager for this run.",
            ]
        )
    lines.extend(
        [
            "",
            "## ComfyUI",
            "",
            f"- ComfyUI process count: {len(classified.get('comfyui') or [])}",
            f"- Active 8188 PID: {classified.get('active_comfyui_8188_pid') or 'not detected'}",
            f"- Duplicate/stale candidates: {len(classified.get('duplicate_comfyui_candidates') or [])}",
            f"- Queue running: {len((report.get('comfyui') or {}).get('queue', {}).get('queue_running') or []) if isinstance((report.get('comfyui') or {}).get('queue'), dict) else 'unknown'}",
            f"- Queue pending: {len((report.get('comfyui') or {}).get('queue', {}).get('queue_pending') or []) if isinstance((report.get('comfyui') or {}).get('queue'), dict) else 'unknown'}",
            "",
            "## Local AI Processes",
            "",
        ]
    )
    for process in classified.get("top_ai_memory") or []:
        command = str(process.get("command_line") or process.get("executable_path") or "")[:220]
        lines.append(f"- PID {process.get('pid')} `{process.get('name')}` {process.get('working_set_mb')} MB :: {command}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
        ]
    )
    if gpu.get("available") and float(adapter_summary.get("shared_usage_bytes") or 0) > 1024**3:
        lines.append("- Windows reports significant shared GPU memory usage. This supports the VRAM-spill/shared-memory-pressure theory.")
    if gemma and int(gemma.get("parallel") or 0) > 1:
        lines.append("- Gemma is not in the fastest single-stream runtime: parallel/concurrency is greater than 1.")
    if minimal.get("visible_tokens_per_second_estimated") and float(minimal["visible_tokens_per_second_estimated"]) < 70:
        lines.append("- Direct LM Studio API speed is below the warning threshold; the bottleneck is outside StoryDriver prompt building.")
    if not lines[-1].startswith("-"):
        lines.append("- No single bottleneck was proven in this diagnostic snapshot.")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect StoryDriver/LM Studio/ComfyUI VRAM and speed diagnostics.")
    parser.add_argument("--include-speed", action="store_true", help="Run direct LM Studio speed test too.")
    parser.add_argument("--label", default="vram_speed_diagnostics")
    args = parser.parse_args()
    report = collect_vram_report(include_speed=args.include_speed, label=args.label)
    write_report(report)
    minimal = direct_result(((report.get("speed") or {}).get("latest") or {}), "direct_minimal_current")
    gpu = report.get("gpu_memory") or {}
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH),
                "latest_json": str(LATEST_JSON_PATH),
                "gemma_parallel": (report.get("gemma") or {}).get("parallel"),
                "gemma_context": (report.get("gemma") or {}).get("contextLength"),
                "direct_minimal_tps": minimal.get("visible_tokens_per_second_estimated"),
                "gpu_counters_available": gpu.get("available"),
                "adapter_dedicated_usage_bytes": (gpu.get("adapter_summary") or {}).get("dedicated_usage_bytes"),
                "adapter_shared_usage_bytes": (gpu.get("adapter_summary") or {}).get("shared_usage_bytes"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
