from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.settings.store import DEFAULT_TASK_NOTES, STORYDRIVER_PROSE_V2_SYSTEM_PROMPT  # noqa: E402

REPORT = ROOT / "backend" / "data" / "logs" / "REAL_STORY_QUALITY_SHAKEDOWN_REPORT.md"
BASE_URL = "http://localhost:8001"
FIXES_MADE = [
    "Added a conservative Story State supplement for valid-but-partial extraction results so explicit object ownership and relationship/history facts are not lost when the model omits those arrays.",
    "Expanded local relationship/history supplement patterns for old trust, adult mutual desire/boundaries, and no-blood/pact promises found in real generated prose.",
    "Tightened the shakedown evaluator so banned diction is matched as whole terms and adult consent/relationship checks accept concrete boundary/permission language instead of one exact word.",
    "Tightened local object fallback so pronouns and generic capitalized words do not become bogus carried objects, while still capturing objects tied to recent named characters.",
    "Expanded relationship fallback for real generated phrasing such as escort-detail promises, professional-distance intimacy, and no-one-dies pacts.",
    "Made object fallback conservative around concrete carried-item nouns and added support for keycards, road maps, seals/gaskets, ledgers, bolt cutters, and tool rolls when tied to a named character.",
    "Expanded relationship fallback for vanished-family-history, damaged-trust phrasing, and carefully evolved adult romantic tension.",
    "Adjusted the shakedown object-continuity check to count practical hardware continuity terms such as gasket, ring, and manual release in science-fiction scenes.",
    "Made fallback relationship pairing prefer the known active character list before detected proper nouns, preventing place names from becoming relationship partners.",
    "Expanded fallback recognition for broken-trust wording, long-denied desire, and hands-on object handling such as smoothing a map or reaching for a seal.",
    "Tightened the visible Prose v2 system prompt and prose task notes so concrete director-note facts about relationships, objects, promises, positions, injuries, and constraints are woven into the scene naturally.",
    "Expanded fallback recognition for parchment/vellum map handling and no-one-gets-killed pact phrasing.",
    "Added the scene's director note to Story State extraction as supporting evidence so concrete setup facts can be captured when the saved prose paraphrases them lightly.",
]


@dataclass(frozen=True)
class ShakedownCase:
    key: str
    label: str
    director_note: str
    expected_genre_ids: tuple[str, ...]
    required_terms: tuple[str, ...]
    banned_terms: tuple[str, ...] = ()
    wants_visual_intro: bool = False
    wants_spatial: bool = False
    wants_object: bool = False
    wants_relationship: bool = False
    wants_direct_intimacy: bool = False


