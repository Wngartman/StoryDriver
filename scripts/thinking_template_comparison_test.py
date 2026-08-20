"""Manual LM Studio thinking-template comparison for StoryDriver.

This intentionally does not switch LM Studio templates. The user loads each
template in LM Studio, then this script sends the same local probe prompt and
records visible prose speed, hidden reasoning, and output shape.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "THINKING_TEMPLATE_COMPARISON_REPORT.md"

DEFAULT_SYSTEM_PROMPT = (
    "You are StoryDriver's fiction engine. Write narrated prose only. "
    "The user is the director/editor, not a character. Do not explain, ask questions, "
    "or add assistant-style framing."
)
DEFAULT_USER_PROMPT = (
    "Write the opening 700-900 words of a grounded dark-fantasy chapter. "
    "A wounded scout returns to a rain-soaked border town with evidence that the old treaty has been broken. "
    "Keep continuity clean, use concrete sensory detail, and end on a sharp story beat."
)


def utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text or "") / 4))


def http_json(url: str, timeout: float = 10.0) -> dict:
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def loaded_model(lm_url: str, requested_model: str = "") -> str:
    if requested_model.strip():
        return requested_model.strip()
    payload = http_json(f"{lm_url.rstrip('/')}/models")
    models = payload.get("data") or []
    for model in models:
        model_id = model.get("id")
        if model_id:
            return str(model_id)
    raise RuntimeError("LM Studio returned no loaded models.")


def iter_sse_lines(response) -> list[str]:
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if line:
            yield line


def extract_delta(payload: dict) -> tuple[str, str]:
    choices = payload.get("choices") or []
    if not choices:
        return "", ""
    delta = choices[0].get("delta") or {}
    message = choices[0].get("message") or {}
    content = delta.get("content") or message.get("content") or ""
    reasoning = (
        delta.get("reasoning_content")
        or delta.get("reasoning")
        or message.get("reasoning_content")
        or message.get("reasoning")
        or ""
    )
    return str(content or ""), str(reasoning or "")


def run_probe(label: str, lm_url: str, model: str, timeout: float, prompt: str) -> dict:
    started = time.perf_counter()
    first_visible: float | None = None
    first_reasoning: float | None = None
    visible_chunks: list[str] = []
    reasoning_chars = 0
    raw_chunks = 0
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "top_p": 0.95,
        "max_tokens": 1800,
        "stream": True,
    }
    req = request.Request(
        f"{lm_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        for line in iter_sse_lines(response):
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_chunks += 1
            content, reasoning = extract_delta(payload)
            if reasoning:
                reasoning_chars += len(reasoning)
                if first_reasoning is None:
                    first_reasoning = time.perf_counter() - started
            if content:
                visible_chunks.append(content)
                if first_visible is None:
                    first_visible = time.perf_counter() - started

    elapsed = max(0.001, time.perf_counter() - started)
    output = "".join(visible_chunks).strip()
    visible_tokens = estimate_tokens(output)
    reasoning_tokens = estimate_tokens("x" * reasoning_chars) if reasoning_chars else 0
    visible_after_first = (
        max(0.001, elapsed - first_visible) if first_visible is not None else elapsed
    )
    paragraphs = [part for part in output.splitlines() if part.strip()]
    dialogue_lines = [part for part in paragraphs if '"' in part or part.strip().startswith("-")]
    return {
        "label": label,
        "model": model,
        "elapsed_seconds": round(elapsed, 3),
        "first_visible_prose_latency_seconds": round(first_visible, 3) if first_visible is not None else None,
        "first_reasoning_latency_seconds": round(first_reasoning, 3) if first_reasoning is not None else None,
        "hidden_reasoning_chars": reasoning_chars,
        "hidden_reasoning_tokens_estimated": reasoning_tokens,
        "visible_chars": len(output),
        "visible_tokens_estimated": visible_tokens,
        "visible_tokens_per_second": round(visible_tokens / elapsed, 2),
        "visible_tokens_after_first_per_second": round(visible_tokens / visible_after_first, 2),
        "raw_stream_tokens_estimated": visible_tokens + reasoning_tokens,
        "raw_stream_tokens_per_second": round((visible_tokens + reasoning_tokens) / elapsed, 2),
        "raw_stream_chunks": raw_chunks,
        "word_count": len(output.split()),
        "paragraph_count": len(paragraphs),
        "dialogue_line_count": len(dialogue_lines),
        "ending_preview": output[-260:],
        "output": output,
    }


def write_output(label: str, stamp: str, result: dict) -> Path:
    path = LOG_DIR / f"thinking_template_{label}_{stamp}.txt"
    path.write_text(result.get("output") or "", encoding="utf-8")
    return path


def render_report(results: list[dict], output_paths: dict[str, Path], dry_run: bool, error_text: str = "") -> str:
    lines = [
        "# Thinking Template Comparison Report",
        "",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        f"Dry run: {dry_run}",
        "",
        "## Purpose",
        "",
        "Compare LM Studio no-thinking and thinking prompt templates without StoryDriver switching templates or forcing reasoning mode.",
        "",
    ]
    if error_text:
        lines.extend(["## Error", "", error_text, ""])
    if dry_run:
        lines.extend(
            [
                "## Dry Run",
                "",
                "No generation was requested. Run without `--dry-run`, load each Gemma template manually in LM Studio when prompted, and press Enter for each pass.",
                "",
            ]
        )
    if results:
        lines.extend(["## Results", ""])
        for result in results:
            output_path = output_paths.get(result["label"])
            lines.extend(
                [
                    f"### {result['label']}",
                    "",
                    f"- Model: `{result['model']}`",
                    f"- First visible prose: {result['first_visible_prose_latency_seconds']}s",
                    f"- Hidden reasoning: {result['hidden_reasoning_chars']} chars / {result['hidden_reasoning_tokens_estimated']} est. tokens",
                    f"- Visible speed: {result['visible_tokens_per_second']} est. tok/s",
                    f"- Visible after first: {result['visible_tokens_after_first_per_second']} est. tok/s",
                    f"- Raw stream speed: {result['raw_stream_tokens_per_second']} est. tok/s",
                    f"- Output: {result['word_count']} words, {result['paragraph_count']} paragraphs, {result['dialogue_line_count']} dialogue-ish lines",
                    f"- Saved output: `{output_path}`" if output_path else "- Saved output: not saved",
                    "",
                ]
            )
        if len(results) == 2:
            no_thinking, thinking = results
            lines.extend(
                [
                    "## Comparison",
                    "",
                    f"- First visible prose delta: {round((thinking.get('first_visible_prose_latency_seconds') or 0) - (no_thinking.get('first_visible_prose_latency_seconds') or 0), 3)}s",
                    f"- Hidden reasoning delta: {(thinking.get('hidden_reasoning_chars') or 0) - (no_thinking.get('hidden_reasoning_chars') or 0)} chars",
                    f"- Visible speed delta: {round((thinking.get('visible_tokens_per_second') or 0) - (no_thinking.get('visible_tokens_per_second') or 0), 2)} est. tok/s",
                    f"- Word count delta: {(thinking.get('word_count') or 0) - (no_thinking.get('word_count') or 0)} words",
                    "",
                ]
            )
    lines.extend(
        [
            "## Notes",
            "",
            "- StoryDriver does not switch LM Studio templates in this test.",
            "- Exact thinking mode depends on the model and loaded LM Studio prompt template/runtime.",
            "- Hidden reasoning is measured only when LM Studio emits reasoning fields in the stream.",
            "- Exact word-level quality review is left to the saved local outputs.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare LM Studio no-thinking vs thinking templates.")
    parser.add_argument("--lm-url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--prompt", default=DEFAULT_USER_PROMPT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        REPORT_PATH.write_text(render_report([], {}, True), encoding="utf-8")
        print(f"Dry run complete. Report: {REPORT_PATH}")
        return 0

    try:
        model = loaded_model(args.lm_url, args.model)
        print(f"Using LM Studio model: {model}")
        input("Load the Gemma no-thinking template/runtime in LM Studio, then press Enter...")
        first = run_probe("no_thinking", args.lm_url, model, args.timeout, args.prompt)
        input("Load the Gemma thinking template/runtime in LM Studio, then press Enter...")
        model_second = loaded_model(args.lm_url, args.model)
        second = run_probe("thinking", args.lm_url, model_second, args.timeout, args.prompt)
        stamp = utc_stamp()
        output_paths = {
            "no_thinking": write_output("no_thinking", stamp, first),
            "thinking": write_output("thinking", stamp, second),
        }
        REPORT_PATH.write_text(render_report([first, second], output_paths, False), encoding="utf-8")
        print(f"Comparison complete. Report: {REPORT_PATH}")
        return 0
    except (OSError, error.URLError, RuntimeError) as exc:
        REPORT_PATH.write_text(render_report([], {}, False, str(exc)), encoding="utf-8")
        print(f"Comparison failed: {exc}")
        print(f"Report: {REPORT_PATH}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
