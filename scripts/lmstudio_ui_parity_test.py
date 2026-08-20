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
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "LM_STUDIO_UI_PARITY_REPORT.md"
PAYLOAD_DEBUG_PATH = ROOT / "backend" / "data" / "logs" / "last_lmstudio_prose_request_payload.json"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(ROOT / "scripts"))

from app.utils.openssl_dlls import add_openssl_dll_directory  # noqa: E402


add_openssl_dll_directory()

from app.config import settings  # noqa: E402
from app.database import db_session  # noqa: E402
from app.routes.sessions import (  # noqa: E402
    build_prompt_diagnostics,
    generation_parameters,
    lmstudio_chat_payload,
    prepare_generation,
    prose_task_notes_for_prompt,
    task_type_for_generation_mode,
    write_prose_debug_files,
)
from app.schemas import GenerateSceneRequest  # noqa: E402
from app.generation.router import resolve_task_model_settings  # noqa: E402
from app.generation.prompt_builder import build_scene_prompt, resolve_writing_length  # noqa: E402
from app.settings.store import load_image_settings, load_model_settings  # noqa: E402
from lmstudio_speed_diagnostics import (  # noqa: E402
    GEMMA_MODEL,
    StreamResult,
    capped_parameters,
    choose_model,
    estimate_tokens,
    load_env,
    loaded_instances_from_payload,
    runtime_metadata,
    safe_json,
)

TEST_TITLE = "StoryDriver LM Studio UI Parity Test"
DIRECTOR_NOTE = (
    "Scene: Write a grounded no-magic medieval scene in which three sisters at a farm table decide whether "
    "to rescue a captured child from a bandit camp. Keep it prose only."
)
MINIMAL_USER_PROMPT = (
    "Write one grounded medieval prose paragraph about rain on a farm road. "
    "No headings, no explanation, prose only."
)
MINIMAL_SYSTEM_PROMPT = "You are a fast local fiction prose generator. Return prose only."
DIRECT_SYSTEM_PROMPT = (
    "You write narrated fiction prose only. Follow the user's scene instruction directly. "
    "Do not explain, plan aloud, ask questions, or add assistant-style framing."
)


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def local_create_session(title: str) -> str:
    session_id = str(uuid4())
    with db_session() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, title))
    return session_id


