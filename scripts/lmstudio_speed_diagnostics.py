from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "LM_STUDIO_SPEED_DIAGNOSTICS.md"
LATENCY_REPORT_PATH = ROOT / "backend" / "data" / "logs" / "LATENCY_OPTIMIZATION_REPORT.md"
DIAGNOSTIC_SESSION_TITLE = "StoryDriver LM Speed Diagnostics"
GEMMA_MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"

sys.path.insert(0, str(BACKEND_DIR))

from app.utils.openssl_dlls import add_openssl_dll_directory  # noqa: E402

add_openssl_dll_directory()

from app.config import settings  # noqa: E402
from app.database import db_session  # noqa: E402
from app.routes.sessions import (  # noqa: E402
    build_prompt_diagnostics,
    generation_parameters,
    prepare_generation,
)
from app.schemas import GenerateSceneRequest  # noqa: E402
from app.generation.router import resolve_task_model_settings  # noqa: E402
from app.generation.prompt_builder import build_scene_prompt, resolve_writing_length  # noqa: E402
from app.settings.store import load_image_settings, load_model_settings  # noqa: E402


def estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 3) // 4)


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))


def safe_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 12) -> tuple[Any, str | None]:
    try:
        return request_json(url, method=method, payload=payload, timeout=timeout), None
    except Exception as error:  # noqa: BLE001
        return None, str(error)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class StreamResult:
    label: str
    ok: bool
    error: str | None = None
    model: str = ""
    prompt_chars: int = 0
    prompt_tokens_estimated: int = 0
    configured_max_tokens: int | None = None
    diagnostic_max_tokens: int | None = None
    request_to_headers_seconds: float | None = None
    first_any_seconds: float | None = None
    first_reasoning_seconds: float | None = None
    first_content_seconds: float | None = None
    total_seconds: float | None = None
    output_chars: int = 0
    output_tokens_estimated: int = 0
    reasoning_chars: int = 0
    reasoning_tokens_estimated: int = 0
    tokens_per_second_estimated: float | None = None
    visible_tokens_per_second_estimated: float | None = None
    chunks: int = 0
    reasoning_chunks: int = 0
    usage: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ok": self.ok,
            "error": self.error,
            "model": self.model,
            "prompt_chars": self.prompt_chars,
            "prompt_tokens_estimated": self.prompt_tokens_estimated,
            "configured_max_tokens": self.configured_max_tokens,
            "diagnostic_max_tokens": self.diagnostic_max_tokens,
            "request_to_headers_seconds": self.request_to_headers_seconds,
            "first_any_seconds": self.first_any_seconds,
            "first_reasoning_seconds": self.first_reasoning_seconds,
            "first_content_seconds": self.first_content_seconds,
            "total_seconds": self.total_seconds,
            "output_chars": self.output_chars,
            "output_tokens_estimated": self.output_tokens_estimated,
            "reasoning_chars": self.reasoning_chars,
            "reasoning_tokens_estimated": self.reasoning_tokens_estimated,
            "tokens_per_second_estimated": self.tokens_per_second_estimated,
            "visible_tokens_per_second_estimated": self.visible_tokens_per_second_estimated,
            "chunks": self.chunks,
            "reasoning_chunks": self.reasoning_chunks,
            "usage": self.usage,
            "parameters": self.parameters,
        }


def runtime_metadata(payload: Any) -> dict[str, Any]:
    keywords = (
        "gpu",
        "offload",
        "vram",
        "context",
        "ctx",
        "runtime",
        "reasoning",
        "thinking",
        "backend",
        "device",
        "flash",
        "kv",
        "load",
        "quant",
    )
    fields: list[dict[str, Any]] = []

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if depth > 6 or len(fields) >= 80:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if any(word in str(key).lower() for word in keywords) and not isinstance(child, (dict, list)):
                    fields.append({"path": next_path, "value": child})
                visit(child, next_path, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:12]):
                visit(child, f"{path}[{index}]", depth + 1)

    visit(payload, "")
    lower_paths = [field["path"].lower() for field in fields]
    return {
        "fields": fields[:40],
        "gpu_offload_visible": any("gpu" in path or "offload" in path for path in lower_paths),
        "context_length_visible": any("context" in path or "ctx" in path for path in lower_paths),
        "runtime_info_visible": bool(fields),
        "api_runtime_controls_exposed": False,
        "control_note": "No documented GPU/offload load controls were assumed. Set GPU/offload/context in LM Studio before loading the model unless your local API exposes official controls.",
    }