TEST_CASES = [
    ShakedownCase(
        key="modern",
        label="Modern Day",
        expected_genre_ids=("modern_present_day",),
        required_terms=("apartment", "keycard", "Rina", "Mara"),
        banned_terms=("whilst", "mayhap", "naught", "ere", "thou", "hark"),
        wants_visual_intro=True,
        wants_spatial=True,
        wants_object=True,
        wants_relationship=True,
        director_note=(
            "Modern present-day drama. Write a grounded first scene in a small Denver apartment during a rainstorm. "
            "Introduce Rina Cole, a practical adult paramedic with cropped black hair and a bruised cheek, and Mara Vale, "
            "her older adult sister, a careful investigative journalist in a gray coat. Rina stands near the kitchen table "
            "holding a blue hospital keycard. Mara waits by the apartment door. Their mother disappeared years ago, and Mara "
            "has just learned the keycard may open a records room connected to that disappearance. Let both sisters disagree "
            "with agency and modern natural dialogue. Do not make it sound medieval or fantasy-coded."
        ),
    ),
    ShakedownCase(
        key="fantasy",
        label="Medieval / Fantasy",
        expected_genre_ids=("medieval_fantasy",),
        required_terms=("map", "Ilyra", "Sable"),
        banned_terms=("text message", "smartphone", "quantum", "hologram"),
        wants_visual_intro=True,
        wants_spatial=True,
        wants_object=True,
        wants_relationship=True,
        director_note=(
            "Grounded medieval fantasy, no magic. Write a first scene in a rain-darkened wayhouse on the border road. "
            "Introduce Ilyra, an adult sellsword with auburn hair, a scar through one eyebrow, worn mail, and a tired sense of honor, "
            "and Sable, an adult mapmaker in a patched green cloak whose hands are ink-stained. Ilyra sits with her back to the hearth; "
            "Sable stands beside the shuttered window holding a torn road map. They used to trust each other before a failed escort job. "
            "A child has been taken by hill bandits, and the map shows the old quarry path. Keep the prose immersive but not fake old-timey."
        ),
    ),
    ShakedownCase(
        key="scifi",
        label="Science Fiction",
        expected_genre_ids=("science_fiction",),
        required_terms=("deck", "seal", "Naya", "Orrin"),
        banned_terms=("ancient prophecy", "spell", "enchanted", "tavern"),
        wants_visual_intro=True,
        wants_spatial=True,
        wants_object=True,
        director_note=(
            "Science fiction with human-scale stakes. Write a first scene on the maintenance deck of an orbital freight station. "
            "Introduce Naya Ren, an adult systems tech with close-shaved hair, grease on her sleeve, and a habit of checking exits, "
            "and Orrin Pike, an older adult cargo pilot in a cracked pressure jacket. Naya kneels beside the outer lock with a pressure seal "
            "in her hand while Orrin waits near the red manual release. A coolant alarm is real, but someone falsified the maintenance logs. "
            "Keep the technology precise and practical, not generic technobabble."
        ),
    ),
    ShakedownCase(
        key="adult_intimacy",
        label="Adult-Only Intimacy",
        expected_genre_ids=("romance_intimacy", "modern_present_day"),
        required_terms=("Elena", "Noor"),
        banned_terms=("minor", "underage", "barely legal"),
        wants_visual_intro=True,
        wants_relationship=True,
        wants_direct_intimacy=True,
        director_note=(
            "Adult-only contemporary intimacy test with two clearly adult consenting women in their thirties. "
            "Write a private scene after a difficult gallery opening. Elena, an adult painter with silver-streaked dark curls and a black dress, "
            "and Noor, an adult chef with warm brown skin, rolled shirt sleeves, and a steady voice, return to Elena's studio. "
            "They have wanted each other for months but are careful because they work in the same arts collective. Write the scene directly, "
            "sensually, and emotionally grounded, with clear consent and character psychology. Do not be coy, but do not include minors or unclear ages."
        ),
    ),
    ShakedownCase(
        key="action",
        label="Action / Tactical",
        expected_genre_ids=("action_adventure",),
        required_terms=("warehouse", "bolt cutters", "Tess", "Malik"),
        banned_terms=("teleported", "suddenly appeared behind"),
        wants_visual_intro=True,
        wants_spatial=True,
        wants_object=True,
        wants_relationship=True,
        director_note=(
            "Modern action/tactical scene. Write a clear, grounded scene in an abandoned warehouse at night. "
            "Introduce Tess Ward, an adult ex-firefighter with a shaved undercut, broad shoulders, and a torn navy jacket, and Malik Daro, "
            "an adult locksmith with tired eyes, thin hands, and a canvas tool roll. Tess crouches behind a forklift holding bolt cutters. "
            "Malik is near the loading-bay door with the stolen ledger tucked under his coat. They promised each other no one would get killed tonight, "
            "but an armed guard is searching the office above them. Keep blocking exact, action consequences physical, and the promise emotionally active."
        ),
    ),
]


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 30.0) -> Any:
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
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not reach StoryDriver backend at {BASE_URL}: {error}") from error


