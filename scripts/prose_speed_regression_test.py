from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "PROSE_SPEED_REGRESSION_REPORT.md"
APPLES_REPORT = ROOT / "backend" / "data" / "logs" / "LM_STUDIO_APPLES_TO_APPLES_SPEED_REPORT.md"
BACKEND_URL = "http://localhost:8001"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_json(path: str, timeout: float = 20.0) -> dict:
    request = Request(f"{BACKEND_URL}{path}", method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw or "{}")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} {path}: {detail[:500]}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach StoryDriver backend at {BACKEND_URL}: {error}") from error


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def find_value(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else "not recorded"


def section(text: str, heading: str) -> str:
    pattern = rf"### {re.escape(heading)}\n(?P<body>.*?)(?:\n### |\n## |\Z)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group("body").strip() if match else ""


def first_metric(section_text: str, label: str) -> str:
    return find_value(section_text, rf"- {re.escape(label)}: ([^\n]+)")


def run_apples_test(max_output_tokens: int, skip_comfy_free: bool) -> tuple[int, str]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "lmstudio_apples_to_apples_speed_test.py"),
        "--max-output-tokens",
        str(max_output_tokens),
    ]
    if skip_comfy_free:
        command.append("--skip-comfy-free")
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
    )
    elapsed = time.perf_counter() - started
    return completed.returncode, f"$ {' '.join(command)}\n# elapsed: {elapsed:.3f}s\n\n{completed.stdout}"


