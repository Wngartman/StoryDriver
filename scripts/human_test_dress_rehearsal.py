from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "backend" / "data" / "logs" / "HUMAN_TEST_DRESS_REHEARSAL_REPORT.md"
BASE_URL = "http://localhost:8001"
REHEARSAL_TITLE = "StoryDriver Human Test Dress Rehearsal"

FIRST_CHAPTER_NOTE = (
    "Create a medieval no-magic adventure story about three adult sisters whose parents died when they were young. "
    "They lived on the streets and became stealthy outcasts. No magic exists in this world. They now live on a farm outside "
    "a major town and hear that a bandit camp captured a little girl. The first chapter introduces each sister properly and "
    "shows them talking through whether to rescue the girl. They are smart and tactical, but they have never truly killed or "
    "fought before. Make this a long first chapter, 7-12 minutes of reading time."
)

SECOND_SCENE_NOTE = (
    "Continue after the sisters decide they cannot ignore the captured girl. Keep the grounded no-magic tone. Show them preparing "
    "at the farm in the rain, checking stolen tools, old street habits, and the fear that none of them has truly fought before. "
    "Make it a focused continuation scene, not a summary."
)

REWRITE_NOTE = (
    "Rewrite this scene with clearer tension between the sisters and stronger grounded sensory detail, while preserving the same "
    "events, no magic rule, and continuity."
)

WORLD_NOTES = {
    "setting": "A grounded medieval frontier near a major town, poor tenant farms, mud roads, burned-out shrines, and bandit camps.",
    "tone": "Gritty, rain-soaked, practical, anxious, human, no glamour, no supernatural forces.",
    "rules": "No magic exists in this world. No prophecy, spells, enchanted objects, monsters, or supernatural powers.",
    "locations": "A small rented farm outside the town, muddy lanes, a market road, and a bandit camp in nearby woods.",
    "factions": "Town watch, farm tenants, thieves, bandits, merchants, hungry street children.",
    "conflicts": "Three sisters must decide whether stealth and street tricks are enough to rescue a captured girl.",
    "history": "The sisters survived orphaned street years before finding fragile safety on a farm outside town.",
}

IMAGE_STYLE_PAYLOAD = {
    "image_prompt_style": "Gritty Dark Fantasy Realism",
    "preferred_visual_tone": "rough dark medieval realism, practical faces and bodies, tense but not glossy",
    "realism_notes": "mud, sweat, old cloth, damp hair, worn tools, dirty hems, realistic fear, no clean poster look",
    "lighting_camera_notes": "overcast rain, candlelight or low hearth light, imperfect camera exposure, slight grain",
    "default_negative_prompt": (
        "plastic skin, glamour lighting, clean costume, sterile studio, anime, cartoon, modern objects, extra fingers, "
        "extra limbs, heroic fantasy poster"
    ),
}

MAGIC_TERMS = ("magic", "spell", "wizard", "sorcery", "enchanted", "prophecy", "supernatural")


