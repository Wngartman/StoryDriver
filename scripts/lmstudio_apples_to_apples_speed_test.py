from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from lmstudio_speed_diagnostics import (  # noqa: E402
    GEMMA_MODEL,
    ROOT,
    StreamResult,
    build_prompt_diagnostics,
    capped_parameters,
    choose_model,
    estimate_tokens,
    loaded_instances_from_payload,
    load_env,
    request_json,
    runtime_metadata,
    safe_json,
    stream_lmstudio_chat,
)

from app.config import settings  # noqa: E402
from app.database import db_session  # noqa: E402
from app.routes.sessions import generation_parameters, prepare_generation  # noqa: E402
from app.schemas import GenerateSceneRequest  # noqa: E402
from app.generation.router import resolve_task_model_settings  # noqa: E402
from app.generation.prompt_builder import build_scene_prompt, resolve_writing_length  # noqa: E402
from app.settings.store import load_image_settings, load_model_settings  # noqa: E402


REPORT_PATH = ROOT / "backend" / "data" / "logs" / "LM_STUDIO_APPLES_TO_APPLES_SPEED_REPORT.md"
PROMPT_DEBUG_PATH = ROOT / "backend" / "data" / "logs" / "last_prose_prompt_debug.txt"
SETTINGS_DEBUG_PATH = ROOT / "backend" / "data" / "logs" / "last_prose_settings_debug.json"
TEST_TITLE = "StoryDriver Apples-to-Apples Speed Test"
MINIMAL_NOTE = (
    "Beat: Write a grounded medieval prose scene around 300 words. "
    "Two sisters repair a wagon wheel in hard rain while deciding whether to help a captured child. "
    "No magic, no headings, prose only."
)
FULL_NOTE = (
    "Scene: Continue with a grounded, no-magic medieval scene in which the sisters decide how to approach "
    "the bandit camp. Keep it prose only and do not summarize."
)


def utc_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def post_json(url: str, payload: dict[str, Any], timeout: float = 30) -> Any:
    return request_json(url, method="POST", payload=payload, timeout=timeout)


def backend_create_session(backend_url: str, title: str) -> str:
    created = post_json(
        f"{backend_url.rstrip('/')}/sessions",
        {"title": title},
        timeout=20,
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError("StoryDriver backend did not return a session id.")
    return str(created["id"])


def local_create_session(title: str) -> str:
    session_id = str(uuid4())
    with db_session() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, title))
    return session_id


def insert_seed_scene(session_id: str, director_note: str, generated_text: str) -> None:
    scene_id = str(uuid4())
    version_id = str(uuid4())
    stats = json.dumps({"diagnostic_seed": True}, ensure_ascii=False)
    with db_session() as db:
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, generation_stats_json, mode)
            VALUES (?, ?, ?, ?, ?, 'continue')
            """,
            (scene_id, session_id, director_note, generated_text, stats),
        )
        db.execute(
            """
            INSERT INTO scene_versions (
                id, scene_id, session_id, director_note, generated_text, generation_stats_json, mode, version_index
            )
            VALUES (?, ?, ?, ?, ?, ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, director_note, generated_text, stats),
        )
        db.execute(
            "UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (session_id,),
        )


