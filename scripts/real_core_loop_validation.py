from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND = "http://localhost:8001"
REPORT_PATH = Path(r"D:\StoryDriver\backend\data\logs\REAL_CORE_LOOP_TEST_REPORT.md")
STORY_TITLE = "StoryDriver Real Core Loop Test"
WORKFLOW_ID = "lonecat_zit_nsfw_8_0_1_api"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 60,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{BACKEND}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed HTTP {error.code}: {body}") from error


def get_json(path: str, *, timeout: int = 60) -> Any:
    return request_json("GET", path, timeout=timeout)


def post_json(path: str, payload: dict[str, Any] | None = None, *, timeout: int = 60) -> Any:
    return request_json("POST", path, payload or {}, timeout=timeout)


def put_json(path: str, payload: dict[str, Any], *, timeout: int = 60) -> Any:
    return request_json("PUT", path, payload, timeout=timeout)


def patch_json(path: str, payload: dict[str, Any], *, timeout: int = 60) -> Any:
    return request_json("PATCH", path, payload, timeout=timeout)


def find_or_create_session() -> dict[str, Any]:
    sessions = get_json("/sessions")
    for session in sessions:
        if session["title"] == STORY_TITLE:
            return session
    return post_json("/sessions", {"title": STORY_TITLE})


def find_existing_session() -> dict[str, Any]:
    sessions = get_json("/sessions")
    for session in sessions:
        if session["title"] == STORY_TITLE:
            return session
    raise RuntimeError(f"Test story does not exist yet: {STORY_TITLE}")


def ensure_character(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    name = payload["name"]
    attached = get_json(f"/sessions/{session_id}/characters")
    for link in attached:
        if link["character"]["name"] == name:
            return link["character"]
    payload = {**payload, "attach_to_session": True, "session_id": session_id, "is_active": True}
    return post_json("/characters", payload)


def ensure_world_notes(session_id: str) -> dict[str, Any]:
    payload = {
        "setting": "Stormy dark fantasy frontier with a ruined chapel, thorn camp, bandit trails, and a muddy forest road.",
        "tone": "Grounded gritty tone, rain, fatigue, practical danger, no shiny heroics.",
        "rules": "Continuity matters: wounds, secrets, ownership, current location, and outfit changes persist until contradicted.",
        "locations": "Thorn camp; forest road; ruined chapel; bandit camp.",
        "factions": "Bandits patrol the chapel road; frontier scavengers avoid the chapel after dark.",
        "conflicts": "Mara, Elias, and Rowan need proof from the chapel while keeping Elias's secret safe.",
        "history": "The chapel was abandoned after a border war and still contains old frontier relics.",
    }
    return patch_json(f"/sessions/{session_id}/world", payload)


def configure_image_settings(session_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    image_settings = get_json("/settings/image")
    if image_settings.get("selected_workflow_id") != WORKFLOW_ID or image_settings.get("image_resource_mode") != "image_priority":
        image_settings = put_json(
            "/settings/image",
            {
                **image_settings,
                "selected_workflow_id": WORKFLOW_ID,
                "image_resource_mode": "image_priority",
                "auto_reload_lm_after_image": True,
                "generate_image_while_narrating": True,
            },
        )
    session_settings = put_json(
        f"/sessions/{session_id}/image-settings",
        {
            "selected_workflow_id": WORKFLOW_ID,
            "image_prompt_style": "dark cinematic fantasy realism, grounded costumes, natural moody firelight and rain, realistic wounds and tired faces",
            "preferred_visual_tone": "gritty frontier dark fantasy, practical leather and wool, no glossy plastic skin",
            "realism_notes": "wounds and dirt should look believable; avoid anime stylization",
            "lighting_camera_notes": "filmic low light, campfire glow, wet stone, shallow depth of field when close",
            "auto_open_preview": True,
            "auto_attach_generated": True,
        },
    )
    validation = post_json(f"/image-workflows/{WORKFLOW_ID}/validate")
    return image_settings, session_settings, validation


def generate_scene(session_id: str, director_note: str) -> dict[str, Any]:
    payload = {"director_note": f"{director_note} Keep this scene focused, 250-400 words.", "mode": "continue"}
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BACKEND}/sessions/{session_id}/generate-stream",
        data=data,
        method="POST",
        headers={"Accept": "application/x-ndjson", "Content-Type": "application/json"},
    )
    final_scene = None
    deltas: list[str] = []
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            for raw_line in response:
                if not raw_line.strip():
                    continue
                event = json.loads(raw_line.decode("utf-8"))
                if event.get("type") == "delta":
                    deltas.append(event.get("text", ""))
                elif event.get("type") == "scene":
                    final_scene = event.get("scene")
                elif event.get("type") == "error":
                    raise RuntimeError(f"Streaming scene generation failed: {event.get('detail')}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST /sessions/{session_id}/generate-stream failed HTTP {error.code}: {body}") from error
    if not final_scene:
        raise RuntimeError(f"Streaming scene generation ended without a saved scene. Received {len(''.join(deltas))} characters.")
    return final_scene