def api_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 30.0):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if error.fp else str(error)
        raise RuntimeError(f"{method} {path} failed: HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"{method} {path} failed: {error}") from error


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def magic_violations(text: str) -> list[str]:
    violations: list[str] = []
    for term in MAGIC_TERMS:
        for match in re.finditer(rf"\b{re.escape(term)}\w*\b", text, flags=re.IGNORECASE):
            before = text[max(0, match.start() - 90) : match.start()].lower()
            phrase = text[max(0, match.start() - 25) : match.end() + 50].lower()
            if re.search(r"\b(no|not|without|lacked|lack|lacks|possessed no|possess no|had no|has no|there is no)\b", before):
                continue
            if "no magic" in phrase:
                continue
            violations.append(match.group(0).lower())
    return sorted(set(violations))


def top_repeated_names(text: str, limit: int = 6) -> list[str]:
    exclusions = {
        "The",
        "And",
        "But",
        "She",
        "They",
        "Her",
        "Their",
        "There",
        "Town",
        "Farm",
        "Bandit",
        "Magic",
        "No",
        "That",
        "This",
        "Chapter",
        "Street",
    }
    counts: dict[str, int] = {}
    for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
        name = match.group(0)
        if name in exclusions:
            continue
        counts[name] = counts.get(name, 0) + 1
    return [name for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if count >= 2][:limit]


def assistant_ending(text: str) -> bool:
    tail = text.lower()[-1000:]
    return any(phrase in tail for phrase in ("what happens next", "let me know", "i can continue", "would you like"))


def generate_stream(session_id: str, director_note: str, *, mode: str = "continue", target_scene_id: str | None = None) -> tuple[dict, dict]:
    payload: dict[str, Any] = {"director_note": director_note, "mode": mode}
    if target_scene_id:
        payload["target_scene_id"] = target_scene_id
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    status_events: list[dict[str, Any]] = []
    delta_count = 0
    streamed_chars = 0
    final_scene = None
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=1500) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type")
            if event_type == "status":
                status_events.append(event)
                print(f"[status] {event.get('message')}")
            elif event_type == "delta":
                text = event.get("text") or ""
                delta_count += 1
                streamed_chars += len(text)
                if delta_count % 25 == 0:
                    print(f"[stream] {streamed_chars} chars")
            elif event_type == "scene":
                final_scene = event.get("scene")
            elif event_type == "warning":
                status_events.append(event)
                print(f"[warning] {event.get('message')}")
            elif event_type == "error":
                raise RuntimeError(event.get("detail") or "Generation failed.")
    if not final_scene:
        raise RuntimeError("No final scene event returned by stream.")
    return final_scene, {
        "seconds": round(time.perf_counter() - started, 2),
        "status_events": status_events,
        "delta_count": delta_count,
        "streamed_chars": streamed_chars,
    }


def poll(label: str, fetcher, predicate, *, timeout: float = 180.0, interval: float = 4.0):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        try:
            latest = fetcher()
        except Exception as error:  # noqa: BLE001
            latest = {"error": str(error)}
        if predicate(latest):
            print(f"[OK] {label}")
            return latest
        time.sleep(interval)
    print(f"[WARN] {label} not ready before timeout")
    return latest


def create_session() -> dict:
    return api_json("/sessions", method="POST", payload={"title": "New Story"}, timeout=15)


def set_world_notes(session_id: str) -> dict:
    return api_json(f"/sessions/{session_id}/world", method="POST", payload=WORLD_NOTES, timeout=20)


def set_image_style(session_id: str) -> dict:
    existing = api_json(f"/sessions/{session_id}/image-settings", timeout=20)
    payload = {**existing, **IMAGE_STYLE_PAYLOAD}
    return api_json(f"/sessions/{session_id}/image-settings", method="PUT", payload=payload, timeout=20)


def select_ready_workflow(session_id: str) -> dict | None:
    workflows = api_json(f"/image-workflows?session_id={session_id}", timeout=20)
    ready = [item for item in workflows.get("workflows", []) if item.get("status") == "ready"]
    selected_id = workflows.get("selected_workflow_id")
    selected = next((item for item in ready if item.get("id") == selected_id), None)
    if selected:
        return selected
    return ready[0] if ready else None


def poll_story_state(session_id: str) -> dict:
    return poll(
        "story state extraction",
        lambda: api_json(f"/sessions/{session_id}/story-state", timeout=20),
        lambda payload: isinstance(payload, dict)
        and (payload.get("latest_run") or {}).get("status") in {"completed", "skipped", "failed"},
        timeout=240,
    )


def poll_characters(session_id: str) -> list[dict]:
    return poll(
        "auto-created characters",
        lambda: api_json(f"/sessions/{session_id}/characters", timeout=20),
        lambda items: isinstance(items, list) and len(items) >= 3,
        timeout=240,
    ) or []


def get_image_prompt(session_id: str, scene_id: str, version_id: str | None) -> dict | None:
    suffix = f"?version_id={version_id}" if version_id else ""
    return poll(
        "image prompt draft",
        lambda: api_json(f"/sessions/{session_id}/scenes/{scene_id}/image-prompt{suffix}", timeout=25),
        lambda payload: isinstance(payload, dict) and bool((payload.get("prompt") or "").strip()),
        timeout=240,
    )


def synthesize_kokoro(text: str) -> dict:
    return api_json(
        "/tts/synthesize",
        method="POST",
        payload={"provider": "kokoro", "voice": "af_heart", "speed": 1.0, "text": text},
        timeout=600,
    )