def seed_full_context(session_id: str) -> None:
    seed_scenes = [
        (
            "Diagnostic seed: opening farm scene.",
            (
                "Rain worried the thatch of the farm cottage while Elara kept a knife hidden under her sleeve. "
                "Maren, broad-shouldered and blunt, checked the latch on the barn door for the third time. "
                "Tess sat beside the hearth with a chipped cup between both hands, listening to the road beyond the fields. "
                "They had survived the streets as children after their parents died, and the farm outside Greymarket was "
                "the first place that had ever stayed theirs. No magic softened the dark; only wet earth, old tools, "
                "and three sisters who knew how to go unseen."
            ),
        ),
        (
            "Diagnostic seed: chapel rumor.",
            (
                "A peddler brought word that bandits had taken a little girl near the ruined chapel north of town. "
                "Elara wanted proof before risking their home. Maren wanted a route, a count of men, and a way out. "
                "Tess, who remembered sleeping hungry under market stairs, could not stop picturing the girl alone in the rain. "
                "They argued softly over a rough table while candlelight guttered in the draft."
            ),
        ),
        (
            "Diagnostic seed: scouting aftermath.",
            (
                "By dusk they had seen smoke from the bandit camp and three armed men near the chapel wall. "
                "Maren slipped in the mud and tore her red-brown cloak at the hem. Elara found wagon tracks leading east. "
                "Tess kept a bronze whistle she had stolen from the peddler, ashamed but unwilling to put it back. "
                "None of them had ever killed anyone, and the fact sat between them heavier than any blade."
            ),
        ),
    ]
    for note, text in seed_scenes:
        insert_seed_scene(session_id, note, text)