def loaded_instances_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    instances: list[dict[str, Any]] = []
    raw_top = payload.get("loaded_instances") or payload.get("loadedInstances") or []
    if isinstance(raw_top, dict):
        raw_top = [raw_top]
    if isinstance(raw_top, list):
        instances.extend(item for item in raw_top if isinstance(item, dict))
    models = payload.get("data") or payload.get("models") or payload.get("items") or []
    if isinstance(models, dict):
        models = [models]
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            raw = model.get("loaded_instances") or model.get("loadedInstances") or model.get("instances") or []
            if isinstance(raw, dict):
                raw = [raw]
            if not isinstance(raw, list):
                continue
            for instance in raw:
                if isinstance(instance, dict):
                    item = dict(instance)
                    item.setdefault("model_key", model.get("id") or model.get("key") or model.get("model_key"))
                    item.setdefault("display_name", model.get("name") or model.get("display_name") or item.get("model_key"))
                    instances.append(item)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for instance in instances:
        instance_id = str(instance.get("id") or instance.get("instance_id") or instance.get("instanceId") or "").strip()
        if not instance_id or instance_id in seen:
            continue
        seen.add(instance_id)
        unique.append(instance)
    return unique


def choose_model(openai_base_url: str, configured_model: str) -> str:
    if configured_model.strip():
        return configured_model.strip()
    payload = request_json(f"{openai_base_url.rstrip('/')}/models", timeout=12)
    data = payload.get("data") if isinstance(payload, dict) else []
    first = next((item.get("id") for item in data if isinstance(item, dict) and item.get("id")), None)
    if not first:
        raise RuntimeError("LM Studio /models did not return a usable model id.")
    return str(first)


def capped_parameters(parameters: dict[str, Any], max_output_tokens: int, use_configured_max_tokens: bool) -> tuple[dict[str, Any], int | None]:
    configured = parameters.get("max_tokens")
    if use_configured_max_tokens:
        return dict(parameters), int(configured) if configured else None
    capped = dict(parameters)
    if configured:
        capped["max_tokens"] = min(int(configured), max_output_tokens)
    else:
        capped["max_tokens"] = max_output_tokens
    return capped, int(configured) if configured else None


def stream_lmstudio_chat(
    *,
    label: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    parameters: dict[str, Any],
    configured_max_tokens: int | None,
    timeout: float,
) -> StreamResult:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
    }
    body.update({key: value for key, value in parameters.items() if value is not None and value != ""})
    started = time.perf_counter()
    output_parts: list[str] = []
    reasoning_parts: list[str] = []
    chunks = 0
    reasoning_chunks = 0
    usage: dict[str, Any] | None = None
    first_any: float | None = None
    first_reasoning: float | None = None
    first_content: float | None = None
    headers_at: float | None = None
    prompt_text = f"{system_prompt}\n{user_prompt}"
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            method="POST",
            data=json.dumps(body).encode("utf-8"),
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
                if not content:
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        reasoning_chunks += 1
                        if first_any is None:
                            first_any = time.perf_counter()
                        if first_reasoning is None:
                            first_reasoning = time.perf_counter()
                        reasoning_parts.append(str(reasoning))
                    continue
                chunks += 1
                if first_any is None:
                    first_any = time.perf_counter()
                if first_content is None:
                    first_content = time.perf_counter()
                output_parts.append(str(content))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        return StreamResult(label=label, ok=False, error=f"HTTP {error.code}: {detail[:800]}", model=model)
    except Exception as error:  # noqa: BLE001
        return StreamResult(label=label, ok=False, error=str(error), model=model)

    total = time.perf_counter() - started
    output_text = "".join(output_parts)
    reasoning_text = "".join(reasoning_parts)
    output_tokens = usage.get("completion_tokens") if usage else None
    estimated_tokens = estimate_tokens(output_text) if output_text else 0
    reasoning_tokens = estimate_tokens(reasoning_text) if reasoning_text else 0
    token_count = int(output_tokens) if isinstance(output_tokens, int) and output_tokens > 0 else estimated_tokens + reasoning_tokens
    first_content_seconds = first_content - started if first_content else None
    first_any_seconds = first_any - started if first_any else None
    first_reasoning_seconds = first_reasoning - started if first_reasoning else None
    generated_time = total - first_any_seconds if first_any_seconds and total > first_any_seconds else total
    visible_generated_time = (
        total - first_content_seconds if first_content_seconds and total > first_content_seconds else total
    )
    tokens_per_second = token_count / generated_time if generated_time > 0 else None
    visible_tokens_per_second = estimated_tokens / visible_generated_time if estimated_tokens > 0 and visible_generated_time > 0 else None
    return StreamResult(
        label=label,
        ok=True,
        model=model,
        prompt_chars=len(prompt_text),
        prompt_tokens_estimated=estimate_tokens(prompt_text),
        configured_max_tokens=configured_max_tokens,
        diagnostic_max_tokens=int(parameters.get("max_tokens") or 0) or None,
        request_to_headers_seconds=round(headers_at - started, 3) if headers_at else None,
        first_any_seconds=round(first_any_seconds, 3) if first_any_seconds is not None else None,
        first_reasoning_seconds=round(first_reasoning_seconds, 3) if first_reasoning_seconds is not None else None,
        first_content_seconds=round(first_content_seconds, 3) if first_content_seconds is not None else None,
        total_seconds=round(total, 3),
        output_chars=len(output_text),
        output_tokens_estimated=estimated_tokens,
        reasoning_chars=len(reasoning_text),
        reasoning_tokens_estimated=reasoning_tokens,
        tokens_per_second_estimated=round(tokens_per_second, 2) if tokens_per_second else None,
        visible_tokens_per_second_estimated=round(visible_tokens_per_second, 2) if visible_tokens_per_second else None,
        chunks=chunks,
        reasoning_chunks=reasoning_chunks,
        usage=usage,
        parameters=parameters,
    )