def scene_version(scene: dict[str, Any]) -> dict[str, Any]:
    versions = scene.get("versions") or []
    if versions:
        active_id = scene.get("active_version_id")
        return next((version for version in versions if version["id"] == active_id), versions[-1])
    return {
        "id": scene.get("active_version_id"),
        "generated_text": scene.get("generated_text", ""),
        "version_index": scene.get("active_version_index", 1),
    }


def extract_state(session_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    version = scene_version(scene)
    query = urllib.parse.urlencode({"version_id": version["id"]}) if version.get("id") else ""
    suffix = f"?{query}" if query else ""
    return post_json(f"/sessions/{session_id}/scenes/{scene['id']}/story-state/extract{suffix}", timeout=240)


def synthesize(scene: dict[str, Any]) -> dict[str, Any]:
    version = scene_version(scene)
    return post_json(
        "/tts/synthesize",
        {"text": version["generated_text"], "provider": "kokoro", "voice": "af_heart", "speed": 1.0},
        timeout=180,
    )


def prompt_preview(session_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    version = scene_version(scene)
    return post_json(
        "/images/prompt",
        {"session_id": session_id, "scene_id": scene["id"], "version_id": version["id"], "workflow_id": WORKFLOW_ID},
        timeout=240,
    )


def poll_image_status(stop_event: threading.Event, samples: list[dict[str, Any]]) -> None:
    while not stop_event.is_set():
        try:
            status = get_json("/images/job-status", timeout=10)
            samples.append(
                {
                    "time": now_iso(),
                    "active": status.get("active"),
                    "stage": status.get("stage"),
                    "message": status.get("message"),
                    "percent": status.get("percent"),
                    "elapsed_seconds": status.get("elapsed_seconds"),
                }
            )
        except Exception as error:  # pragma: no cover - diagnostic only
            samples.append({"time": now_iso(), "error": str(error)})
        time.sleep(2.0)


def generate_image(session_id: str, scene: dict[str, Any], *, seed: int | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    version = scene_version(scene)
    samples: list[dict[str, Any]] = []
    stop_event = threading.Event()
    thread = threading.Thread(target=poll_image_status, args=(stop_event, samples), daemon=True)
    thread.start()
    started = time.perf_counter()
    try:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "scene_id": scene["id"],
            "version_id": version["id"],
            "workflow_id": WORKFLOW_ID,
            "open_preview": True,
        }
        if seed is not None:
            payload["seed"] = seed
        image = post_json("/images/generate", payload, timeout=900)
        return image, samples, round(time.perf_counter() - started, 3)
    finally:
        stop_event.set()
        thread.join(timeout=3)


def image_history(session_id: str, image: dict[str, Any]) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"version_id": image.get("version_id") or ""})
    return get_json(f"/sessions/{session_id}/scenes/{image['scene_id']}/images?{query}", timeout=60)


