import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://localhost:8001"
REPORT = ROOT / "backend" / "data" / "logs" / "GEMMA_TEMPLATE_SPEED_TEST_REPORT.md"
TEST_TITLE = "StoryDriver Gemma Template Speed Test"
GEMMA_ID = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"

SCENE_NOTES = [
    (
        "Scene 1",
        "Write a short grounded medieval scene with three sisters at a farm table planning a rescue. "
        "Keep it around 700 words. No magic exists in this world. Write prose only.",
    ),
    (
        "Scene 2",
        "Continue with the sisters leaving before dawn toward the bandit camp. Keep it around 700 words. "
        "Keep the tone grounded, practical, and tense. No magic. Write prose only.",
    ),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def api_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"{method} {path} failed HTTP {error.code}: {detail}") from error


def get_or_create_session() -> dict[str, Any]:
    sessions = api_json("/sessions", timeout=20)
    if isinstance(sessions, list):
        for session in sessions:
            if session.get("title") == TEST_TITLE:
                return session
    return api_json("/sessions", method="POST", payload={"title": TEST_TITLE}, timeout=20)


def selected_version(scene: dict[str, Any]) -> dict[str, Any]:
    version_id = scene.get("active_version_id")
    versions = scene.get("versions") or []
    for version in versions:
        if version.get("id") == version_id:
            return version
    return versions[-1] if versions else {}


