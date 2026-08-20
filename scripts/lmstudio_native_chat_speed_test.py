from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "LM_STUDIO_NATIVE_CHAT_REPORT.md"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
TEST_TITLE = "StoryDriver Native Chat Speed Test"
MINIMAL_SYSTEM_PROMPT = "You are a fast local fiction prose generator. Return prose only."
MINIMAL_USER_PROMPT = (
    "Write one grounded medieval prose paragraph about rain on a farm road. "
    "No headings, no explanation, prose only."
)

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT / "scripts"))

from app.utils.openssl_dlls import add_openssl_dll_directory  # noqa: E402


add_openssl_dll_directory()

from app.generation.model_provider import native_base_url_from_openai_base, native_chat_body  # noqa: E402
from app.generation.router import resolve_task_model_settings  # noqa: E402
from lmstudio_speed_diagnostics import (  # noqa: E402
    StreamResult,
    capped_parameters,
    choose_model,
    estimate_tokens,
    load_env,
    request_json,
)
from lmstudio_ui_parity_test import (  # noqa: E402
    build_storydriver_payloads,
    format_result,
    minimal_body,
    stream_payload,
)


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request_backend_json(
    backend_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{backend_url.rstrip('/')}{path}", method=method, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def profile_update_payload(profile: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "lm_studio_url",
        "model",
        "temperature",
        "top_p",
        "max_tokens",
        "seed",
        "top_k",
        "min_p",
        "repeat_penalty",
        "presence_penalty",
        "frequency_penalty",
        "timeout_seconds",
        "streaming",
        "inference_backend",
        "reasoning_mode",
        "context_length",
        "fallback_to_openai_compatible",
        "notes",
    }
    return {key: value for key, value in profile.items() if key in allowed}


def create_test_session(backend_url: str, suffix: str) -> str:
    created = request_backend_json(
        backend_url,
        "/sessions",
        method="POST",
        payload={"title": f"{TEST_TITLE} {suffix}"},
        timeout=20,
    )
    return str(created["id"])