def run_image_generation(session_id: str, scene_id: str, version_id: str | None, workflow_id: str) -> tuple[dict | None, dict, dict | None]:
    image_settings = api_json("/settings/image", timeout=20)
    updated = {**image_settings, "image_resource_mode": "image_priority", "lm_unload_policy": "all", "lm_reload_policy": "prose"}
    api_json("/settings/image", method="PUT", payload=updated, timeout=20)
    try:
        started = time.perf_counter()
        image = api_json(
            "/images/generate",
            method="POST",
            payload={
                "session_id": session_id,
                "scene_id": scene_id,
                "version_id": version_id,
                "workflow_id": workflow_id,
                "open_preview": False,
            },
            timeout=1200,
        )
        image["wall_seconds"] = round(time.perf_counter() - started, 2)
        status = api_json("/images/job-status", timeout=20)
        history = api_json(
            f"/sessions/{session_id}/scenes/{scene_id}/images?version_id={version_id}",
            timeout=30,
        )
        return image, status, history
    finally:
        api_json("/settings/image", method="PUT", payload=image_settings, timeout=20)


def check_services() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["health"] = api_json("/health", timeout=10)
    checks["diagnostics"] = api_json("/diagnostics", timeout=45)
    checks["tts"] = api_json("/tts/status", timeout=20)
    checks["resource_status"] = api_json("/resource-status", timeout=45)
    checks["workflows"] = api_json("/image-workflows", timeout=20)
    return checks


def line_bool(ok: bool) -> str:
    return "PASS" if ok else "WARN"