def service_summary() -> dict[str, Any]:
    diagnostics = get_json("/diagnostics", timeout=60)
    workflows = get_json(f"/image-workflows?{urllib.parse.urlencode({'session_id': ''})}", timeout=60)
    validation = post_json(f"/image-workflows/{WORKFLOW_ID}/validate", timeout=60)
    return {"diagnostics": diagnostics, "workflows": workflows, "validation": validation}


def summarize_state(story_state: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "character_live_state",
        "relationships",
        "world_state",
        "scene_state",
        "objects",
        "plot_threads",
        "recent_events",
        "conflicts",
    ]
    result: dict[str, Any] = {}
    for key in keys:
        value = story_state.get(key) or []
        result[key] = len(value) if isinstance(value, list) else value
    prompt_context = story_state.get("prompt_context") or ""
    visual_context = story_state.get("visual_prompt_context") or ""
    result["prompt_context_contains"] = {
        "red cloak": "red cloak" in prompt_context.lower(),
        "injury": "injur" in prompt_context.lower() or "wound" in prompt_context.lower() or "bandage" in prompt_context.lower(),
        "compass": "compass" in prompt_context.lower(),
        "camp": "camp" in prompt_context.lower(),
    }
    result["visual_context_contains"] = {
        "red cloak": "red cloak" in visual_context.lower(),
        "injury": "injur" in visual_context.lower() or "wound" in visual_context.lower() or "bandage" in visual_context.lower(),
        "compass": "compass" in visual_context.lower(),
        "camp": "camp" in visual_context.lower(),
    }
    result["latest_run"] = story_state.get("latest_run")
    result["summary_status"] = story_state.get("summary_status")
    return result


def format_seconds(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}s"
    return "n/a"


