from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "backend" / "data" / "logs" / "WRITING_QUALITY_AND_ROUTING_REPORT.md"
BASE_URL = "http://localhost:8001"
TEST_SESSION_IDS: list[str] = []

DIRECTOR_NOTE = (
    "Create a story with 3 women, a medieval adventure story about three sisters whose parents died when they were young. "
    "They lived on the streets and became stealthy outcasts. No magic exists in this world. They now live on a farm outside "
    "a major town and hear that a bandit camp captured a little girl. The first chapter introduces each sister properly and "
    "shows them talking through whether to rescue the girl. They are smart and tactical, but they have never truly killed or "
    "fought before. Make this a long first chapter, 7-12 minutes of reading time."
)


def default_prompt_contract_checks() -> dict[str, bool]:
    settings_source = (ROOT / "backend" / "app" / "settings" / "store.py").read_text(encoding="utf-8").lower()
    prompt_builder_source = (ROOT / "backend" / "app" / "generation" / "prompt_builder.py").read_text(encoding="utf-8").lower()

    return {
        "prose_v3_system_prompt_installed": "storydriver_prose_v3_system_prompt" in settings_source
        and "vivid narrated fiction scenes" in settings_source
        and "spatial continuity" in settings_source,
        "character_agency_guardrails": "interiority" in settings_source and "preserve agency" in settings_source,
        "adult_only_guardrails": "clearly adult consenting fictional characters" in settings_source
        and "never sexualize minors" in settings_source,
        "director_note_overfixation_guard": "treating the note as direction" in settings_source
        and "not wording to repeat" in settings_source,
        "modern_diction_helper": "modern_present_day" in prompt_builder_source
        and "natural contemporary prose" in prompt_builder_source,
        "fantasy_diction_helper": "medieval_fantasy" in prompt_builder_source
        and "fake old-timey words" in prompt_builder_source,
        "scifi_diction_helper": "science_fiction" in prompt_builder_source
        and "generic technobabble" in prompt_builder_source,
    }


def request_json(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 30.0):
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