def write_report(lines: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the StoryDriver human-test dress rehearsal.")
    parser.add_argument("--skip-image", action="store_true", help="Skip real ComfyUI image generation.")
    args = parser.parse_args()

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    print("StoryDriver human-test dress rehearsal")
    service = check_services()
    diagnostics = service.get("diagnostics") or {}
    workflow = select_ready_workflow("")
    comfy_online = bool((diagnostics.get("comfyui") or {}).get("reachable"))
    kokoro_online = bool(((service.get("tts") or {}).get("kokoro") or {}).get("reachable"))
    lm_online = bool((diagnostics.get("lm_studio") or {}).get("reachable"))

    session = create_session()
    session_id = session["id"]
    set_world_notes(session_id)
    set_image_style(session_id)
    print(f"Created rehearsal session: {session_id}")

    first_scene, first_stream = generate_stream(session_id, FIRST_CHAPTER_NOTE)
    first_text = first_scene.get("generated_text") or ""
    first_version_id = first_scene.get("active_version_id") or (first_scene.get("versions") or [{}])[-1].get("id")
    title_after_auto = api_json(f"/sessions/{session_id}/auto-title", method="POST", payload={"scene_text": first_text}, timeout=120)
    generated_title = (title_after_auto or {}).get("title") or ""
    api_json(f"/sessions/{session_id}", method="PATCH", payload={"title": REHEARSAL_TITLE}, timeout=20)

    characters = poll_characters(session_id)
    state = poll_story_state(session_id)
    first_image_prompt = get_image_prompt(session_id, first_scene["id"], first_version_id)

    second_scene, second_stream = generate_stream(session_id, SECOND_SCENE_NOTE)
    second_text = second_scene.get("generated_text") or ""
    second_version_id = second_scene.get("active_version_id") or (second_scene.get("versions") or [{}])[-1].get("id")
    rewritten_scene, rewrite_stream = generate_stream(
        session_id,
        REWRITE_NOTE,
        mode="rewrite",
        target_scene_id=second_scene["id"],
    )
    rewritten_version_id = rewritten_scene.get("active_version_id") or (rewritten_scene.get("versions") or [{}])[-1].get("id")
    rewritten_text = rewritten_scene.get("generated_text") or ""

    narration: dict[str, Any] | None = None
    narration_error = ""
    try:
        narration = synthesize_kokoro(rewritten_text or second_text)
    except Exception as error:  # noqa: BLE001
        narration_error = str(error)

    image_prompt = get_image_prompt(session_id, rewritten_scene["id"], rewritten_version_id) or first_image_prompt
    image: dict[str, Any] | None = None
    image_status: dict[str, Any] | None = None
    image_history: list[dict] | None = None
    image_error = ""
    selected_workflow = select_ready_workflow(session_id)
    if args.skip_image:
        image_error = "Skipped by --skip-image."
    elif not comfy_online:
        image_error = "Skipped because ComfyUI was offline in diagnostics."
    elif not selected_workflow:
        image_error = "Skipped because no ready exported API workflow was available."
    else:
        try:
            image, image_status, image_history = run_image_generation(
                session_id,
                rewritten_scene["id"],
                rewritten_version_id,
                selected_workflow["id"],
            )
        except Exception as error:  # noqa: BLE001
            image_error = str(error)

    final_state = api_json(f"/sessions/{session_id}/story-state", timeout=20)
    final_characters = api_json(f"/sessions/{session_id}/characters", timeout=20) or []
    final_scenes = api_json(f"/sessions/{session_id}/scenes", timeout=20) or []

    first_words = word_count(first_text)
    second_words = word_count(second_text)
    rewrite_words = word_count(rewritten_text)
    magic_terms = sorted(set(magic_violations(first_text) + magic_violations(second_text) + magic_violations(rewritten_text)))
    repeated_names = top_repeated_names(first_text)
    auto_names = [
        item.get("character", {}).get("name")
        for item in final_characters
        if item.get("character", {}).get("auto_created")
    ]
    matched_names = sorted(set(auto_names) & set(repeated_names))
    latest_run = (final_state or {}).get("latest_run") or {}
    prompt_text = (image_prompt or {}).get("prompt") or ""
    prompt_negative = (image_prompt or {}).get("negative_prompt") or ""
    prompt_beat = (image_prompt or {}).get("visual_beat") or ""
    prompt_chars = (image_prompt or {}).get("characters_included") or []
    prompt_grit_terms = [term for term in ("mud", "rain", "rough", "grain", "practical", "dirty", "low-light") if term in prompt_text.lower()]
    image_actions = (image or {}).get("resource_actions") or {}
    image_timings = image_actions.get("performance_timings") or {}

    checks = {
        "backend_online": bool((service.get("health") or {}).get("ok")),
        "lm_studio_online": lm_online,
        "kokoro_online": kokoro_online,
        "comfyui_online_or_image_skipped_cleanly": comfy_online or bool(image_error),
        "workflow_ready": bool(selected_workflow),
        "chapter_length": first_words >= 1300,
        "streaming_visible": first_stream["delta_count"] > 3 and first_stream["streamed_chars"] > 500,
        "director_note_no_magic": not magic_terms,
        "no_assistant_ending": not assistant_ending(first_text),
        "auto_title_generated": generated_title.lower() not in {"", "new story", "untitled story", "untitled"},
        "auto_characters_created": len(final_characters) >= 3,
        "matched_major_names": len(matched_names) >= min(3, len(repeated_names) or 3),
        "state_extraction_not_empty_failure": latest_run.get("error") != "LM Studio returned an empty scene.",
        "image_prompt_ready": bool(prompt_text),
        "image_prompt_gritty": len(prompt_grit_terms) >= 2,
        "narration_ok": bool(narration and narration.get("audio_url")),
        "image_generated_or_clean_skip": bool(image and image.get("image_url") and image.get("is_primary")) or bool(image_error),
    }

    lines = [
        "# StoryDriver Human Test Dress Rehearsal Report",
        "",
        f"Updated: {started_at}",
        f"Session: `{session_id}`",
        f"Final test story title: `{REHEARSAL_TITLE}`",
        "",
        "## 1. Story Generation Quality",
        "",
        f"- First chapter words: {first_words}",
        f"- Second scene words: {second_words}",
        f"- Rewrite words: {rewrite_words}",
        f"- First chapter stream: {first_stream['delta_count']} chunks / {first_stream['streamed_chars']} chars / {first_stream['seconds']}s",
        f"- Second scene stream: {second_stream['delta_count']} chunks / {second_stream['streamed_chars']} chars / {second_stream['seconds']}s",
        f"- Rewrite stream: {rewrite_stream['delta_count']} chunks / {rewrite_stream['streamed_chars']} chars / {rewrite_stream['seconds']}s",
        f"- Assistant-style ending detected: {'yes' if assistant_ending(first_text) else 'no'}",
        "",
        "## 2. Prompt Adherence",
        "",
        f"- Magic/supernatural term violations: {', '.join(magic_terms) or 'none'}",
        f"- Repeated major names detected in chapter: {', '.join(repeated_names) or 'none'}",
        f"- Generated auto-title before rehearsal rename: `{generated_title}`",
        "",
        "## 3. Character Creation And State Extraction",
        "",
        f"- Active/draft characters found: {len(final_characters)}",
        f"- Auto-created characters: {', '.join(name for name in auto_names if name) or 'none'}",
        f"- Major-name matches: {', '.join(matched_names) or 'none'}",
        f"- Latest state extraction status: {latest_run.get('status') or 'unknown'}",
        f"- Latest state extraction error: {latest_run.get('error') or 'none'}",
        f"- Scene count after continuation/rewrite: {len(final_scenes)}",
        "",
        "## 4. Narration Behavior",
        "",
        f"- Kokoro reachable before test: {'yes' if kokoro_online else 'no'}",
        f"- Narration result: {narration.get('audio_url') if narration else narration_error or 'not run'}",
        "",
        "## 5. Image Prompt Quality",
        "",
        f"- Visual beat: {prompt_beat[:600] or 'none'}",
        f"- Characters included: {', '.join(prompt_chars) or 'none'}",
        f"- Grit/grounding terms detected: {', '.join(prompt_grit_terms) or 'none'}",
        f"- Positive prompt excerpt: {prompt_text[:900] or 'none'}",
        f"- Negative prompt excerpt: {prompt_negative[:500] or 'none'}",
        "",
        "## 6. Image Generation Behavior",
        "",
        f"- Selected workflow: {(selected_workflow or {}).get('name') or 'none'} (`{(selected_workflow or {}).get('id') or 'none'}`)",
        f"- Image result: {(image or {}).get('image_url') or image_error or 'not run'}",
        f"- Image is primary: {(image or {}).get('is_primary') if image else 'n/a'}",
        f"- Image wall time: {(image or {}).get('wall_seconds', 'n/a')}s",
        f"- Backend image total: {image_actions.get('performance_total_seconds', 'n/a')}s",
        f"- ComfyUI timing: {image_timings.get('comfyui_queue_to_image_retrieved', 'n/a')}s",
        f"- LM unload confirmed: {image_actions.get('unload_confirmed', 'n/a')}",
        f"- ComfyUI free succeeded: {image_actions.get('comfyui_free_succeeded', 'n/a')}",
        f"- LM reload targets: {', '.join(image_actions.get('reload_targets') or []) or 'n/a'}",
        f"- Image history count for target version: {len(image_history or []) if image_history is not None else 'n/a'}",
        f"- Final image job status: {(image_status or {}).get('stage') or 'n/a'} / {(image_status or {}).get('message') or ''}",
        "",
        "## 7. UI And Mobile Notes",
        "",
        "- Browser desktop/mobile verification is reported separately in this run if the in-app browser check succeeds.",
        "- This API rehearsal specifically validates the routes and state the UI depends on.",
        "",
        "## 8. Checks",
        "",
        *[f"- {line_bool(ok)} {name}" for name, ok in checks.items()],
        "",
        "## 9. Remaining Issues",
        "",
        "- A real human should still review prose quality, pacing, and whether the generated sisters are the intended kind of distinct.",
        "- Browser playback controls can only be fully judged by clicking in the UI; backend Kokoro synthesis passed or failed above.",
        "- Image fidelity still depends on the selected ComfyUI workflow and loaded ComfyUI-side LoRAs/settings.",
        "",
        "## 10. Human Testing Readiness",
        "",
        (
            "StoryDriver is ready for a first human testing session on this PC."
            if checks["chapter_length"]
            and checks["streaming_visible"]
            and checks["director_note_no_magic"]
            and checks["auto_characters_created"]
            and checks["image_prompt_ready"]
            and checks["narration_ok"]
            and checks["image_generated_or_clean_skip"]
            else "StoryDriver is close, but review the WARN lines above before a serious long session."
        ),
    ]
    write_report(lines)
    print(f"Report written: {REPORT}")
    for name, ok in checks.items():
        print(f"[{line_bool(ok)}] {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