def insert_seed_scene(session_id: str, note: str, text: str) -> None:
    scene_id = str(uuid4())
    version_id = str(uuid4())
    with db_session() as db:
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, generation_stats_json, mode)
            VALUES (?, ?, ?, ?, '{}', 'continue')
            """,
            (scene_id, session_id, note, text),
        )
        db.execute(
            """
            INSERT INTO scene_versions (
                id, scene_id, session_id, director_note, generated_text, generation_stats_json, mode, version_index
            )
            VALUES (?, ?, ?, ?, ?, '{}', 'continue', 1)
            """,
            (version_id, scene_id, session_id, note, text),
        )


def seed_context(session_id: str) -> None:
    insert_seed_scene(
        session_id,
        "Diagnostic seed: farm at dusk.",
        (
            "The farm stood outside the town wall where the road became ruts and hedge. Elara kept a kitchen "
            "knife under her sleeve while Maren barred the stable door and Tess listened to rain tick in the "
            "eaves. They had survived streets and hunger together, and the news of a captured child made the "
            "room feel smaller."
        ),
    )
    insert_seed_scene(
        session_id,
        "Diagnostic seed: no-magic rule and bandit threat.",
        (
            "No prayer or charm had ever moved a stone in their world. People survived by wit, hands, and luck. "
            "The bandits on the old quarry road had taken a merchant's daughter, and every neighbor was suddenly "
            "too practical to help."
        ),
    )


def prompt_text_from_messages(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(message.get("content") or "") for message in messages)


def sanitized_payload(body: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in body.items()
        if key.lower() not in {"api_key", "authorization", "token", "access_token"}
    }


def payload_with_cap(body: dict[str, Any], max_output_tokens: int, use_configured_max_tokens: bool) -> tuple[dict[str, Any], int | None]:
    capped = deepcopy(body)
    configured = capped.get("max_tokens")
    configured_int = int(configured) if isinstance(configured, int | float) or str(configured or "").isdigit() else None
    if not use_configured_max_tokens:
        capped["max_tokens"] = min(configured_int or max_output_tokens, max_output_tokens)
    capped["stream"] = True
    return capped, configured_int


def stream_payload(
    *,
    label: str,
    base_url: str,
    body: dict[str, Any],
    configured_max_tokens: int | None,
    timeout: float,
) -> StreamResult:
    started = time.perf_counter()
    headers_at: float | None = None
    first_any: float | None = None
    first_reasoning: float | None = None
    first_content: float | None = None
    chunks = 0
    reasoning_chunks = 0
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, Any] | None = None
    prompt_text = prompt_text_from_messages(body.get("messages") or [])

    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            method="POST",
            data=json.dumps(sanitized_payload(body)).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            headers_at = time.perf_counter()
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
                if isinstance(payload.get("usage"), dict):
                    usage = payload["usage"]
                delta = payload.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content")
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if content:
                    chunks += 1
                    content_parts.append(str(content))
                    if first_any is None:
                        first_any = time.perf_counter()
                    if first_content is None:
                        first_content = time.perf_counter()
                    continue
                if reasoning:
                    reasoning_chunks += 1
                    reasoning_parts.append(str(reasoning))
                    if first_any is None:
                        first_any = time.perf_counter()
                    if first_reasoning is None:
                        first_reasoning = time.perf_counter()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        return StreamResult(label=label, ok=False, error=f"HTTP {error.code}: {detail[:800]}", model=str(body.get("model") or ""))
    except Exception as error:  # noqa: BLE001
        return StreamResult(label=label, ok=False, error=str(error), model=str(body.get("model") or ""))

    total = time.perf_counter() - started
    output_text = "".join(content_parts)
    reasoning_text = "".join(reasoning_parts)
    output_tokens = estimate_tokens(output_text) if output_text else 0
    reasoning_tokens = estimate_tokens(reasoning_text) if reasoning_text else 0
    raw_tokens = output_tokens + reasoning_tokens
    first_any_seconds = first_any - started if first_any else None
    first_reasoning_seconds = first_reasoning - started if first_reasoning else None
    first_content_seconds = first_content - started if first_content else None
    raw_seconds = total - first_any_seconds if first_any_seconds and total > first_any_seconds else total
    visible_seconds = total - first_content_seconds if first_content_seconds and total > first_content_seconds else total
    raw_tps = raw_tokens / raw_seconds if raw_tokens and raw_seconds > 0 else None
    visible_tps = output_tokens / visible_seconds if output_tokens and visible_seconds > 0 else None

    return StreamResult(
        label=label,
        ok=True,
        model=str(body.get("model") or ""),
        prompt_chars=len(prompt_text),
        prompt_tokens_estimated=estimate_tokens(prompt_text),
        configured_max_tokens=configured_max_tokens,
        diagnostic_max_tokens=int(body.get("max_tokens") or 0) or None,
        request_to_headers_seconds=round(headers_at - started, 3) if headers_at else None,
        first_any_seconds=round(first_any_seconds, 3) if first_any_seconds is not None else None,
        first_reasoning_seconds=round(first_reasoning_seconds, 3) if first_reasoning_seconds is not None else None,
        first_content_seconds=round(first_content_seconds, 3) if first_content_seconds is not None else None,
        total_seconds=round(total, 3),
        output_chars=len(output_text),
        output_tokens_estimated=output_tokens,
        reasoning_chars=len(reasoning_text),
        reasoning_tokens_estimated=reasoning_tokens,
        tokens_per_second_estimated=round(raw_tps, 2) if raw_tps else None,
        visible_tokens_per_second_estimated=round(visible_tps, 2) if visible_tps else None,
        chunks=chunks,
        reasoning_chunks=reasoning_chunks,
        usage=usage,
        parameters={key: body.get(key) for key in ("temperature", "top_p", "max_tokens", "seed", "presence_penalty", "frequency_penalty", "stop") if key in body},
    )


def build_storydriver_payloads(
    *,
    model: str,
    model_settings: Any,
    resolved_model: Any,
    max_output_tokens: int,
    use_configured_max_tokens: bool,
) -> dict[str, Any]:
    session_id = local_create_session(f"{TEST_TITLE} {time.strftime('%H%M%S')}")
    seed_context(session_id)
    prepared = prepare_generation(session_id, GenerateSceneRequest(director_note=DIRECTOR_NOTE, mode="continue"))
    prompt_mode = getattr(model_settings, "prose_prompt_mode", "standard") or "standard"
    task_notes = prose_task_notes_for_prompt(resolved_model.notes, prompt_mode)
    full_prompt = build_scene_prompt(
        session_id=session_id,
        director_note=prepared["director_note"],
        mode="continue",
        recent_scenes=prepared["recent_scenes"],
        session_summary=prepared["session_summary"],
        target_scene=prepared["target_scene"],
        task_notes=task_notes,
        writing_length=prepared["writing_length"],
    )
    no_notes_prompt = build_scene_prompt(
        session_id=session_id,
        director_note=prepared["director_note"],
        mode="continue",
        recent_scenes=prepared["recent_scenes"],
        session_summary=prepared["session_summary"],
        target_scene=prepared["target_scene"],
        task_notes="",
        writing_length=prepared["writing_length"],
    )
    direct_notes = prose_task_notes_for_prompt(resolved_model.notes, "direct")
    direct_prompt = build_scene_prompt(
        session_id=session_id,
        director_note=prepared["director_note"],
        mode="continue",
        recent_scenes=prepared["recent_scenes"],
        session_summary=prepared["session_summary"],
        target_scene=prepared["target_scene"],
        task_notes=direct_notes,
        writing_length=prepared["writing_length"],
    )
    writing_length = resolve_writing_length(director_note=DIRECTOR_NOTE)
    parameters = generation_parameters(model_settings, prepared["writing_length"] or writing_length)
    diagnostic_params, configured_max_tokens = capped_parameters(parameters, max_output_tokens, use_configured_max_tokens)
    exact_body = lmstudio_chat_payload(
        model=model,
        system_prompt=model_settings.system_prompt,
        user_prompt=full_prompt,
        parameters=diagnostic_params,
        stream=True,
    )
    no_notes_body = lmstudio_chat_payload(
        model=model,
        system_prompt=model_settings.system_prompt,
        user_prompt=no_notes_prompt,
        parameters=diagnostic_params,
        stream=True,
    )
    direct_body = lmstudio_chat_payload(
        model=model,
        system_prompt=DIRECT_SYSTEM_PROMPT,
        user_prompt=direct_prompt,
        parameters=diagnostic_params,
        stream=True,
    )
    reduced_body = deepcopy(exact_body)
    reduced_body["max_tokens"] = min(int(reduced_body.get("max_tokens") or max_output_tokens), max(96, max_output_tokens // 2))
    prompt_diag = build_prompt_diagnostics(
        system_prompt=model_settings.system_prompt,
        user_prompt=full_prompt,
        task_notes=task_notes,
        director_note=prepared["director_note"],
        session_summary=prepared["session_summary"],
        recent_scenes=prepared["recent_scenes"],
    )
    write_prose_debug_files(
        session_id=session_id,
        mode="continue",
        model=model,
        task_profile=task_type_for_generation_mode("continue"),
        task_label=resolved_model.label,
        system_prompt=model_settings.system_prompt,
        user_prompt=full_prompt,
        task_notes=task_notes,
        director_note=prepared["director_note"],
        writing_length=prepared["writing_length"],
        parameters=diagnostic_params,
        prompt_diagnostics=prompt_diag,
        resolved_timeout_seconds=resolved_model.timeout_seconds,
        streaming=True,
        prompt_mode=prompt_mode,
    )
    return {
        "session_id": session_id,
        "prompt_mode": prompt_mode,
        "task_notes": task_notes,
        "full_prompt": full_prompt,
        "prompt_diag": prompt_diag,
        "parameters": parameters,
        "diagnostic_params": diagnostic_params,
        "configured_max_tokens": configured_max_tokens,
        "exact_body": exact_body,
        "no_notes_body": no_notes_body,
        "direct_body": direct_body,
        "reduced_body": reduced_body,
    }


def minimal_body(*, model: str, messages: list[dict[str, str]], parameters: dict[str, Any]) -> dict[str, Any]:
    body = {"model": model, "messages": messages, "stream": True}
    body.update({key: value for key, value in parameters.items() if value is not None and value != ""})
    return body


def format_result(result: StreamResult) -> list[str]:
    def value(item: Any, suffix: str = "") -> str:
        return f"{item}{suffix}" if item is not None else "none"

    if not result.ok:
        return [f"### {result.label}", f"- FAILED: {result.error}", ""]
    warning = (
        "- Warning: hidden reasoning consumed the diagnostic token budget before visible prose."
        if result.reasoning_chars and not result.output_chars
        else ""
    )
    return [
        f"### {result.label}",
        f"- Prompt size: {result.prompt_chars:,} chars / {result.prompt_tokens_estimated:,} estimated tokens",
        f"- First stream event: {value(result.first_any_seconds, 's')}",
        f"- First reasoning: {value(result.first_reasoning_seconds, 's')}",
        f"- First visible prose: {value(result.first_content_seconds, 's')}",
        f"- Total wall time: {value(result.total_seconds, 's')}",
        f"- Hidden reasoning: {result.reasoning_chars:,} chars / {result.reasoning_tokens_estimated:,} estimated tokens / {result.reasoning_chunks} chunks",
        f"- Visible prose: {result.output_chars:,} chars / {result.output_tokens_estimated:,} estimated tokens / {result.chunks} chunks",
        f"- Raw stream speed: {value(result.tokens_per_second_estimated)} est. tok/s",
        f"- Visible prose speed: {value(result.visible_tokens_per_second_estimated)} est. tok/s",
        f"- Max tokens: configured {value(result.configured_max_tokens)}, diagnostic {value(result.diagnostic_max_tokens)}",
        warning,
        "",
    ]


def fastest_visible(results: list[StreamResult]) -> StreamResult | None:
    visible = [result for result in results if result.ok and result.first_content_seconds is not None]
    return min(visible, key=lambda result: float(result.first_content_seconds or 999999)) if visible else None


def build_report(
    *,
    openai_url: str,
    rest_url: str,
    model: str,
    model_settings: Any,
    resolved_model: Any,
    rest_payload: Any,
    rest_error: str | None,
    runtime: dict[str, Any],
    loaded_instances: list[dict[str, Any]],
    prompt_bundle: dict[str, Any],
    results: list[StreamResult],
) -> str:
    fastest = fastest_visible(results)
    exact_result = next((result for result in results if result.label == "D_exact_storydriver_payload"), None)
    direct_result = next((result for result in results if result.label == "F_exact_context_direct_system_priority"), None)
    minimal_result = next((result for result in results if result.label == "A_minimal_user_only"), None)
    no_notes_result = next((result for result in results if result.label == "E_exact_context_without_task_notes"), None)
    any_visible = fastest is not None

    notes_trigger_reasoning = False
    if exact_result and no_notes_result and exact_result.ok and no_notes_result.ok:
        exact_reasoning = exact_result.reasoning_chars or 0
        no_notes_reasoning = no_notes_result.reasoning_chars or 0
        notes_trigger_reasoning = exact_reasoning > max(600, no_notes_reasoning * 1.25)

    exact_causes_delay = bool(
        exact_result
        and exact_result.ok
        and (
            (exact_result.first_content_seconds and exact_result.first_content_seconds > 8)
            or (exact_result.reasoning_chars and not exact_result.output_chars)
        )
    )
    direct_improves = bool(
        exact_result
        and direct_result
        and exact_result.ok
        and direct_result.ok
        and exact_result.first_content_seconds
        and direct_result.first_content_seconds
        and direct_result.first_content_seconds < exact_result.first_content_seconds * 0.75
    )

    lines = [
        "# LM Studio UI Parity Report",
        "",
        f"Updated: {utc_stamp()}",
        "",
        "## Summary",
        "",
        f"- Model tested: `{model}`",
        f"- Expected fast model: `{GEMMA_MODEL}`",
        f"- Prose task profile: `{resolved_model.task_type}` / {resolved_model.label}",
        f"- Prose prompt mode in StoryDriver: `{prompt_bundle.get('prompt_mode')}`",
        f"- LM Studio OpenAI URL: `{openai_url}`",
        f"- LM Studio REST URL: `{rest_url}`",
        f"- Exact payload debug file: `{PAYLOAD_DEBUG_PATH}`",
        "",
        "## Loaded Runtime Metadata",
        "",
        f"- REST reachable: {'yes' if rest_error is None else 'no'}",
        f"- Loaded LM Studio instances: {len(loaded_instances)}",
    ]
    for instance in loaded_instances[:8]:
        lines.append(
            f"  - `{instance.get('display_name') or instance.get('model_key') or instance.get('id')}` (`{instance.get('id')}`)"
        )
    if rest_error:
        lines.append(f"- REST error: {rest_error}")
    lines.extend(
        [
            f"- Runtime metadata visible: {'yes' if runtime.get('runtime_info_visible') else 'no'}",
            f"- GPU/offload info visible: {'yes' if runtime.get('gpu_offload_visible') else 'no'}",
            f"- Context length visible: {'yes' if runtime.get('context_length_visible') else 'no'}",
            f"- API reasoning/template controls exposed: no documented supported control detected",
            f"- API GPU/offload controls exposed: {'yes' if runtime.get('api_runtime_controls_exposed') else 'no'}",
            f"- Control note: {runtime.get('control_note')}",
        ]
    )
    if runtime.get("fields"):
        lines.append("- Runtime-like fields found:")
        for field in runtime["fields"][:18]:
            lines.append(f"  - `{field['path']}` = `{field['value']}`")
    lines.extend(
        [
            "",
            "## Prompt / Request Size",
            "",
            f"- System prompt chars: {prompt_bundle['prompt_diag'].get('system_prompt_chars')}",
            f"- Task notes chars: {prompt_bundle['prompt_diag'].get('task_notes_chars')}",
            f"- Recent scenes chars: {prompt_bundle['prompt_diag'].get('recent_scenes_chars')}",
            f"- Story state chars: {prompt_bundle['prompt_diag'].get('story_state_chars')}",
            f"- Total prompt chars: {prompt_bundle['prompt_diag'].get('total_prompt_chars')}",
            f"- Estimated prompt tokens: {prompt_bundle['prompt_diag'].get('prompt_estimated_tokens')}",
            f"- Temperature/top_p/max_tokens diagnostic: {prompt_bundle['diagnostic_params'].get('temperature')} / {prompt_bundle['diagnostic_params'].get('top_p')} / {prompt_bundle['diagnostic_params'].get('max_tokens')}",
            "",
            "## Variant Matrix",
            "",
            "- A: Minimal user-only prompt.",
            "- B: Same prompt with StoryDriver system prompt only.",
            "- C: Same prompt with StoryDriver system prompt plus task notes.",
            "- D: Exact StoryDriver message payload.",
            "- E: Exact StoryDriver context but without task notes.",
            "- F: Exact StoryDriver context with a minimal direct system prompt and direct task note.",
            "- G: Exact StoryDriver payload with reduced max_tokens.",
            "- H: Reasoning/template optional fields were not sent because no supported LM Studio API control was detected.",
            "",
            "## Results",
            "",
        ]
    )
    for result in results:
        lines.extend(format_result(result))

    lines.extend(
        [
            "## Findings",
            "",
            f"1. StoryDriver API behavior matches LM Studio UI behavior: {'no - API emitted hidden reasoning before visible prose for every tested variant' if not any_visible else ('not confirmed' if exact_causes_delay else 'closer in this capped API test')}",
            f"2. Fastest first visible prose variant: `{fastest.label if fastest else 'none produced visible prose before cap'}`",
            f"3. Task notes/system prompt trigger extra reasoning: {'likely' if notes_trigger_reasoning else 'not clearly isolated'}",
            f"4. Exact StoryDriver payload causes reasoning delay: {'yes' if exact_causes_delay else 'not in this run'}",
            "5. API reload differs from manual LM Studio load: not automatically mutated by this test. Use the image performance smoke test to compare after Auto Image Priority reload.",
            "6. Supported API field to reduce reasoning: none detected or sent. Thinking/template behavior appears managed in LM Studio UI for this local API surface.",
            "7. Settings that must be managed manually in LM Studio: preset/template/thinking behavior and any GPU/offload controls not exposed by REST metadata.",
            "",
            "## Recommendation",
            "",
        ]
    )
    if not any_visible and minimal_result and minimal_result.reasoning_chars:
        lines.append("- The API path is forcing hidden reasoning even for a minimal user-only request. StoryDriver cannot fully fix that without a supported LM Studio setting/API control that disables or shortens reasoning for server requests.")
    elif direct_improves:
        lines.append("- Use StoryDriver's Direct / system-priority prose prompt mode for fastest visible prose; it materially improved first visible output in this run.")
    elif fastest and fastest.label != "D_exact_storydriver_payload":
        lines.append(f"- The fastest visible variant was `{fastest.label}`. Use that as the next manual comparison against the LM Studio UI.")
    else:
        lines.append("- The exact StoryDriver payload was not worse than the alternatives in this capped run; compare LM Studio UI template/thinking settings next.")
    lines.extend(
        [
            "- For maximum UI parity, manually load Gemma in LM Studio with the fast preset/template, keep only that model loaded, then use Preserve manual LM Studio load after image jobs.",
            "- If hidden `reasoning_content` still appears before prose for minimal user-only API prompts, StoryDriver cannot fully fix it without a supported LM Studio reasoning/template API field.",
            "- Keep separate speed labels: visible prose speed is what the reader sees; raw stream speed includes hidden reasoning and can look much faster.",
            "",
            "## What StoryDriver Changed",
            "",
            "- Saves the exact local prose request payload to `last_lmstudio_prose_request_payload.json`.",
            "- Adds Direct / system-priority prose prompt mode to reduce prompt-induced reasoning pressure.",
            "- Shows first visible prose, visible t/s, reasoning output, and raw stream t/s separately.",
            "- Adds LM Studio UI parity guidance in Settings/Diagnostics.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare StoryDriver LM Studio API payload variants against the fast LM Studio UI behavior.")
    parser.add_argument("--max-output-tokens", type=int, default=320)
    parser.add_argument("--use-configured-max-tokens", action="store_true")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    load_env(ROOT / "backend" / ".env")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    model_settings, resolved_model = resolve_task_model_settings("prose_generation")
    _global_settings = load_model_settings(resolve_active_preset=True)
    image_settings = load_image_settings()
    openai_url = model_settings.lm_studio_url.rstrip("/")
    rest_url = settings.lm_studio_rest_base_url.rstrip("/")
    model = choose_model(openai_url, model_settings.model)

    writing_length = resolve_writing_length(director_note=DIRECTOR_NOTE)
    base_parameters = generation_parameters(model_settings, writing_length)
    diagnostic_params, configured_max_tokens = capped_parameters(
        base_parameters,
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

    minimal_user_only = minimal_body(
        model=model,
        messages=[{"role": "user", "content": MINIMAL_USER_PROMPT}],
        parameters=diagnostic_params,
    )
    storydriver_system_only = minimal_body(
        model=model,
        messages=[
            {"role": "system", "content": model_settings.system_prompt},
            {"role": "user", "content": MINIMAL_USER_PROMPT},
        ],
        parameters=diagnostic_params,
    )
    storydriver_system_with_notes = minimal_body(
        model=model,
        messages=[
            {"role": "system", "content": f"{model_settings.system_prompt}\n\nTASK NOTES:\n{resolved_model.notes}"},
            {"role": "user", "content": MINIMAL_USER_PROMPT},
        ],
        parameters=diagnostic_params,
    )

    exact_body, exact_configured = payload_with_cap(
        prompt_bundle["exact_body"],
        args.max_output_tokens,
        args.use_configured_max_tokens,
    )
    no_notes_body, no_notes_configured = payload_with_cap(
        prompt_bundle["no_notes_body"],
        args.max_output_tokens,
        args.use_configured_max_tokens,
    )
    direct_body, direct_configured = payload_with_cap(
        prompt_bundle["direct_body"],
        args.max_output_tokens,
        args.use_configured_max_tokens,
    )
    reduced_body = deepcopy(prompt_bundle["reduced_body"])
    reduced_body["stream"] = True

    results = [
        stream_payload(
            label="A_minimal_user_only",
            base_url=openai_url,
            body=minimal_user_only,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_payload(
            label="B_minimal_with_storydriver_system",
            base_url=openai_url,
            body=storydriver_system_only,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_payload(
            label="C_minimal_with_system_and_task_notes",
            base_url=openai_url,
            body=storydriver_system_with_notes,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_payload(
            label="D_exact_storydriver_payload",
            base_url=openai_url,
            body=exact_body,
            configured_max_tokens=exact_configured,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_payload(
            label="E_exact_context_without_task_notes",
            base_url=openai_url,
            body=no_notes_body,
            configured_max_tokens=no_notes_configured,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_payload(
            label="F_exact_context_direct_system_priority",
            base_url=openai_url,
            body=direct_body,
            configured_max_tokens=direct_configured,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_payload(
            label="G_exact_payload_reduced_max_tokens",
            base_url=openai_url,
            body=reduced_body,
            configured_max_tokens=prompt_bundle["configured_max_tokens"],
            timeout=resolved_model.timeout_seconds,
        ),
    ]

    rest_payload, rest_error = safe_json(f"{rest_url}/models", timeout=15)
    runtime = runtime_metadata(rest_payload)
    loaded_instances = loaded_instances_from_payload(rest_payload)
    report = build_report(
        openai_url=openai_url,
        rest_url=rest_url,
        model=model,
        model_settings=model_settings,
        resolved_model=resolved_model,
        rest_payload=rest_payload,
        rest_error=rest_error,
        runtime=runtime,
        loaded_instances=loaded_instances,
        prompt_bundle=prompt_bundle,
        results=results,
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