def storydriver_stream_generation(backend_url: str, session_id: str, note: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{backend_url.rstrip('/')}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps({"director_note": note, "mode": "continue"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    first_delta: float | None = None
    first_status: float | None = None
    chunks = 0
    chars = 0
    stages: list[str] = []
    final_scene: dict[str, Any] | None = None
    try:
        with urllib.request.urlopen(req, timeout=420) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get("type") == "status":
                    stages.append(event.get("stage") or "")
                    if first_status is None:
                        first_status = time.perf_counter()
                elif event.get("type") == "delta":
                    chunks += 1
                    text = event.get("text") or ""
                    chars += len(text)
                    if first_delta is None:
                        first_delta = time.perf_counter()
                elif event.get("type") == "scene":
                    final_scene = event.get("scene")
                elif event.get("type") == "error":
                    return {
                        "ok": False,
                        "error": event.get("detail") or "StoryDriver generation failed.",
                        "wall_seconds": round(time.perf_counter() - started, 3),
                        "stages": stages,
                    }
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        return {"ok": False, "error": f"HTTP {error.code}: {detail[:800]}", "stages": stages}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error), "stages": stages}

    stats = {}
    if final_scene:
        versions = final_scene.get("versions") or []
        stats = (versions[-1].get("generation_stats") if versions else final_scene.get("generation_stats")) or {}
    return {
        "ok": bool(final_scene),
        "scene_id": final_scene.get("id") if final_scene else None,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "first_status_seconds": round(first_status - started, 3) if first_status else None,
        "first_delta_seconds": round(first_delta - started, 3) if first_delta else None,
        "chunks": chunks,
        "streamed_chars": chars,
        "streamed_tokens_estimated": estimate_tokens("x" * chars) if chars else 0,
        "stages": stages,
        "generation_stats": stats,
    }


def write_debug_files(
    *,
    model: str,
    resolved_model: Any,
    model_settings: Any,
    parameters: dict[str, Any],
    diagnostic_params: dict[str, Any],
    prompt_diag: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    rest_payload: Any,
    rest_error: str | None,
    comfy_url: str,
    comfy_before_error: str | None,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_DEBUG_PATH.write_text(
        "\n".join(
            [
                "StoryDriver local prose prompt debug",
                f"Generated: {utc_stamp()}",
                "This file is local-only and may contain story text.",
                "",
                "=== SYSTEM PROMPT ===",
                system_prompt or "",
                "",
                "=== EXACT STORYDRIVER USER PROMPT USED FOR DIRECT API TEST ===",
                user_prompt or "",
            ]
        ),
        encoding="utf-8",
    )
    runtime = runtime_metadata(rest_payload)
    SETTINGS_DEBUG_PATH.write_text(
        json.dumps(
            {
                "generated_at": utc_stamp(),
                "model": model,
                "expected_fast_model": GEMMA_MODEL,
                "task_profile": getattr(resolved_model, "task_type", "prose_generation"),
                "task_label": getattr(resolved_model, "label", "Story Writing"),
                "lm_studio_url": getattr(model_settings, "lm_studio_url", ""),
                "temperature": parameters.get("temperature"),
                "top_p": parameters.get("top_p"),
                "max_tokens_configured": parameters.get("max_tokens"),
                "max_tokens_diagnostic": diagnostic_params.get("max_tokens"),
                "streaming": getattr(resolved_model, "streaming", True),
                "timeout_seconds": getattr(resolved_model, "timeout_seconds", None),
                "prompt_diagnostics": prompt_diag,
                "loaded_instances": loaded_instances_from_payload(rest_payload),
                "lm_studio_rest_error": rest_error,
                "runtime_metadata": runtime,
                "comfyui_url": comfy_url,
                "comfyui_system_stats_before_error": comfy_before_error,
                "measurement_note": (
                    "Direct tests report both visible prose speed after first content and raw stream speed. "
                    "StoryDriver saved t/s is estimated visible prose tokens divided by full LM generation wall time."
                ),
                "offload_note": runtime.get("control_note"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def result_line(result: StreamResult) -> list[str]:
    def seconds(value: Any) -> str:
        return f"{value}s" if value is not None else "none"

    def number(value: Any) -> str:
        return str(value) if value is not None else "none"

    if not result.ok:
        return [f"### {result.label}", f"- FAILED: {result.error}", ""]
    return [
        f"### {result.label}",
        f"- Prompt: {result.prompt_chars:,} chars / {result.prompt_tokens_estimated:,} estimated tokens",
        f"- First stream event: {seconds(result.first_any_seconds)}",
        f"- First reasoning: {seconds(result.first_reasoning_seconds)}",
        f"- First visible prose: {seconds(result.first_content_seconds)}",
        f"- Total wall time: {seconds(result.total_seconds)}",
        f"- Visible output: {result.output_chars:,} chars / {result.output_tokens_estimated:,} estimated tokens / {result.chunks} chunks",
        f"- Hidden reasoning: {result.reasoning_chars:,} chars / {result.reasoning_tokens_estimated:,} estimated tokens / {result.reasoning_chunks} chunks",
        f"- Raw stream speed after first event: {number(result.tokens_per_second_estimated)} est. tok/s",
        f"- Visible prose speed after first content: {number(result.visible_tokens_per_second_estimated)} est. tok/s",
        f"- Max tokens: configured {result.configured_max_tokens}, diagnostic {result.diagnostic_max_tokens}",
        "",
    ]


def storydriver_lines(label: str, result: dict[str, Any] | None) -> list[str]:
    if not result:
        return [f"### {label}", "- Skipped.", ""]
    stats = result.get("generation_stats") or {}
    latency = stats.get("latency") or {}
    if not result.get("ok"):
        return [f"### {label}", f"- FAILED: {result.get('error')}", f"- Stages: {', '.join(result.get('stages') or [])}", ""]
    return [
        f"### {label}",
        f"- Scene: `{result.get('scene_id')}`",
        f"- First visible frontend delta: {result.get('first_delta_seconds')}s",
        f"- Wall time: {result.get('wall_seconds')}s",
        f"- Streamed: {result.get('streamed_chars'):,} chars / {result.get('chunks')} chunks",
        f"- Saved StoryDriver t/s: {stats.get('tokens_per_second')} est. visible tok/s",
        f"- Saved first-token latency: {stats.get('first_token_latency_seconds')}s",
        f"- Saved hidden reasoning chars: {stats.get('reasoning_chars')}",
        f"- Prompt estimate: {(stats.get('prompt_diagnostics') or {}).get('prompt_estimated_tokens')} tokens",
        f"- Prompt builder: {latency.get('prompt_builder_seconds')}s",
        f"- Pre-write ComfyUI free: {latency.get('comfyui_pre_write_free_seconds')}s",
        f"- Scene generation wall: {latency.get('scene_generation_seconds')}s",
        f"- Stages: {', '.join(result.get('stages') or [])}",
        "",
    ]


def build_report(
    *,
    openai_url: str,
    rest_url: str,
    comfy_url: str,
    model: str,
    prompt_diag: dict[str, Any],
    runtime: dict[str, Any],
    loaded_instances: list[dict[str, Any]],
    rest_error: str | None,
    comfy_before_error: str | None,
    comfy_free_error: str | None,
    comfy_after_error: str | None,
    direct_results: list[StreamResult],
    storydriver_minimal: dict[str, Any] | None,
    storydriver_full: dict[str, Any] | None,
) -> str:
    def metric(value: Any) -> str:
        return str(value) if value is not None else "none"

    direct_minimal = next((item for item in direct_results if item.label == "A_direct_minimal"), None)
    direct_full = next((item for item in direct_results if item.label == "B_direct_exact_storydriver_full_prompt"), None)
    direct_after_free = next((item for item in direct_results if item.label == "F_direct_minimal_after_comfy_free"), None)
    sd_full_stats = (storydriver_full or {}).get("generation_stats") or {}
    sd_min_stats = (storydriver_minimal or {}).get("generation_stats") or {}

    likely: list[str] = []
    if direct_minimal and direct_minimal.ok and direct_minimal.visible_tokens_per_second_estimated:
        if direct_minimal.visible_tokens_per_second_estimated < 60:
            likely.append("Direct LM Studio API is also slower than the LM Studio UI baseline, so server/runtime/offload or reporting differences are likely.")
        else:
            likely.append("Direct minimal LM Studio API is healthy; compare full prompt and StoryDriver route timings for prompt/context overhead.")
    if direct_full and direct_minimal and direct_full.ok and direct_minimal.ok:
        if (
            direct_full.visible_tokens_per_second_estimated
            and direct_minimal.visible_tokens_per_second_estimated
            and direct_full.visible_tokens_per_second_estimated < direct_minimal.visible_tokens_per_second_estimated * 0.65
        ):
            likely.append("The exact StoryDriver prompt is materially slower than a minimal prompt; prompt/context size is a bottleneck.")
    if direct_after_free and direct_minimal and direct_after_free.ok and direct_minimal.ok:
        before = direct_minimal.visible_tokens_per_second_estimated or direct_minimal.tokens_per_second_estimated or 0
        after = direct_after_free.visible_tokens_per_second_estimated or direct_after_free.tokens_per_second_estimated or 0
        if before and after > before * 1.15:
            likely.append("ComfyUI /free improved direct LM Studio stream speed in this run.")
        elif before and after:
            likely.append("ComfyUI /free did not materially improve direct LM Studio stream speed in this run.")
    if sd_full_stats.get("reasoning_chars"):
        likely.append("StoryDriver received hidden reasoning before/during visible prose; this can inflate first-token latency and reduce visible t/s.")
    if sd_full_stats.get("tokens_per_second") and direct_full and direct_full.ok and direct_full.visible_tokens_per_second_estimated:
        if float(sd_full_stats["tokens_per_second"]) < float(direct_full.visible_tokens_per_second_estimated) * 0.75:
            likely.append("StoryDriver route overhead/background contention may be reducing speed beyond the prompt itself.")
    if not likely:
        likely.append("No single bottleneck was isolated; use the per-stage timings below.")

    lines = [
        "# LM Studio Apples-to-Apples Speed Report",
        "",
        f"Updated: {utc_stamp()}",
        "",
        "## Test Matrix",
        "",
        "- A: Direct LM Studio API minimal prompt.",
        "- B: Direct LM Studio API with exact StoryDriver full prose prompt.",
        "- C/E: StoryDriver `/generate-stream` with seeded full context.",
        "- D: StoryDriver `/generate-stream` with minimal diagnostic context.",
        "- F: Direct LM Studio API minimal prompt after ComfyUI `/free`.",
        "- G: ComfyUI closed/unreachable test was not run automatically, because closing ComfyUI can interrupt the user's working image backend.",
        "",
        "## Configuration",
        "",
        f"- LM Studio OpenAI URL: `{openai_url}`",
        f"- LM Studio REST URL: `{rest_url}`",
        f"- ComfyUI URL: `{comfy_url}`",
        f"- Model tested: `{model}`",
        f"- Expected fast Gemma model: `{GEMMA_MODEL}`",
        f"- Loaded LM Studio instances: {len(loaded_instances)}",
    ]
    for instance in loaded_instances[:8]:
        lines.append(f"  - `{instance.get('display_name') or instance.get('model_key') or instance.get('id')}` (`{instance.get('id')}`)")
    if rest_error:
        lines.append(f"- LM Studio REST error: {rest_error}")
    lines.extend(
        [
            f"- Runtime metadata visible: {'yes' if runtime.get('runtime_info_visible') else 'no'}",
            f"- GPU/offload info visible: {'yes' if runtime.get('gpu_offload_visible') else 'no'}",
            f"- Context length visible: {'yes' if runtime.get('context_length_visible') else 'no'}",
            f"- GPU/offload controls through API: {'yes' if runtime.get('api_runtime_controls_exposed') else 'no'}",
            f"- Runtime/offload note: {runtime.get('control_note')}",
            "",
            "## Prompt Size",
            "",
            f"- System prompt chars: {prompt_diag.get('system_prompt_chars')}",
            f"- Task notes chars: {prompt_diag.get('task_notes_chars')}",
            f"- Story state chars: {prompt_diag.get('story_state_chars')}",
            f"- Active character chars: {prompt_diag.get('active_characters_chars')}",
            f"- World state chars: {prompt_diag.get('world_state_chars')}",
            f"- Summary chars: {prompt_diag.get('summary_chars')}",
            f"- Recent scenes chars: {prompt_diag.get('recent_scenes_chars')}",
            f"- Director note chars: {prompt_diag.get('director_note_chars')}",
            f"- Total prompt chars: {prompt_diag.get('total_prompt_chars')}",
            f"- Estimated prompt tokens: {prompt_diag.get('prompt_estimated_tokens')}",
        ]
    )
    for warning in prompt_diag.get("prompt_size_warnings") or []:
        lines.append(f"- Prompt warning: {warning}")
    lines.extend(
        [
            "",
            "## ComfyUI Idle Impact",
            "",
            f"- ComfyUI system stats before /free: {'ok' if not comfy_before_error else comfy_before_error}",
            f"- ComfyUI /free: {'ok' if not comfy_free_error else comfy_free_error}",
            f"- ComfyUI system stats after /free: {'ok' if not comfy_after_error else comfy_after_error}",
            "- ComfyUI closed/manual unavailable test: not run automatically.",
            "",
            "## Direct LM Studio API",
            "",
        ]
    )
    for result in direct_results:
        lines.extend(result_line(result))
    lines.extend(
        [
            "## StoryDriver Route",
            "",
            "StoryDriver saved t/s is estimated visible prose tokens divided by full LM generation wall time. It is not the same as LM Studio UI raw decode speed if the UI excludes prefill/first-token latency.",
            "",
        ]
    )
    lines.extend(storydriver_lines("D_storydriver_minimal_context", storydriver_minimal))
    lines.extend(storydriver_lines("C_E_storydriver_seeded_full_context", storydriver_full))
    lines.extend(
        [
            "## Comparison",
            "",
            f"- Direct minimal visible speed: {metric(direct_minimal.visible_tokens_per_second_estimated) if direct_minimal else 'unknown'} est. tok/s",
            f"- Direct exact StoryDriver full-prompt visible speed: {metric(direct_full.visible_tokens_per_second_estimated) if direct_full else 'unknown'} est. tok/s",
            f"- Direct minimal after ComfyUI /free visible speed: {metric(direct_after_free.visible_tokens_per_second_estimated) if direct_after_free else 'unknown'} est. tok/s",
            f"- StoryDriver minimal saved speed: {metric(sd_min_stats.get('tokens_per_second'))} est. tok/s",
            f"- StoryDriver full-context saved speed: {metric(sd_full_stats.get('tokens_per_second'))} est. tok/s",
            f"- StoryDriver full first-token latency: {metric(sd_full_stats.get('first_token_latency_seconds'))}s",
            f"- StoryDriver full hidden reasoning chars: {sd_full_stats.get('reasoning_chars') or 0}",
            "",
            "## Likely Bottleneck",
            "",
            *(f"- {item}" for item in likely),
            "",
            "## Recommended Fastest Settings For This PC",
            "",
            f"- Use `{GEMMA_MODEL}` for `prose_generation` and keep only that LM Studio model loaded.",
            "- If automatic reload tests are slower than manual LM Studio UI loads, set Image Settings -> LM reload policy to `Preserve manual LM Studio load` and reload the prose model manually after image jobs.",
            "- Leave `Free idle ComfyUI before writing` off unless this report shows `/free` improves direct LM Studio API speed.",
            "- If exact StoryDriver prompts are slower than minimal prompts, reduce recent-scene/state budget before changing GPU settings.",
            "- GPU/offload/context settings should be set inside LM Studio before loading the model unless your local REST API exposes documented runtime controls.",
            "",
            "## Debug Files",
            "",
            f"- Prompt debug: `{PROMPT_DEBUG_PATH}`",
            f"- Settings debug: `{SETTINGS_DEBUG_PATH}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run apples-to-apples LM Studio speed comparisons for StoryDriver.")
    parser.add_argument("--backend-url", default=os.environ.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001"))
    parser.add_argument("--max-output-tokens", type=int, default=320)
    parser.add_argument("--use-configured-max-tokens", action="store_true")
    parser.add_argument("--skip-storydriver-generation", action="store_true")
    parser.add_argument("--skip-comfy-free", action="store_true")
    args = parser.parse_args()

    load_env(ROOT / ".env")
    load_env(ROOT / "backend" / ".env")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    model_settings, resolved_model = resolve_task_model_settings("prose_generation")
    _global_settings = load_model_settings(resolve_active_preset=True)
    image_settings = load_image_settings()
    openai_url = model_settings.lm_studio_url.rstrip("/")
    rest_url = settings.lm_studio_rest_base_url.rstrip("/")
    comfy_url = (image_settings.comfyui_base_url or settings.comfyui_base_url).rstrip("/")
    model = choose_model(openai_url, model_settings.model)
    writing_length = resolve_writing_length(director_note=MINIMAL_NOTE)
    parameters = generation_parameters(model_settings, writing_length)
    diagnostic_params, configured_max_tokens = capped_parameters(
        parameters,
        args.max_output_tokens,
        args.use_configured_max_tokens,
    )

    full_context_session_id = local_create_session(f"{TEST_TITLE} Full {time.strftime('%H%M%S')}")
    seed_full_context(full_context_session_id)
    prepared_full = prepare_generation(
        full_context_session_id,
        GenerateSceneRequest(director_note=FULL_NOTE, mode="continue"),
    )
    full_prompt = build_scene_prompt(
        session_id=full_context_session_id,
        director_note=prepared_full["director_note"],
        mode="continue",
        recent_scenes=prepared_full["recent_scenes"],
        session_summary=prepared_full["session_summary"],
        target_scene=prepared_full["target_scene"],
        task_notes=resolved_model.notes,
        writing_length=prepared_full["writing_length"],
    )
    no_state_prompt = build_scene_prompt(
        session_id="storydriver-apples-minimal-context",
        director_note=MINIMAL_NOTE,
        mode="continue",
        recent_scenes=[],
        session_summary=None,
        memories=[],
        world_notes=None,
        active_characters=[],
        task_notes=resolved_model.notes,
        writing_length=writing_length,
    )
    prompt_diag = build_prompt_diagnostics(
        system_prompt=model_settings.system_prompt,
        user_prompt=full_prompt,
        task_notes=resolved_model.notes,
        director_note=prepared_full["director_note"],
        session_summary=prepared_full["session_summary"],
        recent_scenes=prepared_full["recent_scenes"],
    )

    rest_payload, rest_error = safe_json(f"{rest_url}/models", timeout=15)
    comfy_before, comfy_before_error = safe_json(f"{comfy_url}/system_stats", timeout=8)
    del comfy_before

    write_debug_files(
        model=model,
        resolved_model=resolved_model,
        model_settings=model_settings,
        parameters=parameters,
        diagnostic_params=diagnostic_params,
        prompt_diag=prompt_diag,
        system_prompt=model_settings.system_prompt,
        user_prompt=full_prompt,
        rest_payload=rest_payload,
        rest_error=rest_error,
        comfy_url=comfy_url,
        comfy_before_error=comfy_before_error,
    )

    minimal_system = "You are a fast local fiction prose generator. Return prose only."
    minimal_user = (
        "Write a grounded, sensory medieval paragraph about rain on a farm road. "
        "No headings, no explanation, prose only."
    )
    direct_results: list[StreamResult] = [
        stream_lmstudio_chat(
            label="A_direct_minimal",
            base_url=openai_url,
            model=model,
            system_prompt=minimal_system,
            user_prompt=minimal_user,
            parameters=diagnostic_params,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_lmstudio_chat(
            label="B_direct_exact_storydriver_full_prompt",
            base_url=openai_url,
            model=model,
            system_prompt=model_settings.system_prompt,
            user_prompt=full_prompt,
            parameters=diagnostic_params,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        ),
        stream_lmstudio_chat(
            label="direct_storydriver_like_minimal_context",
            base_url=openai_url,
            model=model,
            system_prompt=model_settings.system_prompt,
            user_prompt=no_state_prompt,
            parameters=diagnostic_params,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        ),
    ]

    comfy_free, comfy_free_error = (None, "skipped")
    comfy_after, comfy_after_error = (None, "skipped")
    if not args.skip_comfy_free:
        comfy_free, comfy_free_error = safe_json(
            f"{comfy_url}/free",
            method="POST",
            payload={"unload_models": True, "free_memory": True},
            timeout=35,
        )
        del comfy_free
        comfy_after, comfy_after_error = safe_json(f"{comfy_url}/system_stats", timeout=8)
        del comfy_after
    direct_results.append(
        stream_lmstudio_chat(
            label="F_direct_minimal_after_comfy_free",
            base_url=openai_url,
            model=model,
            system_prompt=minimal_system,
            user_prompt=minimal_user,
            parameters=diagnostic_params,
            configured_max_tokens=configured_max_tokens,
            timeout=resolved_model.timeout_seconds,
        )
    )

    storydriver_minimal = None
    storydriver_full = None
    if not args.skip_storydriver_generation:
        minimal_session_id = local_create_session(f"{TEST_TITLE} Minimal {time.strftime('%H%M%S')}")
        storydriver_minimal = storydriver_stream_generation(args.backend_url, minimal_session_id, MINIMAL_NOTE)
        storydriver_full = storydriver_stream_generation(args.backend_url, full_context_session_id, FULL_NOTE)

    runtime = runtime_metadata(rest_payload)
    loaded_instances = loaded_instances_from_payload(rest_payload)
    report = build_report(
        openai_url=openai_url,
        rest_url=rest_url,
        comfy_url=comfy_url,
        model=model,
        prompt_diag=prompt_diag,
        runtime=runtime,
        loaded_instances=loaded_instances,
        rest_error=rest_error,
        comfy_before_error=comfy_before_error,
        comfy_free_error=comfy_free_error,
        comfy_after_error=comfy_after_error,
        direct_results=direct_results,
        storydriver_minimal=storydriver_minimal,
        storydriver_full=storydriver_full,
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)

    direct_ok = all(result.ok for result in direct_results)
    story_ok = args.skip_storydriver_generation or (
        bool(storydriver_minimal and storydriver_minimal.get("ok"))
        and bool(storydriver_full and storydriver_full.get("ok"))
    )
    return 0 if direct_ok and story_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