def generate_stream(session_id: str) -> tuple[dict, dict]:
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps({"director_note": DIRECTOR_NOTE, "mode": "continue"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    status_events = []
    delta_count = 0
    streamed_chars = 0
    final_scene = None
    with urllib.request.urlopen(request, timeout=900) as response:
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
                if delta_count % 20 == 0:
                    print(f"[stream] {streamed_chars} chars")
            elif event_type == "scene":
                final_scene = event.get("scene")
            elif event_type == "error":
                raise RuntimeError(event.get("detail") or "Generation failed.")
    if not final_scene:
        raise RuntimeError("No final scene event returned.")
    return final_scene, {
        "status_events": status_events,
        "delta_count": delta_count,
        "streamed_chars": streamed_chars,
    }


def word_count(text: str) -> int:
    return len([word for word in text.replace("\n", " ").split(" ") if word.strip()])


def is_generic_title(title: str | None) -> bool:
    normalized = (title or "").strip().lower()
    return normalized in {"", "new story", "untitled story", "untitled", "blank story"} or bool(
        re.match(r"^(?:new story|untitled)(?:\s+\d+)?$", normalized)
    )


def magic_violations(text: str) -> list[str]:
    violations: list[str] = []
    for term in ("magic", "spell", "wizard", "sorcery", "enchanted"):
        for match in re.finditer(rf"\b{re.escape(term)}\w*\b", text, flags=re.IGNORECASE):
            before = text[max(0, match.start() - 80) : match.start()].lower()
            phrase = text[max(0, match.start() - 20) : match.end() + 40].lower()
            if re.search(r"\b(no|not|without|lacked|lack|lacks|possessed no|possess no|had no|has no)\b", before):
                continue
            if "no magic" in phrase or "possessed no magic" in phrase:
                continue
            violations.append(match.group(0).lower())
    return sorted(set(violations))


def top_repeated_character_names(text: str) -> list[str]:
    exclusions = {
        "The",
        "A",
        "An",
        "And",
        "But",
        "Or",
        "She",
        "He",
        "They",
        "Her",
        "His",
        "Their",
        "This",
        "That",
        "When",
        "Where",
        "What",
        "There",
        "Not",
        "One",
        "Then",
        "You",
        "From",
        "Every",
        "Only",
        "Those",
        "Maybe",
        "Check",
        "Think",
        "Knowing",
        "How",
        "Like",
        "Short",
        "While",
        "Twelve",
        "Thieves",
        "Old",
        "Man",
        "Little",
        "Miller",
        "Blackwood",
        "Ridge",
        "Oakhaven",
    }
    counts: dict[str, int] = {}
    for match in re.finditer(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", text):
        first = match.group(0).split()[0]
        if first in exclusions:
            continue
        counts[first] = counts.get(first, 0) + 1
    return [name for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if count >= 2][:3]


def story_name_matches(expected: str, actual: str) -> bool:
    expected_clean = (expected or "").strip().casefold()
    actual_clean = (actual or "").strip().casefold()
    if not expected_clean or not actual_clean:
        return False
    return actual_clean == expected_clean or actual_clean.startswith(f"{expected_clean} ")


def poll(label: str, fetcher, predicate, *, timeout: float = 180.0, interval: float = 4.0):
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


def append_report(lines: list[str]) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
    section = "\n".join(lines).rstrip() + "\n"
    if existing.strip():
        REPORT.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")
    else:
        REPORT.write_text(section, encoding="utf-8")


def cleanup_test_sessions() -> None:
    for session_id in reversed(TEST_SESSION_IDS):
        try:
            result = request_json(f"/sessions/{session_id}?permanent=true", method="DELETE", timeout=30) or {}
            job_id = result.get("job_id") or result.get("id")
            if not job_id:
                continue
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                job = request_json(f"/sessions/delete-jobs/{job_id}", timeout=15) or {}
                if job.get("status") == "completed":
                    break
                if job.get("status") in {"failed", "cancelled"}:
                    print(f"[WARN] disposable story cleanup failed: {job}")
                    break
                time.sleep(0.5)
        except Exception as error:
            print(f"[WARN] disposable story cleanup failed for {session_id}: {error}")


def main() -> int:
    print("StoryDriver writing quality smoke test")
    prompt_contract_checks = default_prompt_contract_checks()
    for name, ok in prompt_contract_checks.items():
        print(f"[{'OK' if ok else 'WARN'}] prompt contract: {name}")

    health = request_json("/health", timeout=8)
    if not isinstance(health, dict) or not health.get("ok"):
        raise RuntimeError("StoryDriver backend health is not OK.")

    settings = request_json("/settings/model", timeout=10)
    routing = request_json("/settings/task-model-profiles", timeout=10)
    print(f"Global model: {settings.get('model') or 'auto'}")
    print(f"Writing length mode: {settings.get('writing_length_mode')}")
    print(f"Prose task model: {routing.get('resolved', {}).get('prose_generation', {}).get('model') or 'global'}")

    session = request_json("/sessions", method="POST", payload={"title": "New Story"}, timeout=10)
    session_id = session["id"]
    TEST_SESSION_IDS.append(session_id)
    print(f"Created test session: {session_id}")

    scene, stream = generate_stream(session_id)
    text = scene.get("generated_text") or ""
    words = word_count(text)
    lower_text = text.lower()
    magic_terms = magic_violations(text)
    has_assistant_ending = any(phrase in lower_text[-900:] for phrase in ("what happens next", "let me know", "i can continue"))

    title = poll(
        "automatic story title",
        lambda: request_json(f"/sessions/{session_id}", timeout=10),
        lambda payload: isinstance(payload, dict)
        and not is_generic_title(payload.get("title"))
        and payload.get("auto_title_status") in {"generated", "user_set"},
        timeout=90,
        interval=3,
    )
    characters = poll(
        "auto-created characters",
        lambda: request_json(f"/sessions/{session_id}/characters", timeout=10),
        lambda items: isinstance(items, list) and len(items) >= 3,
        timeout=210,
    )
    story_state = poll(
        "state extraction run",
        lambda: request_json(f"/sessions/{session_id}/story-state", timeout=15),
        lambda payload: isinstance(payload, dict)
        and (payload.get("latest_run") or {}).get("status") in {"completed", "skipped", "failed"},
        timeout=210,
    )
    auto_names = [
        item.get("character", {}).get("name")
        for item in characters or []
        if item.get("character", {}).get("auto_created")
    ]
    story_character_names = [
        item.get("character", {}).get("name")
        for item in characters or []
        if item.get("character", {}).get("name")
    ]
    expected_character_names = top_repeated_character_names(text)
    matched_auto_names = sorted(
        {
            actual
            for expected in expected_character_names
            for actual in auto_names
            if story_name_matches(expected, actual)
        }
    )
    matched_story_names = sorted(
        {
            actual
            for expected in expected_character_names
            for actual in story_character_names
            if story_name_matches(expected, actual)
        }
    )
    state_run = (story_state or {}).get("latest_run") or {}
    checks = {
        "long_enough": words >= 1800,
        "streamed": stream["delta_count"] > 3 and stream["streamed_chars"] > 500,
        "no_magic": not magic_terms,
        "no_assistant_ending": not has_assistant_ending,
        "title_generated": (title or {}).get("title", "").strip().lower() not in {"new story", "untitled story", ""},
        "characters_created": len(matched_story_names) >= min(3, len(expected_character_names)),
        "no_junk_characters": not (set(auto_names) & {"Before", "Being", "Bring", "Did"}),
        "state_not_empty_failure": state_run.get("error") != "LM Studio returned an empty scene.",
    }

    lines = [
        "# StoryDriver Writing Quality and Routing Report",
        "",
        "## Automated Chapter Prompt Test",
        f"- Session ID: `{session_id}`",
        f"- Generated title: `{(title or {}).get('title', '')}`",
        f"- Word count: {words}",
        f"- Stream chunks: {stream['delta_count']} chunks / {stream['streamed_chars']} chars",
        f"- Status stages: {', '.join(event.get('stage', '') for event in stream['status_events'])}",
        f"- Magic-term violations: {', '.join(magic_terms) or 'none'}",
        f"- Assistant-style ending: {'yes' if has_assistant_ending else 'no'}",
        f"- Auto-created characters: {', '.join(auto_names) or 'none yet'}",
        f"- Current story characters: {', '.join(story_character_names) or 'none yet'}",
        f"- Expected repeated character names: {', '.join(expected_character_names) or 'none detected'}",
        f"- Matched auto-created characters: {', '.join(matched_auto_names) or 'none'}",
        f"- Matched current-story characters: {', '.join(matched_story_names) or 'none'}",
        f"- State extraction status: {state_run.get('status') or 'unknown'}",
        f"- State extraction error: {state_run.get('error') or 'none'}",
        "",
        "## Checks",
        "### Prompt Contract",
        *[f"- {'PASS' if ok else 'WARN'} {name}" for name, ok in prompt_contract_checks.items()],
        "",
        "### Generated Passage",
        *[f"- {'PASS' if ok else 'WARN'} {name}" for name, ok in checks.items()],
    ]
    append_report(lines)
    for name, ok in checks.items():
        print(f"[{'OK' if ok else 'WARN'}] {name}")
    return 0 if all(prompt_contract_checks.values()) and all(checks.values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[FAIL] {error}")
        sys.exit(1)
    finally:
        cleanup_test_sessions()
