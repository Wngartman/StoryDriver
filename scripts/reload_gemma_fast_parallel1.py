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
REPORT_PATH = LOG_DIR / "LM_RUNTIME_RECOVERY_REPORT.md"
LATEST_SPEED_PATH = LOG_DIR / "LMSTUDIO_CURRENT_SPEED_TEST_LATEST.json"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
LMS_EXE = Path.home() / ".lmstudio" / "bin" / "lms.exe"

sys.path.insert(0, str(ROOT / "scripts"))

from system_speed_diagnostics import collect_report  # noqa: E402
from verify_gemma_fast_runtime import (  # noqa: E402
    direct_minimal_result,
    lms_ps_json,
    run_command,
    run_speed_test,
    select_gemma,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def supported_load_flags(help_text: str) -> dict[str, bool]:
    return {
        "gpu": "--gpu" in help_text,
        "context_length": "--context-length" in help_text or "-c, --context-length" in help_text,
        "parallel": "--parallel" in help_text,
        "identifier": "--identifier" in help_text,
        "yes": "--yes" in help_text or "-y, --yes" in help_text,
        "estimate_only": "--estimate-only" in help_text,
    }


def current_context(instances: list[dict[str, Any]], fallback: int) -> int:
    gemma = select_gemma(instances)
    if gemma:
        try:
            return int(gemma.get("contextLength") or fallback)
        except (TypeError, ValueError):
            return fallback
    return fallback


def build_load_args(model: str, *, gpu: str, context_length: int, parallel: int, estimate_only: bool = False) -> list[str]:
    args = [
        str(LMS_EXE),
        "load",
        model,
        "--gpu",
        gpu,
        "--context-length",
        str(context_length),
        "--parallel",
        str(parallel),
        "--identifier",
        model,
        "-y",
    ]
    if estimate_only:
        args.append("--estimate-only")
    return args


def wait_for_parallel_one(timeout_seconds: float = 180) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deadline = time.perf_counter() + timeout_seconds
    last_instances: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        instances, result = lms_ps_json()
        last_instances = instances
        last_result = result
        gemma = select_gemma(instances)
        if gemma and gemma.get("parallel") == 1:
            return instances, result
        time.sleep(2)
    return last_instances, last_result


def write_recovery_report(
    *,
    before_system: dict[str, Any],
    before_speed: dict[str, Any] | None,
    after_instances: list[dict[str, Any]],
    after_ps_result: dict[str, Any],
    after_speed: dict[str, Any] | None,
    actions: list[dict[str, Any]],
    load_command: list[str],
    estimate: dict[str, Any] | None,
    confirmed: bool,
    context_length: int,
    parallel: int,
    gpu: str,
) -> None:
    before_lms_ps = (((before_system.get("lms_cli") or {}).get("ps") or {}).get("stdout") or "").strip()
    before_minimal = direct_minimal_result((before_speed or {}).get("latest_speed_json") or {})
    after_minimal = direct_minimal_result((after_speed or {}).get("latest_speed_json") or {})
    after_gemma = select_gemma(after_instances)
    before_http = before_system.get("http") or {}
    classified = before_system.get("classified_processes") or {}
    lines = [
        "# LM Runtime Recovery Report",
        "",
        f"Updated: {utc_now()}",
        "",
        "## Rollback Safety",
        "",
        "- Checkpoint commit before this recovery pass: `0e18fd2 pre_lm_runtime_recovery_checkpoint`.",
        r"- App DB backup: `D:\StoryDriver\backend\data\backups\app_pre_lm_runtime_recovery_20260608.db`.",
        "",
        "## Current Bad State Before Reload",
        "",
        f"- lms ps before: `{before_lms_ps or 'not available'}`",
        f"- direct minimal speed before: {before_minimal.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
        f"- hidden reasoning before: {before_minimal.get('reasoning_chars', 'not measured')} chars",
        f"- LM loaded instances before: {len(before_http.get('lm_loaded_instances') or [])}",
        f"- ComfyUI process count before: {len(classified.get('comfyui') or [])}",
        f"- duplicate ComfyUI candidates before: {len(classified.get('duplicate_comfyui_candidates') or [])}",
        "",
        "## Verified CLI Load Options",
        "",
        "- `lms load --help` supports `--gpu`, `--context-length`, `--parallel`, `--identifier`, and `--estimate-only` on this machine.",
        "- LM Studio documents that higher `--parallel` can reduce individual prediction speed; StoryDriver prose wants `--parallel 1`.",
        "",
        "## Reload Plan",
        "",
        f"- confirmed reload requested: {confirmed}",
        f"- target model: `{GEMMA_MODEL}`",
        f"- gpu offload: `{gpu}`",
        f"- context length: {context_length}",
        f"- parallel/concurrency: {parallel}",
        "",
        "Command:",
        "",
        "```bat",
        " ".join(f'"{part}"' if " " in part else part for part in load_command),
        "```",
        "",
        "## Estimate",
        "",
    ]
    if estimate:
        lines.extend(
            [
                f"- estimate ok: {estimate.get('ok')}",
                f"- estimate stdout: `{(estimate.get('stdout') or '')[:800]}`",
                f"- estimate stderr: `{(estimate.get('stderr') or '')[:800]}`",
            ]
        )
    else:
        lines.append("- estimate not run")
    lines.extend(["", "## Reload Actions", ""])
    if actions:
        for action in actions:
            lines.append(f"- {action.get('step', 'action')}: {'ok' if action.get('ok') else 'failed'}")
            if action.get("stdout"):
                lines.append(f"  stdout: `{str(action['stdout'])[:1000]}`")
            if action.get("stderr"):
                lines.append(f"  stderr: `{str(action['stderr'])[:1000]}`")
    else:
        lines.append("- No unload/reload actions were run. Use `--confirm` to execute the verified CLI reload.")
    lines.extend(
        [
            "",
            "## Post-Reload State",
            "",
            f"- target Gemma loaded: {'yes' if after_gemma else 'no'}",
            f"- loaded model: `{after_gemma.get('modelKey') if after_gemma else 'not loaded'}`",
            f"- identifier: `{after_gemma.get('identifier') if after_gemma else 'not loaded'}`",
            f"- context length: {after_gemma.get('contextLength') if after_gemma else 'unknown'}",
            f"- parallel/concurrency: {after_gemma.get('parallel') if after_gemma else 'unknown'}",
            f"- status: {after_gemma.get('status') if after_gemma else 'unknown'}",
            f"- post-reload lms ps ok: {after_ps_result.get('ok')}",
            "",
            "## Speed After Reload",
            "",
            f"- direct minimal speed after: {after_minimal.get('visible_tokens_per_second_estimated', 'not measured')} est. tok/s",
            f"- first visible prose after: {after_minimal.get('first_content_seconds', 'not measured')}s",
            f"- hidden reasoning after: {after_minimal.get('reasoning_chars', 'not measured')} chars",
            "",
            "## Conclusion",
            "",
        ]
    )
    after_parallel = after_gemma.get("parallel") if after_gemma else None
    after_speed_value = after_minimal.get("visible_tokens_per_second_estimated")
    if not confirmed:
        lines.append("- Reload was not executed. Run with `--confirm` to apply the fast single-stream runtime.")
    elif after_parallel != 1:
        lines.append(f"- FAIL: reload did not produce parallel=1. Current parallel is {after_parallel}.")
    elif after_speed_value is not None and float(after_speed_value) < 70:
        lines.append("- PARTIAL: parallel=1 is restored, but direct speed is still below the warning threshold; LM Studio runtime or system resources remain degraded.")
    else:
        lines.append("- PASS: Gemma was reloaded as parallel=1 and direct speed is back in the fast range or was not measured.")
    if after_minimal.get("reasoning_chars") is not None and int(after_minimal.get("reasoning_chars") or 0) > 50:
        lines.append("- WARNING: hidden reasoning returned. Reload Gemma manually in LM Studio and reselect the no-thinking StoryDriver template.")
    lines.extend(
        [
            "",
            "## Daily Recommended Settings",
            "",
            "- Keep only Gemma loaded for speed-sensitive writing.",
            "- Use context around `50749` if you want the same long-context setup.",
            "- Use GPU/offload `max` on this AMD/Vulkan runtime.",
            "- Use parallel/concurrency `1` for fastest single-scene writing.",
            "- In StoryDriver Image Settings, keep Fast Gemma reload profile enabled so Auto Image Priority does not reload Gemma as parallel 4.",
        ]
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reload Gemma with the fast single-stream LM Studio runtime.")
    parser.add_argument("--confirm", action="store_true", help="Actually unload and reload Gemma.")
    parser.add_argument("--model", default=GEMMA_MODEL)
    parser.add_argument("--gpu", default="max")
    parser.add_argument("--context-length", type=int, default=None)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--skip-speed-tests", action="store_true")
    args = parser.parse_args()

    before_system = collect_report(free_impact=False)
    before_instances, _before_ps = lms_ps_json()
    context_length = int(args.context_length or current_context(before_instances, 50749))
    parallel = max(1, int(args.parallel or 1))
    load_help = run_command([str(LMS_EXE), "load", "--help"], timeout=30) if LMS_EXE.exists() else {"ok": False, "stdout": ""}
    flags = supported_load_flags(load_help.get("stdout") or "")
    required_flags = {"gpu", "context_length", "parallel", "identifier", "yes", "estimate_only"}
    missing = sorted(flag for flag in required_flags if not flags.get(flag))
    load_command = build_load_args(args.model, gpu=args.gpu, context_length=context_length, parallel=parallel, estimate_only=False)
    estimate_command = build_load_args(args.model, gpu=args.gpu, context_length=context_length, parallel=parallel, estimate_only=True)
    before_speed = None if args.skip_speed_tests else run_speed_test("before_parallel1_reload", skip_storydriver=True)
    estimate = None
    actions: list[dict[str, Any]] = []
    if LMS_EXE.exists() and not missing:
        estimate = run_command(estimate_command, timeout=180)
    else:
        actions.append(
            {
                "step": "preflight",
                "ok": False,
                "stderr": f"Cannot safely reload: lms.exe exists={LMS_EXE.exists()}, missing load flags={missing}",
            }
        )
    if args.confirm and LMS_EXE.exists() and not missing and (estimate or {}).get("ok"):
        actions.append({"step": "unload", **run_command([str(LMS_EXE), "unload", args.model], timeout=180)})
        actions.append({"step": "load_parallel1", **run_command(load_command, timeout=900)})
        wait_for_parallel_one(timeout_seconds=180)
    elif args.confirm and not (estimate or {}).get("ok"):
        actions.append({"step": "reload_skipped", "ok": False, "stderr": "Estimate failed; reload was not attempted."})
    after_instances, after_ps = lms_ps_json()
    after_speed = None if args.skip_speed_tests else run_speed_test("after_parallel1_reload", skip_storydriver=True)
    write_recovery_report(
        before_system=before_system,
        before_speed=before_speed,
        after_instances=after_instances,
        after_ps_result=after_ps,
        after_speed=after_speed,
        actions=actions,
        load_command=load_command,
        estimate=estimate,
        confirmed=args.confirm,
        context_length=context_length,
        parallel=parallel,
        gpu=args.gpu,
    )
    after_gemma = select_gemma(after_instances)
    after_minimal = direct_minimal_result((after_speed or {}).get("latest_speed_json") or {})
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH),
                "confirmed": args.confirm,
                "parallel": after_gemma.get("parallel") if after_gemma else None,
                "context_length": after_gemma.get("contextLength") if after_gemma else None,
                "direct_minimal_visible_tps_after": after_minimal.get("visible_tokens_per_second_estimated"),
                "hidden_reasoning_chars_after": after_minimal.get("reasoning_chars"),
                "actions": [{"step": item.get("step"), "ok": item.get("ok")} for item in actions],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