def write_report(*, diagnostics: dict, settings: dict, routing: dict, apples_output: str, apples_return: int) -> None:
    apples = read_text(APPLES_REPORT)
    direct_min = section(apples, "A_direct_minimal")
    direct_full = section(apples, "B_direct_exact_storydriver_full_prompt")
    direct_minimal_context = section(apples, "direct_storydriver_like_minimal_context")
    direct_after_free = section(apples, "F_direct_minimal_after_comfy_free")
    sd_min = section(apples, "D_storydriver_minimal_context")
    sd_full = section(apples, "C_E_storydriver_seeded_full_context")
    lm = diagnostics.get("lm_studio") or {}
    loaded = lm.get("loaded_instances") or []
    image = diagnostics.get("image") or diagnostics.get("comfyui") or {}
    resolved = routing.get("resolved") or {}
    prose = resolved.get("prose_generation") or {}
    direct_speed = first_metric(direct_min, "Visible prose speed after first content")
    direct_full_speed = first_metric(direct_full, "Visible prose speed after first content")
    sd_speed = first_metric(sd_full, "Saved StoryDriver t/s")
    sd_min_speed = first_metric(sd_min, "Saved StoryDriver t/s")
    after_free_speed = first_metric(direct_after_free, "Visible prose speed after first content")
    hidden_reasoning = first_metric(sd_full, "Saved hidden reasoning chars")
    stages_recorded = first_metric(sd_full, "Stages") != "not recorded"
    likely_bottleneck = find_value(
        apples,
        r"## Likely Bottleneck\s+(?:\n- ([^\n]+))",
    )

    lines = [
        "# Prose Speed Regression Report",
        "",
        f"Updated: {utc_now()}",
        "",
        "## Scope",
        "- Human-test issue: Gemma no-thinking template fixed hidden reasoning, but StoryDriver visible t/s regressed to roughly 48 t/s in real use.",
        "- This report focuses on runtime/resource/prompt/context speed, not hidden reasoning unless it reappears.",
        "",
        "## Current Runtime Snapshot",
        f"- Backend reachable: {'yes' if diagnostics else 'no'}",
        f"- LM Studio OpenAI reachable: {'yes' if lm.get('openai_reachable') or lm.get('reachable') else 'check diagnostics'}",
        f"- LM Studio REST reachable: {'yes' if lm.get('rest_reachable') else 'check diagnostics'}",
        f"- Loaded LM Studio model count: {len(loaded)}",
    ]
    for item in loaded:
        lines.append(f"  - `{item.get('display_name') or item.get('model_key') or 'unknown'}` / instance `{item.get('id') or 'unknown'}`")
    lines.extend(
        [
            f"- Prose task model: `{prose.get('model') or settings.get('model') or 'global/default'}`",
            f"- Prose backend: `{prose.get('inference_backend') or settings.get('inference_backend') or 'openai_compatible'}`",
            f"- Prose reasoning mode: `{prose.get('reasoning_mode') or settings.get('reasoning_mode') or 'auto/default'}`",
            f"- Prose prompt mode: `{settings.get('prose_prompt_mode') or 'unknown'}`",
            f"- ComfyUI reachable: {'yes' if image.get('reachable') else 'check diagnostics'}",
            "",
            "## Speed Matrix",
            f"- Direct LM Studio minimal prompt visible t/s: {direct_speed}",
            f"- Direct exact StoryDriver prompt visible t/s: {direct_full_speed}",
            f"- Direct StoryDriver-like minimal context visible t/s: {first_metric(direct_minimal_context, 'Visible prose speed after first content')}",
            f"- Direct minimal after ComfyUI /free visible t/s: {after_free_speed}",
            f"- StoryDriver minimal-context saved visible t/s: {sd_min_speed}",
            f"- StoryDriver full-context saved visible t/s: {sd_speed}",
            f"- StoryDriver full hidden reasoning chars: {hidden_reasoning}",
            "",
            "## Background Contention",
            f"- StoryDriver stream stages recorded: {'yes' if stages_recorded else 'no'}",
            "- State extraction, summaries, image prompt drafts, title generation, and TTS warmup are queued after scene save in the generation route.",
            "- If t/s is still low while hidden reasoning is gone, compare direct minimal vs direct exact prompt and ComfyUI /free numbers above.",
            "",
            "## Likely Bottleneck",
            f"- {likely_bottleneck}",
            "",
            "## ComfyUI Idle / Free Impact",
            f"- Direct minimal before /free: {direct_speed}",
            f"- Direct minimal after /free: {after_free_speed}",
            "- ComfyUI was not closed by this test; it only uses `/free` when not skipped.",
            "",
            "## Test Command",
            f"- Apples-to-apples return code: {apples_return}",
            f"- Full apples report: `{APPLES_REPORT}`",
            "",
            "## Raw Command Output",
            "```text",
            apples_output[-6000:],
            "```",
            "",
            "## Recommendation",
            "- If direct API speeds are also near 48 t/s, the bottleneck is LM Studio server/runtime/resource state, not StoryDriver rendering.",
            "- If direct minimal is fast but exact StoryDriver prompt is slow, reduce prompt/context budgets first.",
            "- If `/free` improves direct API speed, enable `Free idle ComfyUI before writing` and keep it throttled.",
            "- Keep Gemma manually loaded with the no-thinking template for fastest prose; avoid image-job reloads during prose benchmarking.",
        ]
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    global BACKEND_URL
    parser = argparse.ArgumentParser(description="Focused StoryDriver prose speed regression test.")
    parser.add_argument("--backend", default=BACKEND_URL)
    parser.add_argument("--max-output-tokens", type=int, default=260)
    parser.add_argument("--skip-comfy-free", action="store_true")
    args = parser.parse_args()
    BACKEND_URL = args.backend.rstrip("/")

    diagnostics = request_json("/diagnostics", timeout=30)
    settings = request_json("/settings/model", timeout=20)
    routing = request_json("/settings/task-model-profiles", timeout=20)
    return_code, output = run_apples_test(args.max_output_tokens, args.skip_comfy_free)
    write_report(
        diagnostics=diagnostics,
        settings=settings,
        routing=routing,
        apples_output=output,
        apples_return=return_code,
    )
    print(f"Wrote {REPORT_PATH}")
    return return_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            "# Prose Speed Regression Report\n\n"
            f"Updated: {utc_now()}\n\n"
            f"- result: failed\n- error: {error}\n",
            encoding="utf-8",
        )
        print(f"[FAIL] {error}")
        raise