def backend_json(base_url: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 30) -> Any:
    return request_json(f"{base_url.rstrip('/')}{path}", method=method, payload=payload, timeout=timeout)


def get_or_create_diagnostic_session(backend_url: str) -> str:
    sessions = backend_json(backend_url, "/sessions", timeout=20)
    if isinstance(sessions, list):
        existing = next((item for item in sessions if item.get("title") == DIAGNOSTIC_SESSION_TITLE), None)
        if existing:
            return str(existing["id"])
    created = backend_json(
        backend_url,
        "/sessions",
        method="POST",
        payload={"title": DIAGNOSTIC_SESSION_TITLE},
        timeout=20,
    )
    return str(created["id"])


def latest_real_session_id() -> str | None:
    with db_session() as db:
        row = db.execute(
            """
            SELECT id
            FROM sessions
            WHERE title NOT LIKE ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (f"{DIAGNOSTIC_SESSION_TITLE}%",),
        ).fetchone()
    return row["id"] if row else None


def create_diagnostic_session(backend_url: str) -> str:
    created = backend_json(
        backend_url,
        "/sessions",
        method="POST",
        payload={"title": f"{DIAGNOSTIC_SESSION_TITLE} {time.strftime('%H%M%S')}"},
        timeout=20,
    )
    return str(created["id"])


def run_storydriver_generation(backend_url: str, session_id: str) -> dict[str, Any]:
    note = (
        "Beat: Write a grounded medieval prose scene around 350 words. "
        "Two sisters repair a wagon wheel in hard rain while deciding whether to help a captured child. "
        "No magic, no headings, prose only."
    )
    req = urllib.request.Request(
        f"{backend_url.rstrip('/')}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps({"director_note": note, "mode": "continue"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    first_delta: float | None = None
    chunks = 0
    chars = 0
    stages: list[str] = []
    final_scene: dict[str, Any] | None = None
    with urllib.request.urlopen(req, timeout=360) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "status":
                stages.append(event.get("stage") or "")
            elif event.get("type") == "delta":
                chunks += 1
                chars += len(event.get("text") or "")
                if first_delta is None:
                    first_delta = time.perf_counter()
            elif event.get("type") == "scene":
                final_scene = event.get("scene")
            elif event.get("type") == "error":
                raise RuntimeError(event.get("detail") or "StoryDriver generation failed.")
    stats = {}
    if final_scene:
        versions = final_scene.get("versions") or []
        stats = (versions[-1].get("generation_stats") if versions else final_scene.get("generation_stats")) or {}
    return {
        "ok": bool(final_scene),
        "scene_id": final_scene.get("id") if final_scene else None,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "first_delta_seconds": round(first_delta - started, 3) if first_delta else None,
        "chunks": chunks,
        "streamed_chars": chars,
        "stages": stages,
        "generation_stats": stats,
    }


def report_lines(
    *,
    openai_url: str,
    rest_url: str,
    comfy_url: str,
    model: str,
    diagnostics: dict[str, Any],
    rest_payload: Any,
    rest_error: str | None,
    comfy_before: Any,
    comfy_before_error: str | None,
    comfy_free: Any,
    comfy_free_error: str | None,
    comfy_after: Any,
    comfy_after_error: str | None,
    results: list[StreamResult],
    storydriver_result: dict[str, Any] | None,
    prompt_diag: dict[str, Any],
) -> list[str]:
    def seconds(value: float | None) -> str:
        return f"{value}s" if value is not None else "none"

    def number(value: float | None) -> str:
        return str(value) if value is not None else "none"

    runtime = runtime_metadata(rest_payload)
    loaded_instances = loaded_instances_from_payload(rest_payload)
    direct_minimal = next((result for result in results if result.label == "direct_minimal_after_comfy_free"), None) or next(
        (result for result in results if result.label == "direct_minimal"), None
    )
    full_prompt = next((result for result in results if result.label == "direct_full_storydriver_prompt"), None)
    story_stats = (storydriver_result or {}).get("generation_stats") or {}
    likely_bottleneck = "unknown"
    if story_stats.get("reasoning_chars"):
        likely_bottleneck = "LM Studio reasoning/thinking output before visible prose"
    elif direct_minimal and direct_minimal.ok and direct_minimal.reasoning_chars and not direct_minimal.output_chars:
        likely_bottleneck = "LM Studio reasoning/thinking mode consumed the diagnostic output budget before visible prose"
    elif direct_minimal and direct_minimal.ok and storydriver_result and storydriver_result.get("ok"):
        direct_tps = direct_minimal.tokens_per_second_estimated or 0
        story_tps = story_stats.get("tokens_per_second") or 0
        if direct_tps and story_tps and story_tps < direct_tps * 0.5:
            likely_bottleneck = "StoryDriver prompt/context path or background runtime pressure"
        elif direct_tps < 30:
            likely_bottleneck = "LM Studio API/server runtime or VRAM/offload state"
        else:
            likely_bottleneck = "No severe direct API slowdown detected in this run"
    elif direct_minimal and not direct_minimal.ok:
        likely_bottleneck = "LM Studio direct API failed"

    lines = [
        "# LM Studio Speed Diagnostics",
        "",
        f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Configuration",
        "",
        f"- LM Studio OpenAI URL: `{openai_url}`",
        f"- LM Studio REST URL: `{rest_url}`",
        f"- ComfyUI URL: `{comfy_url}`",
        f"- Model tested: `{model}`",
        f"- Expected fast model: `{GEMMA_MODEL}`",
        f"- Active prose profile: `{diagnostics.get('task_profile')}` / {diagnostics.get('task_label')}",
        f"- Configured max tokens: {diagnostics.get('configured_max_tokens')}",
        f"- Diagnostic output cap: {diagnostics.get('diagnostic_max_tokens')}",
        f"- Temperature/top_p: {diagnostics.get('temperature')} / {diagnostics.get('top_p')}",
        f"- Streaming: {diagnostics.get('streaming')}",
        "",
        "## LM Studio Runtime / Loaded Models",
        "",
        f"- REST reachable: {'yes' if rest_error is None else 'no'}",
        f"- Loaded instances: {len(loaded_instances)}",
    ]
    if loaded_instances:
        for instance in loaded_instances[:8]:
            lines.append(
                f"  - `{instance.get('display_name') or instance.get('model_key') or instance.get('id')}` (`{instance.get('id')}`)"
            )
    if rest_error:
        lines.append(f"- REST error: {rest_error}")
    lines.extend(
        [
            f"- Runtime metadata visible: {'yes' if runtime['runtime_info_visible'] else 'no'}",
            f"- GPU/offload info visible: {'yes' if runtime['gpu_offload_visible'] else 'no'}",
            f"- Context length visible: {'yes' if runtime['context_length_visible'] else 'no'}",
            f"- GPU/offload controls through API: {'yes' if runtime['api_runtime_controls_exposed'] else 'no'}",
            f"- Runtime note: {runtime['control_note']}",
        ]
    )
    if runtime["fields"]:
        lines.append("- Runtime-like fields found:")
        for field in runtime["fields"][:12]:
            lines.append(f"  - `{field['path']}` = `{field['value']}`")
    lines.extend(
        [
            "",
            "## ComfyUI Idle / Free",
            "",
            f"- System stats before /free: {'ok' if comfy_before_error is None else comfy_before_error}",
            f"- /free result: {'ok' if comfy_free_error is None else comfy_free_error}",
            f"- System stats after /free: {'ok' if comfy_after_error is None else comfy_after_error}",
            "",
            "## Prompt Size",
            "",
            f"- System prompt chars: {prompt_diag.get('system_prompt_chars')}",
            f"- Task notes chars: {prompt_diag.get('task_notes_chars')}",
            f"- Story state chars: {prompt_diag.get('story_state_chars')}",
            f"- Active characters chars: {prompt_diag.get('active_characters_chars')}",
            f"- World state chars: {prompt_diag.get('world_state_chars')}",
            f"- Summary chars: {prompt_diag.get('summary_chars')}",
            f"- Recent scenes chars: {prompt_diag.get('recent_scenes_chars')}",
            f"- Director note chars: {prompt_diag.get('director_note_chars')}",
            f"- Total prompt chars: {prompt_diag.get('total_prompt_chars')}",
            f"- Estimated prompt tokens: {prompt_diag.get('prompt_estimated_tokens')}",
        ]
    )
    warnings = prompt_diag.get("prompt_size_warnings") or []
    lines.extend(f"- Prompt warning: {warning}" for warning in warnings)
    lines.extend(["", "## Direct LM Studio API Tests", ""])
    for result in results:
        if result.ok:
            no_visible_warning = (
                "- Warning: this run produced hidden reasoning but no visible `content` before the diagnostic token cap."
                if result.reasoning_chars and not result.output_chars
                else ""
            )
            lines.extend(
                [
                    f"### {result.label}",
                    f"- Prompt: {result.prompt_chars} chars / {result.prompt_tokens_estimated} est. tokens",
                    f"- First stream event: {seconds(result.first_any_seconds)}",
                    f"- First reasoning: {seconds(result.first_reasoning_seconds)}",
                    f"- First visible content: {seconds(result.first_content_seconds)}",
                    f"- Total: {seconds(result.total_seconds)}",
                    f"- Visible output: {result.output_tokens_estimated} est. tokens / {result.output_chars} chars / {result.chunks} chunks",
                    f"- Hidden reasoning: {result.reasoning_tokens_estimated} est. tokens / {result.reasoning_chars} chars / {result.reasoning_chunks} chunks",
                    f"- Raw stream speed: {result.tokens_per_second_estimated} est. tok/s",
                    f"- Visible prose speed: {number(result.visible_tokens_per_second_estimated)} est. tok/s",
                    f"- Max tokens: configured {result.configured_max_tokens}, diagnostic {result.diagnostic_max_tokens}",
                    no_visible_warning,
                    "",
                ]
            )
        else:
            lines.extend([f"### {result.label}", f"- FAILED: {result.error}", ""])
    lines.extend(["## StoryDriver Route Test", ""])
    if storydriver_result:
        lines.extend(
            [
                f"- OK: {'yes' if storydriver_result.get('ok') else 'no'}",
                f"- Scene: `{storydriver_result.get('scene_id')}`",
                f"- First delta: {storydriver_result.get('first_delta_seconds')}s",
                f"- Wall time: {storydriver_result.get('wall_seconds')}s",
                f"- Stream: {storydriver_result.get('chunks')} chunks / {storydriver_result.get('streamed_chars')} chars",
                f"- Stages: {', '.join(storydriver_result.get('stages') or [])}",
                f"- Saved stats: `{json.dumps(story_stats, ensure_ascii=False)[:1200]}`",
            ]
        )
        if storydriver_result.get("error"):
            lines.append(f"- Error: {storydriver_result.get('error')}")
    else:
        lines.append("- Skipped.")
    lines.extend(
        [
            "",
            "## Comparison",
            "",
            f"- Direct minimal raw stream speed: {direct_minimal.tokens_per_second_estimated if direct_minimal else 'unknown'} est. tok/s",
            f"- Direct minimal visible prose speed: {number(direct_minimal.visible_tokens_per_second_estimated) if direct_minimal else 'unknown'} est. tok/s",
            f"- Direct full StoryDriver prompt raw stream speed: {full_prompt.tokens_per_second_estimated if full_prompt else 'unknown'} est. tok/s",
            f"- Direct full StoryDriver prompt visible prose speed: {number(full_prompt.visible_tokens_per_second_estimated) if full_prompt else 'unknown'} est. tok/s",
            f"- StoryDriver saved speed: {story_stats.get('tokens_per_second') if story_stats else 'unknown'} tok/s",
            f"- StoryDriver first-token latency: {story_stats.get('first_token_latency_seconds') if story_stats else 'unknown'}s",
            f"- StoryDriver first-reasoning latency: {story_stats.get('first_reasoning_latency_seconds') if story_stats else 'unknown'}s",
            f"- StoryDriver hidden reasoning chars: {story_stats.get('reasoning_chars') if story_stats else 'unknown'}",
            f"- Prompt size difference: full prompt has {prompt_diag.get('prompt_estimated_tokens')} estimated tokens",
            f"- Likely bottleneck: {likely_bottleneck}",
            "",
            "## Recommendations",
            "",
            f"- For fastest writing on this PC, use `{GEMMA_MODEL}` for `prose_generation` and keep only that LM Studio model loaded.",
            "- If `reasoning_content` appears before visible prose, disable/reduce thinking/reasoning mode in LM Studio for the loaded model or use a non-thinking preset for StoryDriver writing.",
            "- Keep ComfyUI open for reuse, but leave image workflows in Auto Image Priority and use ComfyUI `/free` after images.",
            "- If direct API is slow too, fix LM Studio runtime/offload in LM Studio before loading the model; StoryDriver did not assume unsupported GPU/offload controls.",
            "- If direct API is fast but StoryDriver is slow, check prompt size warnings and enable `Free idle ComfyUI before writing` for a comparison run.",
        ]
    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose LM Studio speed through the same local API StoryDriver uses.")
    parser.add_argument("--backend-url", default=os.environ.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001"))
    parser.add_argument("--max-output-tokens", type=int, default=320)
    parser.add_argument("--use-configured-max-tokens", action="store_true")
    parser.add_argument("--skip-storydriver-generation", action="store_true")
    parser.add_argument("--skip-comfy-free", action="store_true")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    load_env(ROOT / "backend" / ".env")

    model_settings, resolved_model = resolve_task_model_settings("prose_generation")
    global_settings = load_model_settings(resolve_active_preset=True)
    image_settings = load_image_settings()
    openai_url = model_settings.lm_studio_url.rstrip("/")
    rest_url = settings.lm_studio_rest_base_url.rstrip("/")
    comfy_url = (image_settings.comfyui_base_url or settings.comfyui_base_url).rstrip("/")
    model = choose_model(openai_url, model_settings.model)
    writing_length = resolve_writing_length(director_note="Beat: Diagnostic prose speed test.")
    parameters = generation_parameters(model_settings, writing_length)
    diagnostic_params, configured_max_tokens = capped_parameters(
        parameters,
        args.max_output_tokens,
        args.use_configured_max_tokens,
    )

    rest_payload, rest_error = safe_json(f"{rest_url}/models", timeout=15)
    comfy_before, comfy_before_error = safe_json(f"{comfy_url}/system_stats", timeout=8)

    minimal_system = "You are a fast local fiction prose generator. Return prose only."
    minimal_user = (
        "Write a grounded, sensory medieval paragraph about rain on a farm road. "
        "No headings, no explanation, prose only."
    )
    no_state_prompt = build_scene_prompt(
        session_id="storydriver-speed-diagnostics-no-state",
        director_note="Beat: Write a grounded medieval scene about two sisters deciding whether to rescue a captured child. No magic.",
        mode="continue",
        recent_scenes=[],
        session_summary=None,
        memories=[],
        world_notes=None,
        active_characters=[],
        task_notes=resolved_model.notes,
        writing_length=writing_length,
    )

    full_session_id = latest_real_session_id()
    prompt_diag: dict[str, Any] = {}
    full_prompt = no_state_prompt
    if full_session_id:
        prepared = prepare_generation(
            full_session_id,
            GenerateSceneRequest(
                director_note="Beat: Diagnostic full-context speed test. Continue with grounded prose only.",
                mode="continue",
            ),
        )
        full_prompt = build_scene_prompt(
            session_id=full_session_id,
            director_note=prepared["director_note"],
            mode="continue",
            recent_scenes=prepared["recent_scenes"],
            session_summary=prepared["session_summary"],
            target_scene=prepared["target_scene"],
            task_notes=resolved_model.notes,
            writing_length=prepared["writing_length"],
        )
        prompt_diag = build_prompt_diagnostics(
            system_prompt=model_settings.system_prompt,
            user_prompt=full_prompt,
            task_notes=resolved_model.notes,
            director_note=prepared["director_note"],
            session_summary=prepared["session_summary"],
            recent_scenes=prepared["recent_scenes"],
        )
    else:
        prompt_diag = build_prompt_diagnostics(
            system_prompt=model_settings.system_prompt,
            user_prompt=full_prompt,
            task_notes=resolved_model.notes,
            director_note="Beat: Diagnostic full-context speed test.",
            session_summary=None,
            recent_scenes=[],
        )

    results: list[StreamResult] = []
    results.append(
        stream_lmstudio_chat(
            label="direct_minimal",
            base_url=openai_url,
            model=model,
            system_prompt=minimal_system,
            user_prompt=minimal_user,
            parameters=diagnostic_params,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        )
    )

    comfy_free, comfy_free_error = (None, "skipped")
    comfy_after, comfy_after_error = (None, "skipped")
    if not args.skip_comfy_free:
        comfy_free, comfy_free_error = safe_json(
            f"{comfy_url}/free",
            method="POST",
            payload={"unload_models": True, "free_memory": True},
            timeout=35,
        )
        comfy_after, comfy_after_error = safe_json(f"{comfy_url}/system_stats", timeout=8)

    results.extend(
        [
            stream_lmstudio_chat(
                label="direct_minimal_after_comfy_free",
                base_url=openai_url,
                model=model,
                system_prompt=minimal_system,
                user_prompt=minimal_user,
                parameters=diagnostic_params,
                configured_max_tokens=configured_max_tokens,
                timeout=resolved_model.timeout_seconds,
            ),
            stream_lmstudio_chat(
                label="direct_storydriver_like_no_state",
                base_url=openai_url,
                model=model,
                system_prompt=model_settings.system_prompt,
                user_prompt=no_state_prompt,
                parameters=diagnostic_params,
                configured_max_tokens=configured_max_tokens,
                timeout=resolved_model.timeout_seconds,
            ),
            stream_lmstudio_chat(
                label="direct_full_storydriver_prompt",
                base_url=openai_url,
                model=model,
                system_prompt=model_settings.system_prompt,
                user_prompt=full_prompt,
                parameters=diagnostic_params,
                configured_max_tokens=configured_max_tokens,
                timeout=resolved_model.timeout_seconds,
            ),
        ]
    )

    storydriver_result = None
    if not args.skip_storydriver_generation:
        try:
            diagnostic_session_id = create_diagnostic_session(args.backend_url)
            storydriver_result = run_storydriver_generation(args.backend_url, diagnostic_session_id)
        except Exception as error:  # noqa: BLE001
            storydriver_result = {"ok": False, "error": str(error)}

    diagnostics = {
        "task_profile": resolved_model.task_type,
        "task_label": resolved_model.label,
        "configured_max_tokens": parameters.get("max_tokens"),
        "diagnostic_max_tokens": diagnostic_params.get("max_tokens"),
        "temperature": diagnostic_params.get("temperature"),
        "top_p": diagnostic_params.get("top_p"),
        "streaming": resolved_model.streaming,
        "global_model": global_settings.model,
    }

    lines = report_lines(
        openai_url=openai_url,
        rest_url=rest_url,
        comfy_url=comfy_url,
        model=model,
        diagnostics=diagnostics,
        rest_payload=rest_payload,
        rest_error=rest_error,
        comfy_before=comfy_before,
        comfy_before_error=comfy_before_error,
        comfy_free=comfy_free,
        comfy_free_error=comfy_free_error,
        comfy_after=comfy_after,
        comfy_after_error=comfy_after_error,
        results=results,
        storydriver_result=storydriver_result,
        prompt_diag=prompt_diag,
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    REPORT_PATH.write_text(text, encoding="utf-8")
    LATENCY_REPORT_PATH.write_text(
        text.replace("# LM Studio Speed Diagnostics", "# Latency Optimization Report", 1),
        encoding="utf-8",
    )
    print(text)
    return 0 if all(result.ok for result in results) and (not storydriver_result or storydriver_result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