def run_storydriver_generation(backend_url: str, label: str) -> StreamResult:
    session_id = create_test_session(backend_url, label)
    note = (
        "Beat: Write 250-350 words of grounded no-magic medieval prose. "
        "Three sisters at a farm table decide whether to rescue a captured child. "
        "No headings, no explanation, prose only."
    )
    started = time.perf_counter()
    first_delta: float | None = None
    content_parts: list[str] = []
    reasoning_chars = 0
    stats: dict[str, Any] = {}
    error: str | None = None
    try:
        req = urllib.request.Request(
            f"{backend_url.rstrip('/')}/sessions/{session_id}/generate-stream",
            method="POST",
            data=json.dumps({"director_note": note, "mode": "continue"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=420) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get("type") == "delta":
                    text = event.get("text") or ""
                    content_parts.append(text)
                    if first_delta is None:
                        first_delta = time.perf_counter()
                elif event.get("type") == "scene":
                    scene = event.get("scene") or {}
                    versions = scene.get("versions") or []
                    stats = (versions[-1].get("generation_stats") if versions else scene.get("generation_stats")) or {}
                    reasoning_chars = int(stats.get("reasoning_chars") or 0)
                elif event.get("type") == "error":
                    error = str(event.get("detail") or "StoryDriver generation failed.")
                    break
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    total = time.perf_counter() - started
    output_text = "".join(content_parts)
    output_tokens = estimate_tokens(output_text) if output_text else 0
    visible_seconds = total - (first_delta - started) if first_delta and total > (first_delta - started) else total
    return StreamResult(
        label=label,
        ok=not error and bool(output_text),
        error=error,
        model=str(stats.get("model") or ""),
        prompt_chars=int((stats.get("prompt_diagnostics") or {}).get("total_prompt_chars") or 0),
        prompt_tokens_estimated=int((stats.get("prompt_diagnostics") or {}).get("prompt_estimated_tokens") or 0),
        configured_max_tokens=stats.get("requested_max_tokens"),
        diagnostic_max_tokens=stats.get("requested_max_tokens"),
        first_content_seconds=round(first_delta - started, 3) if first_delta else None,
        first_reasoning_seconds=stats.get("first_reasoning_latency_seconds"),
        total_seconds=round(total, 3),
        output_chars=len(output_text),
        output_tokens_estimated=output_tokens,
        reasoning_chars=reasoning_chars,
        reasoning_tokens_estimated=int(stats.get("reasoning_tokens_estimated") or estimate_tokens("x" * reasoning_chars) if reasoning_chars else 0),
        tokens_per_second_estimated=stats.get("raw_stream_tokens_per_second") or stats.get("tokens_per_second"),
        visible_tokens_per_second_estimated=stats.get("visible_prose_tokens_per_second") or stats.get("tokens_per_second"),
        chunks=0,
        reasoning_chunks=int(stats.get("reasoning_chunks") or 0),
        usage={
            "generation_stats": {
                key: stats.get(key)
                for key in (
                    "inference_backend",
                    "requested_inference_backend",
                    "reasoning_mode",
                    "native_chat_fallback_used",
                    "native_chat_warnings",
                    "native_chat_stats",
                    "lmstudio_raw_tokens_per_second",
                    "lmstudio_reasoning_output_tokens",
                    "lmstudio_time_to_first_token_seconds",
                )
            }
        },
    )


def stream_native_chat(
    *,
    label: str,
    native_url: str,
    body: dict[str, Any],
    timeout: float,
) -> StreamResult:
    started = time.perf_counter()
    first_any: float | None = None
    first_reasoning: float | None = None
    first_content: float | None = None
    output_parts: list[str] = []
    reasoning_parts: list[str] = []
    chunks = 0
    reasoning_chunks = 0
    stats: dict[str, Any] = {}
    prompt_text = f"{body.get('system_prompt') or ''}\n{body.get('input') or ''}"

    try:
        req = urllib.request.Request(
            f"{native_url.rstrip('/')}/chat",
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload.get("error"), dict):
                    return StreamResult(label=label, ok=False, error=json.dumps(payload["error"])[:800], model=str(body.get("model") or ""))
                if isinstance(payload.get("stats"), dict):
                    stats = payload["stats"]
                payload_type = str(payload.get("type") or "").lower()
                content = ""
                reasoning = ""
                if "reasoning" in payload_type:
                    reasoning = str(payload.get("content") or payload.get("text") or payload.get("delta") or "")
                else:
                    content = str(payload.get("content") or payload.get("text") or payload.get("delta") or "")
                output = payload.get("output")
                if isinstance(output, list):
                    for item in output:
                        if not isinstance(item, dict):
                            continue
                        item_type = str(item.get("type") or "").lower()
                        item_text = str(item.get("content") or item.get("text") or "")
                        if "reasoning" in item_type:
                            reasoning += item_text
                        else:
                            content += item_text
                if reasoning:
                    reasoning_chunks += 1
                    reasoning_parts.append(reasoning)
                    if first_any is None:
                        first_any = time.perf_counter()
                    if first_reasoning is None:
                        first_reasoning = time.perf_counter()
                    continue
                if content:
                    chunks += 1
                    output_parts.append(content)
                    if first_any is None:
                        first_any = time.perf_counter()
                    if first_content is None:
                        first_content = time.perf_counter()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        return StreamResult(label=label, ok=False, error=f"HTTP {error.code}: {detail[:800]}", model=str(body.get("model") or ""))
    except Exception as error:  # noqa: BLE001
        return StreamResult(label=label, ok=False, error=str(error), model=str(body.get("model") or ""))

    total = time.perf_counter() - started
    output_text = "".join(output_parts)
    reasoning_text = "".join(reasoning_parts)
    output_tokens = int(stats.get("total_output_tokens") or 0)
    reasoning_tokens = int(stats.get("reasoning_output_tokens") or 0)
    if output_tokens and reasoning_tokens:
        visible_tokens = max(0, output_tokens - reasoning_tokens)
    else:
        visible_tokens = estimate_tokens(output_text) if output_text else 0
        reasoning_tokens = estimate_tokens(reasoning_text) if reasoning_text else 0
        output_tokens = visible_tokens + reasoning_tokens
    first_content_seconds = first_content - started if first_content else None
    first_any_seconds = first_any - started if first_any else None
    first_reasoning_seconds = first_reasoning - started if first_reasoning else None
    raw_speed = stats.get("tokens_per_second")
    visible_seconds = total - first_content_seconds if first_content_seconds and total > first_content_seconds else total
    visible_speed = visible_tokens / visible_seconds if visible_tokens and visible_seconds > 0 else None
    return StreamResult(
        label=label,
        ok=True,
        model=str(body.get("model") or ""),
        prompt_chars=len(prompt_text),
        prompt_tokens_estimated=estimate_tokens(prompt_text),
        configured_max_tokens=int(body.get("max_output_tokens") or 0) or None,
        diagnostic_max_tokens=int(body.get("max_output_tokens") or 0) or None,
        first_any_seconds=round(first_any_seconds, 3) if first_any_seconds is not None else None,
        first_reasoning_seconds=round(first_reasoning_seconds, 3) if first_reasoning_seconds is not None else None,
        first_content_seconds=round(first_content_seconds, 3) if first_content_seconds is not None else None,
        total_seconds=round(total, 3),
        output_chars=len(output_text),
        output_tokens_estimated=visible_tokens,
        reasoning_chars=len(reasoning_text),
        reasoning_tokens_estimated=reasoning_tokens,
        tokens_per_second_estimated=round(float(raw_speed), 2) if isinstance(raw_speed, (int, float)) else None,
        visible_tokens_per_second_estimated=round(visible_speed, 2) if visible_speed else None,
        chunks=chunks,
        reasoning_chunks=reasoning_chunks,
        usage=stats,
        parameters={key: body.get(key) for key in ("temperature", "top_p", "max_output_tokens", "context_length", "reasoning") if key in body},
    )


def storydriver_profile_variant(
    *,
    backend_url: str,
    original_profile: dict[str, Any],
    model: str,
    lm_studio_url: str,
    inference_backend: str,
    reasoning_mode: str,
) -> StreamResult:
    patch = {
        "lm_studio_url": lm_studio_url,
        "model": model,
        "inference_backend": inference_backend,
        "reasoning_mode": reasoning_mode,
        "fallback_to_openai_compatible": True,
        "streaming": True,
    }
    request_backend_json(
        backend_url,
        "/settings/task-model-profiles/prose_generation",
        method="PUT",
        payload=patch,
        timeout=20,
    )
    return run_storydriver_generation(
        backend_url,
        f"G_storydriver_{inference_backend}_reasoning_{reasoning_mode}"
        if inference_backend == "native_rest"
        else "H_storydriver_openai_compatible",
    )


def build_report(
    *,
    openai_url: str,
    native_url: str,
    model: str,
    results: list[StreamResult],
    storydriver_results: list[StreamResult],
) -> str:
    def seconds(value: Any) -> str:
        return f"{value}s" if value is not None else "none"

    def fallback_note(result: StreamResult | None) -> str:
        if not result or not isinstance(result.usage, dict):
            return ""
        stats = result.usage.get("generation_stats") if isinstance(result.usage.get("generation_stats"), dict) else {}
        return " (fell back to OpenAI-compatible)" if stats.get("native_chat_fallback_used") else ""

    native_off = next((result for result in results if result.label.endswith("native_reasoning_off")), None)
    native_auto = next((result for result in results if result.label.endswith("native_reasoning_auto")), None)
    openai_exact = next((result for result in results if result.label == "D_openai_exact_storydriver_prompt"), None)
    native_exact_off = next((result for result in results if result.label == "F_native_exact_storydriver_prompt_native_reasoning_off"), None)
    reasoning_off_accepted = bool(native_off and native_off.ok)
    native_works = any(result.ok and "native" in result.label for result in results)
    fastest_visible = min(
        [result for result in [*results, *storydriver_results] if result.ok and result.first_content_seconds is not None],
        key=lambda item: float(item.first_content_seconds or 999999),
        default=None,
    )
    lines = [
        "# LM Studio Native Chat Report",
        "",
        f"Updated: {utc_stamp()}",
        "",
        "## Summary",
        "",
        f"- Model tested: `{model}`",
        f"- OpenAI-compatible URL: `{openai_url}`",
        f"- Native REST Chat URL: `{native_url}/chat`",
        f"- Local `/api/v1/chat` works: {'yes' if native_works else 'no'}",
        f"- `reasoning=\"off\"` accepted: {'yes' if reasoning_off_accepted else 'no'}",
        f"- Fastest first visible prose: `{fastest_visible.label}` at {fastest_visible.first_content_seconds}s{fallback_note(fastest_visible)}"
        if fastest_visible
        else "- Fastest first visible prose: none produced visible prose in the matrix.",
        "",
        "## Direct Matrix",
        "",
    ]
    for result in results:
        lines.extend(format_result(result))
        if result.usage:
            lines.append(f"- Native/OpenAI stats: `{json.dumps(result.usage, ensure_ascii=False)[:900]}`")
            lines.append("")
    lines.extend(["## StoryDriver Route Matrix", ""])
    for result in storydriver_results:
        lines.extend(format_result(result))
        if result.usage:
            lines.append(f"- StoryDriver stats: `{json.dumps(result.usage, ensure_ascii=False)[:900]}`")
            lines.append("")
    lines.extend(
        [
            "## Findings",
            "",
            f"1. Whether local `/api/v1/chat` works: {'yes' if native_works else 'no'}.",
            f"2. Whether `reasoning=\"off\"` is accepted: {'yes' if reasoning_off_accepted else 'no'}.",
            f"3. OpenAI-compatible exact prompt first visible prose: {seconds(openai_exact.first_content_seconds if openai_exact else None)}.",
            f"4. Native exact prompt with reasoning off first visible prose: {seconds(native_exact_off.first_content_seconds) if native_exact_off and native_exact_off.ok else 'not available'}.",
            f"5. Hidden reasoning comparison: Native auto produced {native_auto.reasoning_chars if native_auto else 'unknown'} reasoning chars; Native off {'did not run cleanly' if not reasoning_off_accepted else f'produced {native_off.reasoning_chars} reasoning chars'}.",
            "6. Raw LM Studio stats are recorded when Native Chat emits `stats`.",
            f"7. Native Chat should become default for prose_generation: {'not yet - reasoning off was not accepted for this loaded model' if not reasoning_off_accepted else 'candidate - compare the full StoryDriver route result before changing defaults'}",
            "",
            "## Recommended Settings",
            "",
        ]
    )
    if not reasoning_off_accepted:
        lines.extend(
            [
                "- Keep `prose_generation` on OpenAI-compatible or Native auto until LM Studio exposes reasoning control for the loaded Gemma instance.",
                "- If you want to retry Native off, check LM Studio model/runtime settings; this loaded model reported that it does not expose reasoning configuration.",
                "- Continue using Direct / system-priority prompt mode when first visible prose latency matters.",
            ]
        )
    else:
        lines.extend(
            [
                "- Set `prose_generation` to Native REST Chat and `reasoning_mode=off` for the fastest visible-prose path from this matrix.",
                "- Leave fallback enabled while testing longer chapter requests.",
            ]
        )
    lines.extend(
        [
            "",
            "## What StoryDriver Changed",
            "",
            "- Added per-task LM Studio inference backend selection.",
            "- Added per-task reasoning mode and context length fields.",
            "- Added one-click Model Settings actions for Native REST reasoning off on prose or writing tasks.",
            "- Added Native REST Chat streaming/parser support with safe OpenAI fallback.",
            "- Saved backend/reasoning/native stats in scene generation metadata and diagnostics.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare LM Studio OpenAI-compatible chat with Native REST Chat reasoning modes.")
    parser.add_argument("--backend-url", default=os.environ.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001"))
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--use-configured-max-tokens", action="store_true")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    load_env(ROOT / "backend" / ".env")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    model_settings, resolved_model = resolve_task_model_settings("prose_generation")
    openai_url = model_settings.lm_studio_url.rstrip("/")
    native_url = native_base_url_from_openai_base(openai_url)
    model = choose_model(openai_url, model_settings.model)
    parameters = {
        "temperature": model_settings.temperature,
        "top_p": model_settings.top_p,
        "max_tokens": model_settings.max_tokens,
        "seed": model_settings.seed,
        "top_k": model_settings.top_k,
        "min_p": model_settings.min_p,
        "repeat_penalty": model_settings.repeat_penalty,
    }
    diagnostic_params, configured_max_tokens = capped_parameters(
        parameters,
        args.max_output_tokens,
        args.use_configured_max_tokens,
    )
    prompt_bundle = build_storydriver_payloads(
        model=model,
        model_settings=model_settings,
        resolved_model=resolved_model,
        max_output_tokens=args.max_output_tokens,
        use_configured_max_tokens=args.use_configured_max_tokens,
    )

    minimal_openai = minimal_body(
        model=model,
        messages=[{"role": "user", "content": MINIMAL_USER_PROMPT}],
        parameters=diagnostic_params,
    )
    exact_openai = deepcopy(prompt_bundle["exact_body"])
    exact_openai["stream"] = True
    exact_openai["max_tokens"] = diagnostic_params.get("max_tokens")

    exact_messages = exact_openai.get("messages") or []
    exact_system = str(exact_messages[0].get("content") if exact_messages else model_settings.system_prompt)
    exact_user = str(exact_messages[1].get("content") if len(exact_messages) > 1 else "")

    results = [
        stream_payload(
            label="A_openai_minimal_prompt",
            base_url=openai_url,
            body=minimal_openai,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_native_chat(
            label="B_native_minimal_prompt_native_reasoning_auto",
            native_url=native_url,
            body=native_chat_body(
                model=model,
                system_prompt=MINIMAL_SYSTEM_PROMPT,
                user_prompt=MINIMAL_USER_PROMPT,
                parameters=diagnostic_params,
                stream=True,
                reasoning_mode="auto",
                context_length=model_settings.context_length,
            ),
            timeout=resolved_model.timeout_seconds,
        ),
        stream_native_chat(
            label="C_native_minimal_prompt_native_reasoning_off",
            native_url=native_url,
            body=native_chat_body(
                model=model,
                system_prompt=MINIMAL_SYSTEM_PROMPT,
                user_prompt=MINIMAL_USER_PROMPT,
                parameters=diagnostic_params,
                stream=True,
                reasoning_mode="off",
                context_length=model_settings.context_length,
            ),
            timeout=resolved_model.timeout_seconds,
        ),
        stream_payload(
            label="D_openai_exact_storydriver_prompt",
            base_url=openai_url,
            body=exact_openai,
            configured_max_tokens=prompt_bundle["configured_max_tokens"],
            timeout=resolved_model.timeout_seconds,
        ),
        stream_native_chat(
            label="E_native_exact_storydriver_prompt_native_reasoning_auto",
            native_url=native_url,
            body=native_chat_body(
                model=model,
                system_prompt=exact_system,
                user_prompt=exact_user,
                parameters=diagnostic_params,
                stream=True,
                reasoning_mode="auto",
                context_length=model_settings.context_length,
            ),
            timeout=resolved_model.timeout_seconds,
        ),
        stream_native_chat(
            label="F_native_exact_storydriver_prompt_native_reasoning_off",
            native_url=native_url,
            body=native_chat_body(
                model=model,
                system_prompt=exact_system,
                user_prompt=exact_user,
                parameters=diagnostic_params,
                stream=True,
                reasoning_mode="off",
                context_length=model_settings.context_length,
            ),
            timeout=resolved_model.timeout_seconds,
        ),
    ]

    storydriver_results: list[StreamResult] = []
    original_profile: dict[str, Any] | None = None
    try:
        profiles_response = request_backend_json(args.backend_url, "/settings/task-model-profiles", timeout=20)
        original_profile = (profiles_response.get("profiles") or {}).get("prose_generation") if isinstance(profiles_response, dict) else None
        if original_profile:
            storydriver_results.append(
                storydriver_profile_variant(
                    backend_url=args.backend_url,
                    original_profile=original_profile,
                    model=model,
                    lm_studio_url=openai_url,
                    inference_backend="native_rest",
                    reasoning_mode="off",
                )
            )
            storydriver_results.append(
                storydriver_profile_variant(
                    backend_url=args.backend_url,
                    original_profile=original_profile,
                    model=model,
                    lm_studio_url=openai_url,
                    inference_backend="openai_compatible",
                    reasoning_mode="auto",
                )
            )
    except Exception as error:  # noqa: BLE001
        storydriver_results.append(StreamResult(label="G_H_storydriver_route_tests", ok=False, error=str(error), model=model))
    finally:
        if original_profile:
            try:
                request_backend_json(
                    args.backend_url,
                    "/settings/task-model-profiles/prose_generation",
                    method="PUT",
                    payload=profile_update_payload(original_profile),
                    timeout=20,
                )
            except Exception:
                pass

    report = build_report(
        openai_url=openai_url,
        native_url=native_url,
        model=model,
        results=results,
        storydriver_results=storydriver_results,
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    return 0 if any(result.ok and "native" in result.label for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