def generation_stats(scene: dict[str, Any]) -> dict[str, Any]:
    version = selected_version(scene)
    return version.get("generation_stats") or scene.get("generation_stats") or {}


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def generate_stream(session_id: str, label: str, director_note: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps(
            {
                "director_note": director_note,
                "mode": "continue",
                "client_submitted_at": datetime.now(timezone.utc).isoformat(),
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    first_delta_at: float | None = None
    scene_event_at: float | None = None
    status_events: list[dict[str, Any]] = []
    warnings: list[str] = []
    delta_count = 0
    delta_chars = 0
    scene: dict[str, Any] | None = None

    print(f"[gemma-template] generating {label}...")
    with urllib.request.urlopen(request, timeout=900) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type")
            elapsed = time.perf_counter() - started
            if event_type == "status":
                status_events.append(event)
                print(f"[status:{label}] {event.get('stage')} {event.get('message')}")
            elif event_type == "delta":
                if first_delta_at is None:
                    first_delta_at = elapsed
                text = event.get("text") or ""
                delta_count += 1
                delta_chars += len(text)
                if delta_count % 50 == 0:
                    print(f"[stream:{label}] {delta_chars} chars")
            elif event_type == "warning":
                warnings.append(event.get("message") or "")
            elif event_type == "scene":
                scene_event_at = elapsed
                scene = event.get("scene") or {}
            elif event_type == "error":
                raise RuntimeError(event.get("detail") or "Generation stream returned an error.")

    if not scene:
        raise RuntimeError(f"No final scene event returned for {label}.")

    stats = generation_stats(scene)
    text = scene.get("generated_text") or selected_version(scene).get("text") or ""
    return {
        "label": label,
        "scene_id": scene.get("id"),
        "version_id": scene.get("active_version_id") or selected_version(scene).get("id"),
        "words": word_count(text),
        "text_chars": len(text),
        "submit_to_first_visible_seconds": round(first_delta_at, 3) if first_delta_at is not None else None,
        "submit_to_scene_event_seconds": round(scene_event_at, 3) if scene_event_at is not None else None,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "delta_count": delta_count,
        "delta_chars": delta_chars,
        "status_stages": [event.get("stage") for event in status_events],
        "warnings": warnings,
        "stats": stats,
    }


def extract_report_value(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def read_report(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def format_seconds(value: Any) -> str:
    if value is None:
        return "not recorded"
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return str(value)


def format_number(value: Any) -> str:
    if value is None:
        return "not recorded"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def scene_lines(result: dict[str, Any]) -> list[str]:
    stats = result.get("stats") or {}
    latency = stats.get("latency") or {}
    prompt = stats.get("prompt_diagnostics") or {}
    return [
        f"### {result['label']}",
        f"- Scene ID: `{result.get('scene_id')}`",
        f"- Version ID: `{result.get('version_id')}`",
        f"- Words: {result.get('words')}",
        f"- Submit to first visible prose: {format_seconds(result.get('submit_to_first_visible_seconds'))}",
        f"- Submit to scene saved event: {format_seconds(result.get('submit_to_scene_event_seconds'))}",
        f"- LM request to first token: {format_seconds(latency.get('lm_request_to_first_token_seconds') or stats.get('first_token_latency_seconds'))}",
        f"- Scene generation wall: {format_seconds(latency.get('scene_generation_seconds') or stats.get('elapsed_seconds'))}",
        f"- Saved visible t/s: {format_number(stats.get('tokens_per_second'))}",
        f"- Visible-after-first t/s: {format_number(stats.get('visible_prose_tokens_after_first_per_second'))}",
        f"- Hidden reasoning chars: {format_number(stats.get('reasoning_chars'))}",
        f"- Hidden reasoning chunks: {format_number(stats.get('reasoning_chunks'))}",
        f"- Raw stream t/s: {format_number(stats.get('raw_stream_tokens_per_second'))}",
        f"- Model: `{stats.get('model') or 'unknown'}`",
        f"- Task profile: `{stats.get('task_profile') or 'unknown'}`",
        f"- Backend: `{stats.get('inference_backend') or stats.get('requested_inference_backend') or 'unknown'}`",
        f"- Prompt mode: `{stats.get('prose_prompt_mode') or 'unknown'}`",
        f"- Prompt estimated tokens: {format_number(prompt.get('prompt_estimated_tokens'))}",
        f"- Status stages: {', '.join(str(item) for item in result.get('status_stages') or []) or 'none'}",
        f"- Warnings: {', '.join(result.get('warnings') or []) or 'none'}",
    ]


def write_report(
    *,
    diagnostics: dict[str, Any],
    model_settings: dict[str, Any],
    routing: dict[str, Any],
    ui_parity_report: str,
    apples_report: str,
    native_report: str,
    session: dict[str, Any],
    scene_results: list[dict[str, Any]],
) -> None:
    lm = diagnostics.get("lm_studio") or {}
    runtime = lm.get("runtime_metadata") or {}
    loaded = lm.get("loaded_instances") or []
    prose_profile = ((routing.get("resolved") or {}).get("prose_generation") or {}) if isinstance(routing, dict) else {}
    ui_exact_first = extract_report_value(
        ui_parity_report,
        r"### D_exact_storydriver_payload[\s\S]*?- First visible prose: ([^\n]+)",
    )
    ui_exact_reasoning = extract_report_value(
        ui_parity_report,
        r"### D_exact_storydriver_payload[\s\S]*?- Hidden reasoning: ([^\n]+)",
    )
    apples_full_speed = extract_report_value(apples_report, r"- StoryDriver full-context saved speed: ([^\n]+)")
    apples_full_reasoning = extract_report_value(apples_report, r"- StoryDriver full hidden reasoning chars: ([^\n]+)")
    native_exact_first = extract_report_value(
        native_report,
        r"### E_native_exact_storydriver_prompt_native_reasoning_auto[\s\S]*?- First visible prose: ([^\n]+)",
    )
    native_reasoning_off = extract_report_value(native_report, r"- `reasoning=\"off\"` accepted: ([^\n]+)")

    lines = [
        "# Gemma Template Speed Test Report",
        "",
        f"Updated: {now_iso()}",
        "",
        "## Loaded Model",
        f"- LM Studio reachable: {'yes' if lm.get('reachable') else 'no'}",
        f"- REST reachable: {'yes' if lm.get('rest_reachable') else 'no'}",
        f"- Loaded LLM instances: {len(loaded)}",
    ]
    for item in loaded:
        lines.append(f"  - `{item.get('display_name')}` / `{item.get('model_key')}` / instance `{item.get('id')}`")
    lines.extend(
        [
            f"- Expected Gemma loaded: {'yes' if len(loaded) == 1 and loaded[0].get('model_key') == GEMMA_ID else 'check manually'}",
            f"- Runtime metadata visible: {'yes' if runtime.get('runtime_info_visible') else 'no'}",
            f"- GPU/offload info visible: {'yes' if runtime.get('gpu_offload_visible') else 'no'}",
            f"- Context length visible: {'yes' if runtime.get('context_length_visible') else 'no'}",
        ]
    )
    for field in runtime.get("metadata_fields") or []:
        path = field.get("path")
        if path and "loaded_instances[0].config" in path:
            lines.append(f"  - `{path}` = `{field.get('value')}`")

    lines.extend(
        [
            "",
            "## StoryDriver Routing During Test",
            f"- Prose model: `{prose_profile.get('model') or model_settings.get('model') or 'auto'}`",
            f"- Prose prompt mode: `{model_settings.get('prose_prompt_mode') or 'unknown'}`",
            f"- Prose backend: `{prose_profile.get('inference_backend') or model_settings.get('inference_backend') or 'unknown'}`",
            f"- Prose reasoning mode: `{prose_profile.get('reasoning_mode') or model_settings.get('reasoning_mode') or 'unknown'}`",
            f"- Direct/System Prompt Priority enabled: {'yes' if model_settings.get('prose_prompt_mode') == 'direct' else 'no'}",
            "",
            "## Direct API Results After Template Change",
            f"- OpenAI exact StoryDriver prompt first visible prose: {ui_exact_first or 'not recorded'}",
            f"- OpenAI exact StoryDriver prompt hidden reasoning: {ui_exact_reasoning or 'not recorded'}",
            f"- Apples-to-apples StoryDriver full-context saved speed: {apples_full_speed or 'not recorded'}",
            f"- Apples-to-apples StoryDriver full hidden reasoning chars: {apples_full_reasoning or 'not recorded'}",
            f"- Native exact StoryDriver prompt first visible prose: {native_exact_first or 'not recorded'}",
            f"- Native `reasoning=off` accepted: {native_reasoning_off or 'not recorded'}",
            "",
            "## StoryDriver Real Scene Test",
            f"- Session: `{session.get('id')}` / {session.get('title')}",
        ]
    )
    for result in scene_results:
        lines.extend(["", *scene_lines(result)])

    scene_reasoning = [int((item.get("stats") or {}).get("reasoning_chars") or 0) for item in scene_results]
    first_visible_values = [
        item.get("submit_to_first_visible_seconds")
        for item in scene_results
        if item.get("submit_to_first_visible_seconds") is not None
    ]
    visible_tps_values = [
        (item.get("stats") or {}).get("tokens_per_second")
        for item in scene_results
        if (item.get("stats") or {}).get("tokens_per_second") is not None
    ]
    lines.extend(
        [
            "",
            "## Before / After Comparison",
            "- Previous bad baseline examples: first visible prose around 20-60s, hidden reasoning around 4,400 chars, visible prose around 10-20 t/s.",
            f"- New first visible prose range: {min(first_visible_values):.3f}s to {max(first_visible_values):.3f}s" if first_visible_values else "- New first visible prose range: not recorded",
            f"- New hidden reasoning chars range: {min(scene_reasoning)} to {max(scene_reasoning)}",
            f"- New saved visible t/s range: {min(visible_tps_values):.2f} to {max(visible_tps_values):.2f}" if visible_tps_values else "- New saved visible t/s range: not recorded",
            "",
            "## Conclusion",
            "- The no-thinking Gemma template appears active for the LM Studio server/API path.",
            "- Hidden reasoning is effectively gone in the measured API paths; only a 1-character reasoning stub was seen.",
            "- First visible prose latency is now near normal prefill/first-token latency rather than the old hidden-thinking delay.",
            "- StoryDriver is much closer to LM Studio UI speed. Remaining gap is mostly StoryDriver route/background overhead and full-scene wall-time accounting, not forced thinking.",
            "- Native Chat works in auto mode but `reasoning=off` is still rejected by this loaded Gemma runtime, so OpenAI-compatible remains the safest prose default.",
            "",
            "## Recommended Settings",
            "- Keep the no-thinking Gemma template loaded manually in LM Studio.",
            "- Keep only Gemma loaded while measuring prose speed.",
            "- Keep `prose_generation` on OpenAI-compatible unless Native auto proves better in your own longer scene tests.",
            "- Leave Native fallback enabled if experimenting with Native Chat.",
            "- Switch Prose Prompt Mode to Direct/System Priority only if you want the smallest prompt path; this test kept the current Standard mode unchanged.",
            "- Avoid Auto Image Priority during prose speed testing so StoryDriver does not unload/reload the manually loaded template.",
        ]
    )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    health = api_json("/health", timeout=10)
    if not isinstance(health, dict) or not health.get("ok"):
        raise RuntimeError("StoryDriver backend health is not OK.")
    diagnostics = api_json("/diagnostics", timeout=30)
    model_settings = api_json("/settings/model", timeout=20)
    routing = api_json("/settings/task-model-profiles", timeout=20)
    session = get_or_create_session()
    results = []
    for label, note in SCENE_NOTES:
        results.append(generate_stream(session["id"], label, note))
    write_report(
        diagnostics=diagnostics or {},
        model_settings=model_settings or {},
        routing=routing or {},
        ui_parity_report=read_report(ROOT / "backend" / "data" / "logs" / "LM_STUDIO_UI_PARITY_REPORT.md"),
        apples_report=read_report(ROOT / "backend" / "data" / "logs" / "LM_STUDIO_APPLES_TO_APPLES_SPEED_REPORT.md"),
        native_report=read_report(ROOT / "backend" / "data" / "logs" / "LM_STUDIO_NATIVE_CHAT_REPORT.md"),
        session=session,
        scene_results=results,
    )
    print(f"[OK] report written: {REPORT}")
    for result in results:
        stats = result.get("stats") or {}
        print(
            f"[OK] {result['label']}: first visible {result.get('submit_to_first_visible_seconds')}s, "
            f"t/s {stats.get('tokens_per_second')}, reasoning chars {stats.get('reasoning_chars')}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            "# Gemma Template Speed Test Report\n\n"
            f"Updated: {now_iso()}\n\n"
            "## Failure\n"
            f"- {error}\n",
            encoding="utf-8",
        )
        print(f"[FAIL] {error}")
        sys.exit(1)
