from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "SPEED_RECOVERY_WORKFLOW_REPORT.md"
LATEST_JSON_PATH = LOG_DIR / "SPEED_RECOVERY_WORKFLOW_LATEST.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(args: list[str], *, timeout: float = 900) -> dict[str, Any]:
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


def batch(name: str) -> str:
    return str(ROOT / "scripts" / name)


def load_latest_verify() -> dict[str, Any]:
    path = LOG_DIR / "VERIFY_GEMMA_FAST_RUNTIME_LATEST.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def verify_status(run: dict[str, Any]) -> str:
    return str(run.get("status") or "").upper()


def should_reload_gemma(verify_run: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    gemma = verify_run.get("gemma") or {}
    direct = verify_run.get("direct_minimal") or {}
    instances = verify_run.get("instances") or []
    try:
        parallel = int(gemma.get("parallel")) if gemma.get("parallel") is not None else None
    except (TypeError, ValueError):
        parallel = None
    try:
        hidden = int(direct.get("reasoning_chars")) if direct.get("reasoning_chars") is not None else None
    except (TypeError, ValueError):
        hidden = None
    if not gemma:
        reasons.append("Gemma is not loaded.")
    if len(instances) != 1:
        reasons.append(f"Loaded model count is {len(instances)}.")
    if parallel is not None and parallel != 1:
        reasons.append(f"Gemma parallel is {parallel}.")
    if hidden is not None and hidden > 20:
        reasons.append(f"Hidden reasoning chars are {hidden}.")
    return bool(reasons), reasons


def write_report(run: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    before = run.get("verify_before") or {}
    after = run.get("verify_after") or {}
    lines = [
        "# StoryDriver Speed Recovery Workflow",
        "",
        f"Generated: {run.get('generated_at')}",
        f"- Diagnose only: {run.get('diagnose_only')}",
        f"- Confirmed recovery: {run.get('confirmed')}",
        f"- Reload Gemma if slow: {run.get('reload_if_slow')}",
        "",
        "## Before",
        "",
        f"- Status: {verify_status(before) or 'not run'}",
        f"- Direct visible t/s: {(before.get('direct_minimal') or {}).get('visible_tokens_per_second_estimated', 'not measured')}",
        f"- Hidden reasoning chars: {(before.get('direct_minimal') or {}).get('reasoning_chars', 'not measured')}",
        f"- Gemma parallel: {(before.get('gemma') or {}).get('parallel', 'unknown')}",
        f"- Loaded model count: {len(before.get('instances') or [])}",
        "",
        "## Actions",
        "",
    ]
    if run.get("diagnose_only"):
        lines.append("- Diagnose-only mode. No duplicate cleanup, hard sleep, or reload was applied.")
    else:
        for key, label in (
            ("cleanup_duplicates", "Duplicate ComfyUI cleanup"),
            ("hard_sleep", "ComfyUI hard sleep"),
            ("reload_gemma", "Gemma reload"),
        ):
            action = run.get(key) or {}
            if not action:
                lines.append(f"- {label}: not run")
            else:
                lines.append(
                    f"- {label}: {'ok' if action.get('ok') else 'failed/skipped'}"
                    f" (return {action.get('returncode')}, {action.get('stderr') or action.get('stdout') or 'no output'})"
                )
    lines.extend(
        [
            "",
            "## After",
            "",
            f"- Status: {verify_status(after) or 'not run'}",
            f"- Direct visible t/s: {(after.get('direct_minimal') or {}).get('visible_tokens_per_second_estimated', 'not measured')}",
            f"- Hidden reasoning chars: {(after.get('direct_minimal') or {}).get('reasoning_chars', 'not measured')}",
            f"- Gemma parallel: {(after.get('gemma') or {}).get('parallel', 'unknown')}",
            f"- Loaded model count: {len(after.get('instances') or [])}",
            "",
            "## Recommended Next Step",
            "",
        ]
    )
    if verify_status(after) == "PASS":
        lines.append("- StoryDriver speed guardrails pass. Continue writing.")
    elif verify_status(after) == "WARN":
        lines.append("- Usable but below target. Keep Writing Speed Protection on warn-only/auto and consider the manual ComfyUI-closed isolation test if you need 120+ t/s.")
    elif run.get("diagnose_only"):
        lines.append(r"- If the diagnosis is safe, run `D:\StoryDriver\scripts\recover_storydriver_speed.bat --confirm`.")
    else:
        lines.append(r"- Run `D:\StoryDriver\scripts\manual_comfyui_closed_speed_test.bat --confirm-close-comfyui` only when you are ready to temporarily isolate ComfyUI.")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the safe no-reboot StoryDriver speed recovery workflow.")
    parser.add_argument("--diagnose-only", action="store_true", help="Only verify current state. No cleanup or reload.")
    parser.add_argument("--confirm", action="store_true", help="Allow safe duplicate cleanup, ComfyUI hard sleep, and Gemma reload if runtime is bad.")
    parser.add_argument("--reload-if-slow", action="store_true", help="Also reload Gemma if speed remains low despite parallel/template being correct.")
    parser.add_argument("--skip-speed-test", action="store_true")
    args = parser.parse_args()

    verify_args = ["cmd", "/c", batch("verify_gemma_fast_runtime.bat"), "--label", "speed_recovery_verify_before"]
    if args.skip_speed_test:
        verify_args.append("--skip-speed-test")
    verify_before_cmd = run_command(verify_args, timeout=1200)
    verify_before = load_latest_verify()
    run: dict[str, Any] = {
        "generated_at": utc_now(),
        "diagnose_only": args.diagnose_only or not args.confirm,
        "confirmed": args.confirm,
        "reload_if_slow": args.reload_if_slow,
        "verify_before_command": verify_before_cmd,
        "verify_before": verify_before,
    }
    if args.diagnose_only or not args.confirm:
        run["verify_after"] = verify_before
        write_report(run)
        print(json.dumps({"report": str(REPORT_PATH), "ran_recovery": False, "status": verify_status(verify_before)}, indent=2))
        return 0 if verify_status(verify_before) != "FAIL" else 1

    run["cleanup_duplicates"] = run_command(["cmd", "/c", batch("cleanup_duplicate_comfyui.bat"), "--apply"], timeout=180)
    run["hard_sleep"] = run_command(
        ["cmd", "/c", batch("comfyui_hard_sleep.bat"), "--label", "speed_recovery_hard_sleep", "--skip-before-speed"],
        timeout=1200,
    )
    verify_mid_cmd = run_command(["cmd", "/c", batch("verify_gemma_fast_runtime.bat"), "--label", "speed_recovery_verify_after_hard_sleep"], timeout=1200)
    verify_mid = load_latest_verify()
    run["verify_after_hard_sleep_command"] = verify_mid_cmd
    run["verify_after_hard_sleep"] = verify_mid
    reload_needed, reload_reasons = should_reload_gemma(verify_mid)
    if args.reload_if_slow and verify_status(verify_mid) in {"WARN", "FAIL"}:
        reload_needed = True
        reload_reasons.append("Reload requested because speed remains below target.")
    run["reload_reasons"] = reload_reasons
    if reload_needed:
        run["reload_gemma"] = run_command(["cmd", "/c", batch("reload_gemma_fast_parallel1.bat"), "--confirm"], timeout=1200)
    else:
        run["reload_gemma"] = {"ok": True, "returncode": 0, "stdout": "Skipped: Gemma runtime shape did not require reload.", "stderr": ""}

    verify_after_cmd = run_command(["cmd", "/c", batch("verify_gemma_fast_runtime.bat"), "--label", "speed_recovery_verify_after"], timeout=1200)
    verify_after = load_latest_verify()
    run["verify_after_command"] = verify_after_cmd
    run["verify_after"] = verify_after
    write_report(run)
    print(json.dumps({"report": str(REPORT_PATH), "ran_recovery": True, "status": verify_status(verify_after)}, indent=2))
    return 0 if verify_status(verify_after) != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