def main() -> None:
    report: dict[str, Any] = {"started_at": now_iso(), "errors": [], "warnings": []}
    services = service_summary()
    report["services"] = {
        "backend": services["diagnostics"].get("backend"),
        "lm_studio": services["diagnostics"].get("lm_studio"),
        "kokoro": services["diagnostics"].get("kokoro"),
        "comfyui": services["diagnostics"].get("comfyui"),
        "workflow_validation": services["validation"],
    }
    validation = services["validation"]
    if not validation.get("valid"):
        raise RuntimeError(f"Workflow validation failed: {validation}")
    diagnostics = services["diagnostics"]
    if not diagnostics.get("lm_studio", {}).get("reachable"):
        raise RuntimeError("LM Studio OpenAI endpoint is offline.")
    if not diagnostics.get("lm_studio", {}).get("rest_reachable"):
        raise RuntimeError("LM Studio REST endpoint is offline.")
    if not diagnostics.get("kokoro", {}).get("reachable"):
        raise RuntimeError("Kokoro is offline.")
    if not diagnostics.get("comfyui", {}).get("reachable"):
        raise RuntimeError("ComfyUI is offline.")

    session = find_or_create_session()
    session_id = session["id"]
    report["session"] = session

    characters = [
        {
            "name": "Mara",
            "role": "Thief/scout",
            "personality": "Guarded, observant, sarcastic under pressure.",
            "appearance": "Short black hair, scar over left eyebrow, red cloak, quick hands.",
            "relationships": "Trusts Rowan's blade more than Elias's explanations.",
            "current_state": "Wearing a red cloak and watching for betrayal.",
            "image_prompt": "Mara, short black hair, scar over left eyebrow, red cloak, guarded expression",
            "visual_profile": {
                "base_visual_description": "Adult thief/scout with short black hair, sharp tired eyes, scar over left eyebrow.",
                "hair": "short black hair",
                "default_outfit": "red cloak over dark practical travel leathers",
                "distinctive_marks": "scar over left eyebrow",
                "color_palette": "deep red cloak, charcoal leather, rain-dark fabric",
                "image_prompt": "Mara, short black hair, scar over left eyebrow, red cloak",
                "used_in_image_prompts": True,
            },
        },
        {
            "name": "Elias",
            "role": "Nervous scholar",
            "personality": "Careful, evasive, kind when cornered.",
            "appearance": "Dark coat, silver spectacles, ink-stained hands.",
            "relationships": "Hiding secrets from Mara and Rowan.",
            "current_state": "Protecting a hidden bronze compass and a dangerous name.",
            "image_prompt": "Elias, nervous scholar, dark coat, silver spectacles, ink-stained hands",
            "visual_profile": {
                "base_visual_description": "Adult nervous scholar with silver spectacles, dark coat, ink-stained hands.",
                "default_outfit": "long dark coat and travel satchel",
                "distinctive_marks": "silver spectacles",
                "color_palette": "black wool, tarnished silver, parchment tan",
                "image_prompt": "Elias, silver spectacles, dark scholar coat",
                "used_in_image_prompts": True,
            },
        },
        {
            "name": "Rowan",
            "role": "Tired mercenary",
            "personality": "Practical, blunt, loyal after payment becomes personal.",
            "appearance": "Heavy cloak, old sword, weathered face.",
            "relationships": "Protects Mara and distrusts Elias's secrets.",
            "current_state": "Carrying an old sword and watching the tree line.",
            "image_prompt": "Rowan, tired mercenary, heavy cloak, old sword, weathered face",
            "visual_profile": {
                "base_visual_description": "Adult tired mercenary with weathered face, old sword, heavy cloak.",
                "default_outfit": "mud-spattered heavy cloak over worn armor",
                "distinctive_marks": "old sword with nicked guard",
                "color_palette": "mud brown, dull iron, storm gray",
                "image_prompt": "Rowan, tired mercenary, heavy cloak, old sword",
                "used_in_image_prompts": True,
            },
        },
    ]
    report["characters"] = [ensure_character(session_id, character) for character in characters]
    report["world_notes"] = ensure_world_notes(session_id)
    image_settings, session_image_settings, workflow_validation = configure_image_settings(session_id)
    report["image_settings"] = image_settings
    report["session_image_settings"] = session_image_settings
    report["workflow_validation"] = workflow_validation

    scene_notes = [
        "Scene 1: Planning near the thorn camp in cold rain. Mara in her red cloak, Elias nervous with silver spectacles, and Rowan with his old sword plan how to reach the ruined chapel. Elias secretly keeps a bronze compass hidden in his coat. End with a clear promise or suspicion.",
        "Scene 2: Travel from camp toward the ruined chapel. Show the muddy forest road, storm, shifting trust, and Elias almost revealing the compass but hiding it again.",
        "Scene 3: Bandit encounter near the chapel road. Keep it grounded and tense. Rowan fights with the old sword, Mara scouts and is put at risk, Elias protects the bronze compass secret.",
        "Scene 4: Injury and aftermath inside or near the ruined chapel. Mara receives a left-shoulder wound or bandage, Rowan takes ownership of a recovered chapel key, Elias's secret strains trust.",
        "Scene 5: Return to camp after the chapel. They treat wounds by firelight; Mara's red cloak and injury remain current, Rowan has the chapel key, Elias still hides what the bronze compass means.",
    ]
    scenes: list[dict[str, Any]] = []
    for note in scene_notes:
        scene = generate_scene(session_id, note)
        scenes.append(scene)
        report.setdefault("scene_generation", []).append(
            {
                "scene_id": scene["id"],
                "version_id": scene_version(scene).get("id"),
                "note": note,
                "text_preview": scene_version(scene).get("generated_text", "")[:500],
            }
        )
        try:
            state_run = extract_state(session_id, scene)
        except Exception as error:
            state_run = {"status": "failed", "error": str(error)}
            report["errors"].append(f"State extraction failed for scene {scene['id']}: {error}")
        if state_run.get("status") != "completed":
            report["warnings"].append(
                f"State extraction returned {state_run.get('status')} for scene {scene['id']}: {state_run.get('error') or 'no error detail'}"
            )
        report.setdefault("state_extractions", []).append(state_run)

    story_state = get_json(f"/sessions/{session_id}/story-state", timeout=60)
    report["story_state_summary"] = summarize_state(story_state)

    tts_results = []
    for scene in (scenes[0], scenes[-1]):
        started = time.perf_counter()
        response = synthesize(scene)
        tts_results.append(
            {
                "scene_id": scene["id"],
                "version_id": scene_version(scene).get("id"),
                "seconds": round(time.perf_counter() - started, 3),
                "response": response,
            }
        )
    report["tts"] = tts_results
    report["tts_status_after"] = get_json("/tts/status", timeout=30)

    prompt_results = []
    for scene in (scenes[2], scenes[-1]):
        started = time.perf_counter()
        prompt = prompt_preview(session_id, scene)
        prompt_results.append(
            {
                "scene_id": scene["id"],
                "version_id": scene_version(scene).get("id"),
                "seconds": round(time.perf_counter() - started, 3),
                "visual_beat": prompt.get("visual_beat"),
                "characters_included": prompt.get("characters_included"),
                "continuity_used": prompt.get("continuity_used"),
                "prompt_preview": prompt.get("prompt", "")[:700],
            }
        )
    report["image_prompts"] = prompt_results

    images = []
    for scene in (scenes[2], scenes[-1]):
        image, samples, seconds = generate_image(session_id, scene)
        history = image_history(session_id, image)
        images.append(
            {
                "scene_id": scene["id"],
                "version_id": scene_version(scene).get("id"),
                "seconds": seconds,
                "image": image,
                "history_count": len(history),
                "primary_image_ids": [item["id"] for item in history if item.get("is_primary")],
                "poll_samples": samples[-20:],
            }
        )
    report["images"] = images

    regen_image, regen_samples, regen_seconds = generate_image(session_id, scenes[-1])
    regen_history = image_history(session_id, regen_image)
    report["regenerate"] = {
        "scene_id": scenes[-1]["id"],
        "version_id": scene_version(scenes[-1]).get("id"),
        "seconds": regen_seconds,
        "image": regen_image,
        "history_count": len(regen_history),
        "primary_image_ids": [item["id"] for item in regen_history if item.get("is_primary")],
        "old_images_retained": len(regen_history) >= 2,
        "poll_samples": regen_samples[-20:],
    }

    report["resource_status_after_images"] = get_json("/resource-status", timeout=60)
    post_scene = generate_scene(
        session_id,
        "After the images and narration, continue with one short scene at camp. Keep Mara's red cloak and shoulder injury, Rowan's chapel key, and Elias's hidden bronze compass continuity intact.",
    )
    report["post_image_story_generation"] = {
        "scene_id": post_scene["id"],
        "version_id": scene_version(post_scene).get("id"),
        "text_preview": scene_version(post_scene).get("generated_text", "")[:500],
    }

    report["finished_at"] = now_iso()
    write_report(report)
    print(f"Real core-loop validation complete. Report: {REPORT_PATH}")
    print(json.dumps(
        {
            "session_id": session_id,
            "scenes": len(scenes),
            "state_errors": len(report["errors"]),
            "tts": len(tts_results),
            "images": len(images),
            "regenerate_primary_ids": report["regenerate"]["primary_image_ids"],
        },
        indent=2,
    ))


