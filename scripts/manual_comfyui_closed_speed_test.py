from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "MANUAL_COMFYUI_CLOSED_SPEED_TEST.md"
STABLE_LAUNCHER = r"C:\Users\wngar\OneDrive\Desktop\Launch_ComfyUI_STABLE_FULL.bat"

import sys

sys.path.insert(0, str(ROOT / "scripts"))

from system_speed_diagnostics import collect_report, summarize_process  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_ok(url: str, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read(16)
        return True
    except Exception:
        return False


def write_report(lines: list[str]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manual isolation test that can temporarily stop ComfyUI, test LM speed, and restart ComfyUI."
    )
    parser.add_argument("--confirm-close-comfyui", action="store_true", help="Actually stop the active ComfyUI backend.")
    parser.add_argument("--force", action="store_true", help="Use taskkill /F when stopping ComfyUI.")
    args = parser.parse_args()
    before = collect_report(free_impact=False)
    classified = before.get("classified_processes") or {}
    active_pid = classified.get("active_comfyui_8188_pid")
    comfy_processes = classified.get("comfyui") or []
    lines = [
        "# Manual ComfyUI-Closed Speed Test",
        "",
        f"Generated: {utc_now()}",
        "",
        "This script is intentionally manual. It does not close ComfyUI unless `--confirm-close-comfyui` is passed.",
        "",
        f"- Stable restart launcher: `{STABLE_LAUNCHER}`",
        f"- Active 8188 PID: {active_pid or 'not detected'}",
        "",
        "## ComfyUI Processes Before",
        "",
    ]
    if comfy_processes:
        lines.extend(summarize_process(item) for item in comfy_processes)
    else:
        lines.append("- None detected.")
    if not args.confirm_close_comfyui:
        lines.extend(
            [
                "",
                "## Not Run",
                "",
                "No process was stopped. To run the isolation test manually:",
                "",
                "```bat",
                r"cd /d D:\StoryDriver",
                r"scripts\manual_comfyui_closed_speed_test.bat --confirm-close-comfyui",
                "```",
            ]
        )
        write_report(lines)
        print(json.dumps({"report": str(REPORT_PATH), "ran": False, "active_8188_pid": active_pid}, indent=2))
        return 0
    if not active_pid:
        lines.extend(["", "## Stopped", "", "- No active 8188 ComfyUI PID was detected, so nothing was stopped."])
        write_report(lines)
        print(json.dumps({"report": str(REPORT_PATH), "ran": False, "reason": "no_active_8188_pid"}, indent=2))
        return 0

    kill_args = ["taskkill", "/PID", str(active_pid), "/T"]
    if args.force:
        kill_args.append("/F")
    stopped = subprocess.run(kill_args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines.extend(
        [
            "",
            "## Stop Active ComfyUI",
            "",
            f"- Command: `{' '.join(kill_args)}`",
            f"- Return code: {stopped.returncode}",
            f"- stdout: {stopped.stdout.strip() or 'none'}",
            f"- stderr: {stopped.stderr.strip() or 'none'}",
            "",
            "## Speed Test With ComfyUI Closed",
            "",
        ]
    )
    speed = subprocess.run(
        [str(ROOT / "scripts" / "lmstudio_current_speed_test.bat"), "--label", "manual_comfyui_closed"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=600,
    )
    lines.extend(
        [
            f"- Return code: {speed.returncode}",
            "```text",
            (speed.stdout or speed.stderr or "no output")[-2000:],
            "```",
            "",
            "## Restart ComfyUI",
            "",
        ]
    )
    restarted = subprocess.Popen(
        ["cmd.exe", "/d", "/s", "/c", "start", "", STABLE_LAUNCHER],
        cwd=str(ROOT),
        shell=False,
    )
    reachable = False
    for _ in range(90):
        time.sleep(5)
        if request_ok("http://localhost:8188/system_stats", timeout=5):
            reachable = True
            break
    lines.extend(
        [
            f"- Launcher process started: PID {restarted.pid}",
            f"- ComfyUI reachable after restart wait: {reachable}",
        ]
    )
    write_report(lines)
    print(json.dumps({"report": str(REPORT_PATH), "ran": True, "comfyui_restarted_reachable": reachable}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
