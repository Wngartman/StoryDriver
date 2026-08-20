from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "DUPLICATE_COMFYUI_CLEANUP_REPORT.md"

import sys

sys.path.insert(0, str(ROOT / "scripts"))

from system_speed_diagnostics import collect_report, summarize_process  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stop_process(pid: int, *, force: bool = False) -> dict[str, Any]:
    args = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        args.append("/F")
    started = time.perf_counter()
    completed = subprocess.run(
        args,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return {
        "pid": pid,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "command": args,
    }


def write_report(
    *,
    report: dict[str, Any],
    after_report: dict[str, Any] | None,
    apply: bool,
    force: bool,
    stopped: list[dict[str, Any]],
) -> None:
    classified = report.get("classified_processes") or {}
    after_classified = (after_report or {}).get("classified_processes") or {}
    candidates = classified.get("duplicate_comfyui_candidates") or []
    ignored = classified.get("ignored_comfyui_related_processes") or []
    active_pid = classified.get("active_comfyui_pid") or classified.get("active_comfyui_8188_pid")
    active_port = classified.get("active_comfyui_port") or "unknown"
    lines = [
        "# Duplicate ComfyUI Cleanup Report",
        "",
        f"Generated: {utc_now()}",
        "",
        f"- Active ComfyUI PID preserved: {active_pid or 'not detected'} on port {active_port}",
        f"- ComfyUI backend processes: {len(classified.get('comfyui_backend_processes') or [])}",
        f"- ComfyUI launcher/helper processes: {len(classified.get('comfyui_launcher_processes') or [])}",
        f"- Duplicate/stale candidates: {len(candidates)}",
        f"- Ignored ComfyUI-related non-backend processes: {len(ignored)}",
        f"- Apply mode: {apply}",
        f"- Force mode: {force}",
        "",
        "## Candidates",
        "",
    ]
    if candidates:
        lines.extend(summarize_process(item) for item in candidates)
    else:
        lines.append("- None clearly identified.")
    if ignored:
        lines.extend(["", "## Ignored Related Processes", ""])
        for item in ignored[:12]:
            lines.append(summarize_process(item))
    lines.extend(["", "## Actions", ""])
    if not apply:
        lines.append("- Dry run only. No processes were stopped.")
    elif stopped:
        for item in stopped:
            lines.append(f"- PID {item['pid']}: {'stopped' if item['ok'] else 'failed'} ({item.get('stderr') or item.get('stdout') or 'no output'})")
    else:
        lines.append("- Apply requested, but there were no safe duplicate candidates.")
    if after_report is not None:
        lines.extend(
            [
                "",
                "## After Status",
                "",
                f"- Active ComfyUI PID: {(after_classified.get('active_comfyui_pid') or after_classified.get('active_comfyui_8188_pid')) or 'not detected'} on port {after_classified.get('active_comfyui_port') or 'unknown'}",
                f"- Duplicate/stale candidates: {len(after_classified.get('duplicate_comfyui_candidates') or [])}",
                f"- Backend processes: {len(after_classified.get('comfyui_backend_processes') or [])}",
            ]
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This script only targets real Python ComfyUI `main.py` backend processes that are not the active backend and are not an ancestor of it.",
            "- It does not stop the main active backend candidate.",
            "- Hidden PowerShell probes, AMD installer checks, launcher windows, and helper scripts are not safe duplicate candidates.",
            "- Default mode is dry-run.",
        ]
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or stop clearly duplicate ComfyUI processes.")
    parser.add_argument("--apply", action="store_true", help="Stop safe duplicate candidates. Default is dry-run.")
    parser.add_argument("--force", action="store_true", help="Use taskkill /F for stopped candidates.")
    args = parser.parse_args()
    report = collect_report(free_impact=False)
    candidates = (report.get("classified_processes") or {}).get("duplicate_comfyui_candidates") or []
    stopped: list[dict[str, Any]] = []
    if args.apply:
        for process in candidates:
            try:
                pid = int(process.get("pid"))
            except (TypeError, ValueError):
                continue
            stopped.append(stop_process(pid, force=args.force))
    after_report = collect_report(free_impact=False) if args.apply else None
    write_report(report=report, after_report=after_report, apply=args.apply, force=args.force, stopped=stopped)
    print(json.dumps({
        "report": str(REPORT_PATH),
        "dry_run": not args.apply,
        "duplicate_candidates": len(candidates),
        "ignored_related": len((report.get("classified_processes") or {}).get("ignored_comfyui_related_processes") or []),
        "stopped": stopped,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