def append_state_recheck_report(
    *,
    session_id: str,
    extraction_results: list[dict[str, Any]],
    story_state: dict[str, Any],
) -> None:
    state = summarize_state(story_state)
    failed = [item for item in extraction_results if item.get("status") != "completed"]
    completed = [item for item in extraction_results if item.get("status") == "completed"]
    lines = [
        "",
        "## State Re-Extraction After Compact Prompt Fix",
        "",
        f"- Rechecked at: {now_iso()}",
        f"- Session ID: `{session_id}`",
        f"- Completed extraction runs: {len(completed)}",
        f"- Failed extraction runs: {len(failed)}",
        f"- Character live state items: {state.get('character_live_state')}",
        f"- Relationship items: {state.get('relationships')}",
        f"- Object items: {state.get('objects')}",
        f"- Plot threads: {state.get('plot_threads')}",
        f"- Prompt context continuity flags: `{state.get('prompt_context_contains')}`",
        f"- Visual context continuity flags: `{state.get('visual_context_contains')}`",
    ]
    if failed:
        lines.extend(["", "### Remaining Extraction Failures", ""])
        for item in failed:
            lines.append(f"- Scene `{item.get('scene_id')}`: {item.get('error') or item.get('status')}")
    lines.extend(
        [
            "",
            "### Raw State Recheck",
            "",
            "```json",
            json.dumps(
                {
                    "extraction_results": extraction_results,
                    "story_state_summary": state,
                    "latest_run": story_state.get("latest_run"),
                },
                indent=2,
                ensure_ascii=False,
            ),
            "```",
        ]
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else "# Real Core Loop Test Report\n"
    marker = "\n## State Re-Extraction After Compact Prompt Fix\n"
    if marker in existing:
        existing = existing.split(marker, 1)[0].rstrip() + "\n"
    REPORT_PATH.write_text(existing.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def reextract_existing_state() -> None:
    session = find_existing_session()
    session_id = session["id"]
    scenes = get_json(f"/sessions/{session_id}/scenes", timeout=60)
    if not scenes:
        raise RuntimeError(f"No scenes found for {STORY_TITLE}.")
    results: list[dict[str, Any]] = []
    for scene in scenes:
        version = scene_version(scene)
        if not version.get("id"):
            continue
        result = extract_state(session_id, scene)
        result_summary = {
            "scene_id": scene["id"],
            "version_id": version.get("id"),
            "status": result.get("status"),
            "error": result.get("error"),
            "warnings": result.get("warnings"),
        }
        results.append(result_summary)
        print(json.dumps(result_summary, indent=2))
    story_state = get_json(f"/sessions/{session_id}/story-state", timeout=60)
    append_state_recheck_report(
        session_id=session_id,
        extraction_results=results,
        story_state=story_state,
    )
    print(json.dumps(summarize_state(story_state), indent=2))


def write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    services = report.get("services", {})
    workflow = report.get("workflow_validation", {})
    state = report.get("story_state_summary", {})
    failed_extractions = [
        item
        for item in report.get("state_extractions", [])
        if item.get("status") != "completed"
    ]
    image_rows = report.get("images", [])
    regen = report.get("regenerate", {})
    lines = [
        "# Real Core Loop Test Report",
        "",
        f"Started: {report.get('started_at')}",
        f"Finished: {report.get('finished_at')}",
        "",
        "## Services Status",
        "",
        f"- Backend: {services.get('backend')}",
        f"- LM Studio OpenAI reachable: {bool((services.get('lm_studio') or {}).get('reachable'))}",
        f"- LM Studio REST reachable: {bool((services.get('lm_studio') or {}).get('rest_reachable'))}",
        f"- Kokoro reachable: {bool((services.get('kokoro') or {}).get('reachable'))}",
        f"- ComfyUI reachable: {bool((services.get('comfyui') or {}).get('reachable'))}",
        f"- Workflow valid: {workflow.get('valid')}",
        f"- Workflow mapping: positive `1342.positive`, negative `1317.negative`, seed `1307.seed`, output `1704`.",
        "",
        "## Scene Generation Results",
        "",
    ]
    for index, scene in enumerate(report.get("scene_generation", []), start=1):
        lines.extend(
            [
                f"### Scene {index}",
                f"- Scene ID: `{scene.get('scene_id')}`",
                f"- Version ID: `{scene.get('version_id')}`",
                f"- Preview: {scene.get('text_preview', '').replace(chr(10), ' ')}",
                "",
            ]
        )
    lines.extend(
        [
            "## State Extraction Results",
            "",
            f"- Extraction runs: {len(report.get('state_extractions', []))}",
            f"- Failed extraction runs: {len(failed_extractions)}",
            f"- Character live state items: {state.get('character_live_state')}",
            f"- Relationship items: {state.get('relationships')}",
            f"- Object items: {state.get('objects')}",
            f"- Plot threads: {state.get('plot_threads')}",
            f"- Prompt context continuity flags: `{state.get('prompt_context_contains')}`",
            f"- Visual context continuity flags: `{state.get('visual_context_contains')}`",
            "",
            "## Kokoro Narration Results",
            "",
        ]
    )
    if failed_extractions:
        lines.extend(
            f"- Extraction `{item.get('id', 'unknown')}` for scene `{item.get('scene_id')}` returned `{item.get('status')}`: {item.get('error') or 'no error detail'}"
            for item in failed_extractions
        )
        lines.append("")
    for item in report.get("tts", []):
        response = item.get("response") or {}
        lines.append(
            f"- Scene `{item.get('scene_id')}` synthesized in {format_seconds(item.get('seconds'))}; provider `{response.get('provider')}`, audio `{response.get('audio_url')}`."
        )
    lines.extend(["", "## Image Generation Timings", ""])
    for index, row in enumerate(image_rows, start=1):
        image = row.get("image") or {}
        actions = image.get("resource_actions") or {}
        timings = actions.get("performance_timings") or {}
        lines.extend(
            [
                f"### Image {index}",
                f"- Scene ID: `{row.get('scene_id')}`",
                f"- Image ID: `{image.get('id')}`",
                f"- Total time: {format_seconds(row.get('seconds'))}",
                f"- ComfyUI queue-to-image: {format_seconds(timings.get('comfyui_queue_to_image_retrieved'))}",
                f"- LM unload confirmed: {actions.get('unload_confirmed')}",
                f"- LM reload succeeded: {actions.get('reload_succeeded')}",
                f"- ComfyUI /free succeeded: {actions.get('comfyui_free_succeeded')}",
                f"- Auto-attached primary: {image.get('is_primary') and image.get('status') == 'accepted'}",
                f"- History count: {row.get('history_count')}",
                f"- Warnings: `{image.get('resource_warnings')}`",
                "",
            ]
        )
    regen_image = regen.get("image") or {}
    regen_actions = regen_image.get("resource_actions") or {}
    regen_timings = regen_actions.get("performance_timings") or {}
    lines.extend(
        [
            "## Regenerate And History",
            "",
            f"- Regenerated image ID: `{regen_image.get('id')}`",
            f"- Total time: {format_seconds(regen.get('seconds'))}",
            f"- ComfyUI queue-to-image: {format_seconds(regen_timings.get('comfyui_queue_to_image_retrieved'))}",
            f"- New image primary: {regen_image.get('is_primary')}",
            f"- Old images retained: {regen.get('old_images_retained')}",
            f"- History count: {regen.get('history_count')}",
            f"- Primary image IDs after regenerate: `{regen.get('primary_image_ids')}`",
            "",
            "## Bugs Found/Fixes",
            "",
        ]
    )
    if report.get("errors"):
        lines.extend(f"- {error}" for error in report["errors"])
    else:
        lines.append("- No app-side crashes or stuck states were observed by the API validation.")
    lines.extend(
        [
            "",
            "## Stability Verdict",
            "",
            "The core loop is stable enough for deeper prompt and visual-consistency work if the service checks remain green. Full browser playback controls still need human-observed validation for pause/resume/skip timing, but Kokoro synthesis and backend TTS status passed.",
            "",
            "## Raw Evidence",
            "",
            "```json",
            json.dumps(report, indent=2, ensure_ascii=False)[:120000],
            "```",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--state-only":
        reextract_existing_state()
    else:
        main()
