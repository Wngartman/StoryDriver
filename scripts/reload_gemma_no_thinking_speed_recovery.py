from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "GEMMA_NO_THINKING_RELOAD_RECOVERY.md"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
LMS_EXE = Path.home() / ".lmstudio" / "bin" / "lms.exe"

import sys

sys.path.insert(0, str(ROOT / "scripts"))

from system_speed_diagnostics import collect_report  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(args: list[str], *, timeout: float = 300) -> dict:
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
        "command": args,
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def loaded_context(loaded: list[dict]) -> int:
    for instance in loaded:
        config = instance.get("config") or {}
        value = config.get("context_length") or config.get("contextLength")
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 50749


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manual Gemma no-thinking reload recovery guide. Does not reload by default."
    )
    parser.add_argument("--run-speed-test", action="store_true", help="Run lmstudio_current_speed_test after you manually reload Gemma.")
    parser.add_argument("--confirm-cli-reload", action="store_true", help="Unload/reload Gemma with the verified lms CLI command.")
    parser.add_argument("--gpu", default="max", help='GPU offload value for lms load. Default: "max".')
    parser.add_argument("--parallel", type=int, default=1, help="Parallel slot count for lms load. Default: 1 for fastest single stream.")
    parser.add_argument("--context-length", type=int, default=None, help="Context length for lms load. Defaults to current loaded context or 50749.")
    args = parser.parse_args()
    report = collect_report(free_impact=False)
    loaded = ((report.get("http") or {}).get("lm_loaded_instances") or [])
    context_length = int(args.context_length or loaded_context(loaded))
    unload_command = [str(LMS_EXE), "unload", GEMMA_MODEL]
    load_command = [
        str(LMS_EXE),
        "load",
        GEMMA_MODEL,
        "--gpu",
        args.gpu,
        "--context-length",
        str(context_length),
        "--parallel",
        str(args.parallel),
        "--identifier",
        GEMMA_MODEL,
        "-y",
    ]
    estimate_command = [*load_command[:3], *load_command[3:], "--estimate-only"]
    actions: list[dict] = []
    lines = [
        "# Gemma No-Thinking Reload Recovery",
        "",
        f"Generated: {utc_now()}",
        "",
        "This helper does not unload or reload Gemma automatically because LM Studio may not expose the exact UI template/runtime/offload preset through REST.",
        "",
        "## Current Loaded Instances",
        "",
    ]
    if loaded:
        for instance in loaded:
            lines.append(f"- `{instance.get('display_name') or instance.get('model_key') or instance.get('id')}` id `{instance.get('id') or instance.get('instance_id') or instance.get('instanceId') or ''}`")
    else:
        lines.append("- None reported by LM Studio REST.")
    lines.extend(
        [
            "",
            "## Manual Recovery Steps",
            "",
            "1. In LM Studio, unload every model except the target Gemma model, or unload Gemma too if it appears degraded.",
            "2. Reload `gemma4-26b-a4b-uncensored-hauhaucs-balanced` with the no-thinking StoryDriver template selected.",
            "3. Use the same known-fast context/runtime/offload preset you used when the UI reached 120-130+ t/s.",
            "4. Confirm only Gemma is loaded.",
            r"5. Run `D:\StoryDriver\scripts\lmstudio_current_speed_test.bat --label after_manual_gemma_reload`.",
            "",
            "## Verified CLI Recovery Option",
            "",
            "The local `lms load --help` command supports `--gpu`, `--context-length`, and `--parallel`.",
            "For single StoryDriver prose generation, `--parallel 1` is recommended because LM Studio documents that higher parallelism can reduce individual prediction speed.",
            "",
            "Prepared commands:",
            "",
            "```bat",
            f'"{LMS_EXE}" unload {GEMMA_MODEL}',
            f'"{LMS_EXE}" load {GEMMA_MODEL} --gpu {args.gpu} --context-length {context_length} --parallel {args.parallel} --identifier {GEMMA_MODEL} -y',
            r"D:\StoryDriver\scripts\lmstudio_current_speed_test.bat --label after_cli_parallel1_gpumax_reload",
            "```",
            "",
            "Run this helper with `--confirm-cli-reload` only if you are comfortable letting the CLI reload Gemma. Manual LM Studio UI reload remains safer if you need to guarantee the no-thinking template selection.",
            "",
            "## Why This Is Manual",
            "",
            "- The local API can report loaded models, but may not expose the selected prompt template or exact GPU/offload runtime preset.",
            "- An API reload could accidentally lose the no-thinking template or known-fast runtime configuration.",
        ]
    )
    if args.confirm_cli_reload:
        if not LMS_EXE.exists():
            actions.append({"ok": False, "error": f"lms.exe not found at {LMS_EXE}"})
        else:
            actions.append({"step": "unload", **run_command(unload_command, timeout=120)})
            actions.append({"step": "load", **run_command(load_command, timeout=600)})
            actions.append(
                {
                    "step": "speed_test",
                    **run_command(
                        [
                            str(ROOT / "scripts" / "lmstudio_current_speed_test.bat"),
                            "--label",
                            "after_cli_parallel1_gpumax_reload",
                            "--skip-storydriver",
                        ],
                        timeout=900,
                    ),
                }
            )
        lines.extend(["", "## CLI Reload Actions", ""])
        for action in actions:
            lines.append(f"- {action.get('step', 'action')}: {'ok' if action.get('ok') else 'failed'}")
            if action.get("stdout"):
                lines.append(f"  stdout: {str(action['stdout'])[:600]}")
            if action.get("stderr"):
                lines.append(f"  stderr: {str(action['stderr'])[:600]}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.run_speed_test:
        subprocess.run(
            [str(ROOT / "scripts" / "lmstudio_current_speed_test.bat"), "--label", "after_manual_gemma_reload"],
            cwd=str(ROOT),
            shell=False,
            check=False,
        )
    print(json.dumps({"report": str(REPORT_PATH), "auto_reload_performed": bool(args.confirm_cli_reload), "actions": actions}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