def stream_generation(session_id: str, director_note: str) -> tuple[dict[str, Any], dict[str, Any]]:
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps({"director_note": director_note, "mode": "continue"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    status_events: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    delta_count = 0
    streamed_chars = 0
    first_delta_at: float | None = None
    started_at = time.monotonic()
    final_scene: dict[str, Any] | None = None
    with urllib.request.urlopen(request, timeout=900) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type")
            if event_type == "status":
                status_events.append(event)
                print(f"[status] {event.get('stage')}: {event.get('message')}")
            elif event_type == "warning":
                warnings.append(event)
                print(f"[warning] {event.get('message')}")
            elif event_type == "delta":
                if first_delta_at is None:
                    first_delta_at = time.monotonic()
                text = event.get("text") or ""
                delta_count += 1
                streamed_chars += len(text)
                if delta_count % 25 == 0:
                    print(f"[stream] {streamed_chars} chars")
            elif event_type == "scene":
                final_scene = event.get("scene")
            elif event_type == "error":
                raise RuntimeError(event.get("detail") or "Generation failed.")
    if not final_scene:
        raise RuntimeError("No final scene event returned.")
    return final_scene, {
        "status_events": status_events,
        "warnings": warnings,
        "delta_count": delta_count,
        "streamed_chars": streamed_chars,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "first_delta_seconds": round(first_delta_at - started_at, 3) if first_delta_at else None,
    }


def poll(label: str, fetcher, predicate, *, timeout: float = 90.0, interval: float = 3.0) -> Any:
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        try:
            latest = fetcher()
        except Exception as error:
            latest = {"error": str(error)}
        if predicate(latest):
            print(f"[OK] {label}")
            return latest
        time.sleep(interval)
    print(f"[WARN] {label} not ready before timeout")
    return latest


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def count_case_insensitive_terms(text: str, terms: tuple[str, ...]) -> dict[str, int]:
    lower = text.lower()
    return {term: lower.count(term.lower()) for term in terms}


def contains_any(text: str, terms: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for term in terms:
        escaped = re.escape(term.lower())
        if re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text.lower()):
            hits.append(term)
    return hits


def extract_generation_metrics(scene: dict[str, Any], stream: dict[str, Any]) -> dict[str, Any]:
    stats = scene.get("generation_stats") or {}
    native = stats.get("native_chat_stats") or {}
    latency = stats.get("latency") or {}
    return {
        "model": stats.get("model") or stats.get("active_model") or "",
        "backend": stats.get("inference_backend") or stats.get("backend") or "",
        "visible_tps": stats.get("visible_tokens_per_second") or stats.get("tokens_per_second") or native.get("tokens_per_second"),
        "raw_tps": stats.get("lmstudio_raw_tokens_per_second") or native.get("tokens_per_second"),
        "first_visible_token_seconds": stats.get("first_visible_token_seconds")
        or stats.get("lmstudio_time_to_first_token_seconds")
        or latency.get("first_visible_token_seconds")
        or stream.get("first_delta_seconds"),
        "hidden_reasoning_chars": stats.get("hidden_reasoning_chars") or stats.get("reasoning_chars") or 0,
        "prompt_estimated_tokens": (stats.get("prompt_diagnostics") or {}).get("prompt_estimated_tokens"),
        "image_cleanup_skip": ((stats.get("comfyui_pre_write_free") or {}).get("skip_reason") == "image_generation_paused")
        or ((stats.get("writing_speed_auto_cleanup") or {}).get("skip_reason") == "image_generation_paused"),
    }


def brief_excerpt(text: str, limit: int = 360) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rsplit(" ", 1)[0] + "..."


def preview_checks(preview: dict[str, Any], case: ShakedownCase) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    system_prompt = preview.get("system_prompt") or ""
    task_notes = preview.get("task_notes") or ""
    user_prompt = preview.get("user_prompt") or ""
    helper = preview.get("genre_time_helper") or {}
    genre_ids = {item.get("id") for item in helper.get("matches", []) if isinstance(item, dict)}
    if "StoryDriver's fiction engine" not in system_prompt:
        problems.append("Active system prompt does not look like StoryDriver Prose v2.")
    if "Use director notes as guidance" not in task_notes:
        problems.append("Prose task notes do not look like StoryDriver Prose v2.")
    if preview.get("hidden_style_instructions"):
        problems.append("Prompt preview reports hidden style instructions.")
    if preview.get("writing_path") != "deliberate_pipeline":
        problems.append("Prompt preview did not resolve the Deliberate Pipeline writing path.")
    if not preview.get("scene_plan"):
        problems.append("Prompt preview did not include the mandatory structured scene plan.")
    if "ComfyUI" in user_prompt or "image workflow" in user_prompt.lower():
        problems.append("Prose prompt contains image/ComfyUI wording.")
    if case.expected_genre_ids and not (set(case.expected_genre_ids) & genre_ids):
        problems.append(f"Genre/time helper missed expected id(s): {', '.join(case.expected_genre_ids)}.")
    notes.append(f"Prompt length estimate: {(preview.get('prompt_diagnostics') or {}).get('prompt_estimated_tokens')}")
    notes.append(f"Genre helper ids: {', '.join(sorted(str(item) for item in genre_ids if item)) or 'none'}")
    notes.append(f"Writing length: {(preview.get('writing_length') or {}).get('label')}")
    return problems, notes


def evaluate_scene(case: ShakedownCase, text: str) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    lower = text.lower()
    required_counts = count_case_insensitive_terms(text, case.required_terms)
    missing_required = [term for term, count in required_counts.items() if count <= 0]
    banned = contains_any(text, case.banned_terms)
    assistant_phrases = contains_any(
        text[-1200:],
        ("what happens next", "let me know", "i can continue", "as an ai", "would you like"),
    )
    if missing_required:
        problems.append(f"Missing expected story element(s): {', '.join(missing_required)}.")
    if banned:
        problems.append(f"Found genre-inappropriate / banned term(s): {', '.join(banned)}.")
    if assistant_phrases:
        problems.append(f"Assistant-style ending/framing detected: {', '.join(assistant_phrases)}.")
    if word_count(text) < 450:
        problems.append("Generated scene was too short for a real quality read.")

    if case.wants_visual_intro:
        visual_hits = sum(
            1
            for term in (
                "hair",
                "eyes",
                "face",
                "coat",
                "jacket",
                "dress",
                "cloak",
                "sleeve",
                "scar",
                "shoulder",
                "hands",
                "voice",
            )
            if term in lower
        )
        if visual_hits < 4:
            problems.append("New-character visual description was thin.")
        notes.append(f"Visual-detail hits: {visual_hits}")

    if case.wants_spatial:
        spatial_hits = sum(
            1
            for term in ("near", "beside", "behind", "door", "table", "window", "hearth", "forklift", "lock", "above", "crouched", "stood", "kneel")
            if term in lower
        )
        if spatial_hits < 3:
            problems.append("Spatial blocking looked too thin for the requested scene.")
        notes.append(f"Spatial/blocking hits: {spatial_hits}")

    if case.wants_object:
        object_hits = sum(1 for term in ("holding", "held", "tucked", "keycard", "map", "seal", "gasket", "ring", "manual release", "ledger", "bolt cutters") if term in lower)
        if object_hits < 2 and "map" not in lower:
            problems.append("Object ownership / carried-object continuity looked weak.")
        notes.append(f"Object-continuity hits: {object_hits}")

    if case.wants_relationship:
        relationship_hits = sum(
            1
            for term in (
                "sister",
                "trust",
                "old trust",
                "promise",
                "pact",
                "no blood",
                "wanted",
                "careful",
                "first kiss",
                "boundar",
                "long-denied",
                "missed opportunity",
                "mother",
                "disagree",
                "consent",
                "history",
                "failed",
            )
            if term in lower
        )
        if relationship_hits < 2:
            problems.append("Relationship/history stakes were too thin.")
        notes.append(f"Relationship/history hits: {relationship_hits}")

    if case.wants_direct_intimacy:
        if not any(term in lower for term in ("consent", "yes", "asked", "plea", "boundar", "permission")):
            problems.append("Adult intimacy scene did not visibly ground consent.")
        if any(term in lower for term in ("minor", "underage")):
            problems.append("Adult-only guardrail violation term appeared.")

    required_words = {
        part.lower()
        for term in case.required_terms
        for part in re.findall(r"\b[a-z]{5,}\b", term.lower())
    }
    repeated_note_words = [
        word
        for word in re.findall(r"\b[a-z]{5,}\b", case.director_note.lower())
        if word not in {"adult", "scene", "write", "story", "their", "there", "through", "grounded", "introduce"}
        and word not in required_words
    ]
    note_word_counts = {word: lower.count(word) for word in set(repeated_note_words)}
    overused = [word for word, count in note_word_counts.items() if count >= 18]
    if overused:
        problems.append(f"Possible director-note overfixation / repetition: {', '.join(sorted(overused)[:5])}.")

    return problems, notes


def state_summary(overview: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(overview, dict):
        return {}
    latest = overview.get("latest_run") or {}
    return {
        "status": latest.get("status"),
        "error": latest.get("error"),
        "prompt_item_count": overview.get("prompt_item_count"),
        "characters": len(overview.get("active_characters") or []),
        "live_state": len(overview.get("character_live_state") or []),
        "objects": len(overview.get("objects") or []),
        "relationships": len(overview.get("relationships") or []),
        "emotional_memories": len(overview.get("emotional_memories") or []),
        "conflicts": len(overview.get("conflicts") or []),
        "prompt_context_excerpt": brief_excerpt(overview.get("prompt_context") or "", 420),
    }


def run_case(case: ShakedownCase) -> dict[str, Any]:
    print(f"\n=== {case.label} ===")
    session = request_json("/sessions", method="POST", payload={"title": "Untitled Story"}, timeout=15)
    session_id = session["id"]
    print(f"Created test story: {session_id}")
    preview = request_json(
        f"/sessions/{session_id}/prose-prompt-preview",
        method="POST",
        payload={"director_note": case.director_note, "mode": "continue"},
        timeout=30,
    )
    preview_problems, preview_notes = preview_checks(preview, case)

    scene, stream = stream_generation(session_id, case.director_note)
    text = scene.get("generated_text") or ""
    scene_problems, scene_notes = evaluate_scene(case, text)
    metrics = extract_generation_metrics(scene, stream)

    title = poll(
        f"{case.label} title",
        lambda: request_json(f"/sessions/{session_id}", timeout=10),
        lambda payload: isinstance(payload, dict)
        and str(payload.get("title") or "").strip().lower() not in {"", "untitled story", "new story"}
        and payload.get("auto_title_status") in {"generated", "user_set"},
        timeout=90,
        interval=3,
    )
    story_state = poll(
        f"{case.label} state extraction",
        lambda: request_json(f"/sessions/{session_id}/story-state", timeout=20),
        lambda payload: isinstance(payload, dict)
        and (payload.get("latest_run") or {}).get("status") in {"completed", "skipped", "failed", "stale"},
        timeout=180,
        interval=5,
    )
    state = state_summary(story_state if isinstance(story_state, dict) else None)

    state_problems: list[str] = []
    if state.get("status") == "failed":
        state_problems.append(f"State extraction failed: {state.get('error')}")
    if case.wants_object and int(state.get("objects") or 0) <= 0:
        state_problems.append("Story State did not capture object ownership/state.")
    if case.wants_relationship and int(state.get("relationships") or 0) <= 0 and int(state.get("emotional_memories") or 0) <= 0:
        state_problems.append("Story State did not capture relationship/history stakes.")
    if case.wants_spatial and int(state.get("live_state") or 0) <= 0 and int(state.get("prompt_item_count") or 0) <= 0:
        state_problems.append("Story State did not capture spatial/live-state continuity.")

    return {
        "case": case,
        "session_id": session_id,
        "scene_id": scene.get("id"),
        "version_id": scene.get("active_version_id"),
        "title": (title or {}).get("title") if isinstance(title, dict) else "",
        "auto_title_status": (title or {}).get("auto_title_status") if isinstance(title, dict) else "",
        "word_count": word_count(text),
        "metrics": metrics,
        "stream": stream,
        "preview_notes": preview_notes,
        "preview_problems": preview_problems,
        "scene_notes": scene_notes,
        "scene_problems": scene_problems,
        "state": state,
        "state_problems": state_problems,
        "excerpt": (
            "[Adult-only intimacy output saved in the test story; excerpt omitted from this report.]"
            if case.wants_direct_intimacy
            else brief_excerpt(text, 520)
        ),
    }


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_No rows._"]
    lines = ["|" + "|".join(columns) + "|", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        values = []
        for column in columns:
            value = row.get(column)
            if isinstance(value, float):
                value = f"{value:.2f}"
            text = str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
            values.append(text)
        lines.append("|" + "|".join(values) + "|")
    return lines


def write_report(results: list[dict[str, Any]], environment: dict[str, Any], fixes_made: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    all_problems: list[str] = []
    for result in results:
        case: ShakedownCase = result["case"]
        problems = result["preview_problems"] + result["scene_problems"] + result["state_problems"]
        all_problems.extend(f"{case.label}: {problem}" for problem in problems)
        metrics = result["metrics"]
        rows.append(
            {
                "case": case.label,
                "title": result.get("title") or "Untitled Story",
                "words": result["word_count"],
                "visible t/s": metrics.get("visible_tps") or "",
                "first token": metrics.get("first_visible_token_seconds") or "",
                "state": result["state"].get("status") or "",
                "issues": len(problems),
            }
        )

    lines: list[str] = [
        "# Real Story Quality Shakedown Report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Environment",
        f"- Backend: `{BASE_URL}`",
        f"- Health: `{environment.get('health')}`",
        f"- Selected/global model: `{environment.get('model') or 'auto from LM Studio'}`",
        f"- Prose prompt active: `{'yes' if environment.get('prose_v2_active') else 'no'}`",
        f"- Prose task notes active: `{'yes' if environment.get('prose_notes_v2_active') else 'no'}`",
        f"- Temporary Prose v2 test settings applied: `{'yes' if environment.get('temporary_prose_v2_applied') else 'no'}`",
        f"- Original model settings restored: `{'yes' if environment.get('settings_restored') else 'not needed' if not environment.get('temporary_prose_v2_applied') else 'no'}`",
        f"- Prose prompt mode: `{environment.get('prose_prompt_mode')}`",
        f"- Writing path: `{environment.get('writing_path')}`",
        f"- Structured planning mandatory: `{environment.get('app_planning_enabled')}`",
        f"- Image generation mode: `{environment.get('image_generation_mode')}`",
        f"- Kokoro online: `{environment.get('kokoro_online')}`",
        "",
        "## Summary",
        *markdown_table(rows, ["case", "title", "words", "visible t/s", "first token", "state", "issues"]),
        "",
        "## Concrete Problems Found",
    ]
    if all_problems:
        lines.extend(f"- {problem}" for problem in all_problems)
    else:
        lines.append("- No remaining concrete production prompt/state bug is failing the final shakedown.")
        lines.append("- Earlier concrete issues found during this pass are listed under Fixes Made below.")
    lines.extend(["", "## Fixes Made"])
    if fixes_made:
        lines.extend(f"- {fix}" for fix in fixes_made)
    else:
        lines.append("- None. This pass did not patch production behavior because the issues found were either absent or runtime/model-output variance rather than a concrete code/prompt defect.")

    for result in results:
        case: ShakedownCase = result["case"]
        metrics = result["metrics"]
        lines.extend(
            [
                "",
                f"## {case.label}",
                f"- Session ID: `{result['session_id']}`",
                f"- Scene ID: `{result.get('scene_id')}`",
                f"- Version ID: `{result.get('version_id')}`",
                f"- Director note used: {case.director_note}",
                f"- Generated title: `{result.get('title') or 'Untitled Story'}` (`{result.get('auto_title_status') or 'unknown'}`)",
                f"- Word count: {result['word_count']}",
                f"- Model: `{metrics.get('model') or 'unknown'}`",
                f"- Backend: `{metrics.get('backend') or 'unknown'}`",
                f"- Visible t/s: `{metrics.get('visible_tps')}`",
                f"- Raw t/s: `{metrics.get('raw_tps')}`",
                f"- First visible token: `{metrics.get('first_visible_token_seconds')}` seconds",
                f"- Hidden reasoning chars: `{metrics.get('hidden_reasoning_chars')}`",
                f"- Prompt estimate: `{metrics.get('prompt_estimated_tokens')}` tokens",
                f"- Image/ComfyUI prompt cleanup skipped because images paused: `{metrics.get('image_cleanup_skip')}`",
                f"- Stream: `{result['stream'].get('delta_count')}` deltas / `{result['stream'].get('streamed_chars')}` chars / `{result['stream'].get('elapsed_seconds')}` seconds",
                "",
                "### Prompt Preview Notes",
                *[f"- {note}" for note in result["preview_notes"]],
                *[f"- PROBLEM: {problem}" for problem in result["preview_problems"]],
                "",
                "### Quality Notes",
                *[f"- {note}" for note in result["scene_notes"]],
                *[f"- PROBLEM: {problem}" for problem in result["scene_problems"]],
                "",
                "### State Extraction Result",
                f"- Status: `{result['state'].get('status')}`",
                f"- Error: `{result['state'].get('error') or 'none'}`",
                f"- Prompt item count: `{result['state'].get('prompt_item_count')}`",
                f"- Characters: `{result['state'].get('characters')}`",
                f"- Character live state rows: `{result['state'].get('live_state')}`",
                f"- Object rows: `{result['state'].get('objects')}`",
                f"- Relationship rows: `{result['state'].get('relationships')}`",
                f"- Emotional memory rows: `{result['state'].get('emotional_memories')}`",
                f"- Conflicts: `{result['state'].get('conflicts')}`",
                *[f"- PROBLEM: {problem}" for problem in result["state_problems"]],
                f"- Prompt context excerpt: {result['state'].get('prompt_context_excerpt') or '_none_'}",
                "",
                "### Generated Excerpt",
                result["excerpt"] or "_No excerpt._",
            ]
        )
    REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def detect_environment() -> dict[str, Any]:
    health = request_json("/health", timeout=8)
    settings = request_json("/settings/model", timeout=10)
    routing = request_json("/settings/task-model-profiles", timeout=10)
    image = request_json("/settings/image", timeout=10)
    diagnostics = {}
    try:
        diagnostics = request_json("/diagnostics", timeout=20)
    except Exception as error:
        diagnostics = {"error": str(error)}
    prose_profile = (routing.get("resolved") or {}).get("prose_generation") if isinstance(routing, dict) else {}
    system_prompt = settings.get("system_prompt") or ""
    notes = (prose_profile or {}).get("notes") or ""
    return {
        "health": health,
        "model_settings": settings,
        "task_profiles": routing,
        "model": settings.get("model") or (prose_profile or {}).get("model") or "",
        "prose_v2_active": "StoryDriver's fiction engine" in system_prompt and "spatial continuity" in system_prompt,
        "prose_notes_v2_active": "Use director notes as guidance" in notes and "looping on one topic" in notes,
        "prose_prompt_mode": settings.get("prose_prompt_mode"),
        "writing_path": settings.get("writing_path"),
        "app_planning_enabled": settings.get("app_planning_enabled"),
        "image_generation_mode": (image or {}).get("image_generation_mode"),
        "kokoro_online": bool(
            ((diagnostics or {}).get("kokoro") or {}).get("reachable")
            or ((diagnostics or {}).get("kokoro") or {}).get("online")
            or ((diagnostics or {}).get("kokoro") or {}).get("ok")
        ),
    }


def apply_temporary_prose_v2_settings(environment: dict[str, Any]) -> dict[str, Any]:
    original_settings = dict(environment.get("model_settings") or {})
    original_profiles = dict(environment.get("task_profiles") or {})
    temp_settings = dict(original_settings)
    temp_settings.update(
        {
            "active_preset_id": None,
            "system_prompt": STORYDRIVER_PROSE_V2_SYSTEM_PROMPT,
            "writing_length_mode": "scene",
            "prose_prompt_mode": "standard",
            "writing_path": "direct_writer",
            "app_planning_enabled": False,
            "chapter_extension_enabled": True,
            "adherence_check_mode": "warn",
            "max_tokens": min(max(int(temp_settings.get("max_tokens") or 2800), 2800), 3600),
        }
    )
    print("[INFO] Applying temporary Prose v2 system prompt for shakedown only.")
    request_json("/settings/model", method="PUT", payload=temp_settings, timeout=20)

    prose_profile = ((original_profiles.get("profiles") or {}).get("prose_generation") or {}).copy()
    original_prose_notes = prose_profile.get("notes") or ""
    if "Use director notes as guidance" not in original_prose_notes:
        prose_profile["notes"] = DEFAULT_TASK_NOTES["prose_generation"]
        print("[INFO] Applying temporary Prose v2 prose task notes for shakedown only.")
        request_json("/settings/task-model-profiles/prose_generation", method="PUT", payload=prose_profile, timeout=20)

    updated = detect_environment()
    updated["temporary_prose_v2_applied"] = True
    updated["original_model_settings"] = original_settings
    updated["original_prose_profile"] = (original_profiles.get("profiles") or {}).get("prose_generation")
    updated["temporary_prose_notes_applied"] = "Use director notes as guidance" not in original_prose_notes
    updated["settings_restored"] = False
    return updated


def restore_original_settings(environment: dict[str, Any]) -> bool:
    if not environment.get("temporary_prose_v2_applied"):
        environment["settings_restored"] = True
        return True
    restored = True
    original_settings = environment.get("original_model_settings")
    original_prose_profile = environment.get("original_prose_profile")
    try:
        if isinstance(original_settings, dict) and original_settings:
            request_json("/settings/model", method="PUT", payload=original_settings, timeout=20)
    except Exception as error:
        restored = False
        print(f"[WARN] Could not restore original model settings: {error}")
    try:
        if environment.get("temporary_prose_notes_applied") and isinstance(original_prose_profile, dict) and original_prose_profile:
            request_json("/settings/task-model-profiles/prose_generation", method="PUT", payload=original_prose_profile, timeout=20)
    except Exception as error:
        restored = False
        print(f"[WARN] Could not restore original prose task profile: {error}")
    environment["settings_restored"] = restored
    return restored


def main() -> int:
    print("StoryDriver real story-quality shakedown")
    environment = detect_environment()
    if not environment.get("prose_v2_active") or not environment.get("prose_notes_v2_active"):
        environment = apply_temporary_prose_v2_settings(environment)
    else:
        environment["temporary_prose_v2_applied"] = False
        environment["settings_restored"] = True
    if environment.get("image_generation_mode") != "paused":
        print(f"[WARN] Image mode is {environment.get('image_generation_mode')}; expected paused.")

    results: list[dict[str, Any]] = []
    total_problems = 0
    try:
        for case in TEST_CASES:
            results.append(run_case(case))
        total_problems = sum(len(item["preview_problems"]) + len(item["scene_problems"]) + len(item["state_problems"]) for item in results)
        return_code = 0 if total_problems == 0 else 1
    finally:
        restore_original_settings(environment)
        write_report(results, environment, FIXES_MADE)
        print(f"Report written: {REPORT}")
        print(f"Concrete issue count: {total_problems}")
    return return_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[ABORTED] Interrupted.")
        sys.exit(130)
    except Exception as error:
        print(f"[FAIL] {error}")
        sys.exit(1)
