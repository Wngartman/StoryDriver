from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "backend" / "data"
DB_PATH = DATA_DIR / "app.db"
METRICS_PATH = DATA_DIR / "temp" / "complete_revamp_acceptance_metrics.json"
BASE_URL = os.environ.get("STORYDRIVER_BACKEND_URL", "http://localhost:8001").rstrip("/")


MODERN_OPENING = (
    "Opening chapter of a present-day Denver character drama. Cover only the next fifteen minutes inside a modest "
    "Capitol Hill apartment during an evening rainstorm. Introduce three adults naturally: Dana, a tenant who works "
    "night shifts; Jules, Dana's estranged older sibling; and Micah, the downstairs neighbor who has come up because "
    "water is leaking through his ceiling. Invent grounded full identities, appearances, voices, and distinct personalities "
    "where details are missing. Establish the living room, kitchenette, hall, front door, fire-escape window, and who can "
    "see or hear whom. Dana wants to keep a sealed envelope private. Jules wants an honest conversation. Micah wants the "
    "leak stopped before his instruments are damaged. Let all three initiate choices. They may argue and inspect the room, "
    "but nobody leaves the apartment, opens the envelope, solves the old family problem, or jumps ahead in time. End on a "
    "natural handoff within the same conversation, not a manufactured cliffhanger."
)

MODERN_CONTINUATIONS = [
    (
        "Continue for only two minutes in the same apartment. Dana takes a small brass utility key from a hook beside the "
        "kitchen and deliberately hands it to Micah so he can check the shutoff cabinet. Jules objects to trusting him. End "
        "with Micah still holding the brass key. The envelope remains sealed and nobody leaves."
    ),
    (
        "Continue for five minutes without changing location. Wind forces rain through the fire-escape window. Jules's gray "
        "coat and left sleeve become soaked; broken glass gives Jules a shallow cut across the right palm. Dana rinses it at "
        "the kitchenette sink and secures a clean gauze wrap. Micah keeps possession of the brass utility key while helping "
        "close the window. Make the relationships alter their decisions."
    ),
    (
        "Continue immediately. Micah uses the brass key he still holds to open the shutoff cabinet in the hall and discovers "
        "that a recent replacement valve was installed backward. Jules remains in the wet gray coat with the right palm "
        "bandaged. Keep blocking exact and let Dana and Jules disagree about whether the faulty repair is negligence. No time skip."
    ),
    (
        "Continue in the apartment for the next six minutes. Micah begins cautious and conflict-avoidant, but a second pipe "
        "surge threatens his instruments downstairs. Give him a credible turning point: he takes charge of the temporary "
        "shutoff and commits to confronting the landlord with Dana, while still listening to Jules's objection. Do not reset "
        "anyone's personality or move to the later confrontation."
    ),
    (
        "Continue the same evening for five minutes. Show that Micah's new resolve persists without turning him into a different "
        "person. He still has the brass key. Jules's coat is wet and the wrapped right palm affects what Jules can handle. Dana "
        "finally explains why the sealed envelope matters, but it remains unopened. Let shared history change the plan."
    ),
    (
        "Continue for ten minutes at most, still inside the apartment. Dana, Jules, and Micah each want a different next step: "
        "Dana wants to document the plumbing, Jules wants to call a former source connected to the envelope, and Micah wants to "
        "protect the instruments downstairs without abandoning the evidence. Let them negotiate a concrete plan. Do not execute "
        "the phone call, go downstairs, open the envelope, or jump to tomorrow."
    ),
]

MEDIEVAL_OPENING = (
    "First chapter of a grounded medieval no-magic adventure. Cover one evening hour inside the Lantern Wayhouse only. "
    "Introduce adult cooper Elian Voss, adult caravan guard Sabine Marr, and adult herbalist Odo Fen through clothing, tools, "
    "habits, fears, and the strained trust left by a failed winter crossing. They must study a toll ledger and a hand-drawn "
    "ford map, argue over routes, and agree on preparations for tomorrow's search for a missing cart. Establish hearth, trestle "
    "table, shuttered windows, stable door, benches, wet cloaks, knife, awl, herbs, lantern oil, and object ownership. The future "
    "mission, road departure, ambush, rescue, battle, and next morning must not occur. End after the plan and introductions deepen."
)

SCIFI_OPENING = (
    "Opening scene on the maintenance ring of the freight ship Halcyon Spur, covering twelve minutes before docking. Introduce "
    "adult chief engineer Rhea Tan, adult cargo auditor Sol Mercer, and adult deckhand Imani Crowe. Make their technical dialogue "
    "precise but human as they compare an air-scrubber fault, a falsified service log, and a numbered ceramic data wafer. Establish "
    "the lock vestibule, tool cage, scrubber access bay, ladder well, warning lights, ship vibration, suits, injuries, and sightlines. "
    "Rhea starts holding the wafer, then consciously gives it to Sol for safekeeping. No docking, sabotage reveal, arrest, or time skip."
)

SCIFI_CONTINUE = (
    "Continue for four minutes in the same maintenance ring. Sol must still hold the numbered ceramic data wafer and compare it "
    "against the falsified service log while Rhea opens the scrubber panel and Imani watches the ladder well. Keep ship details, "
    "positions, and professional tensions consistent. They may form a plan but must not dock or identify the saboteur."
)

ADULT_SCENE = (
    "Adult-only contemporary relationship scene between two consenting women in their mid-thirties, Elena Ward and Noor Aziz, "
    "after months of mutual attraction. They are alone in Elena's locked studio after a difficult gallery opening. Write direct, "
    "detailed intimacy with spoken consent, desire, sensation, chemistry, changing emotions, and the pressure created by their work "
    "in the same arts collective. Both initiate and can slow or redirect what happens. Their adult status is unambiguous. No assistant "
    "framing, moralizing, summary, or fade-to-black coyness. Keep the scene relationship-focused and within the studio that night."
)

LONG_STORY_OPENING = (
    "Opening scene of a present-day archival mystery, covering ten minutes inside the basement records room of an old civic "
    "building. Introduce adult archivist Lysa Calder and adult building superintendent Tomas Reed with distinct appearance, "
    "voice, personality, goals, and an uneasy professional history. Lysa holds a red field notebook; Tomas holds a heavy flashlight. "
    "Establish the stair door, worktable, shelving aisles, fuse cabinet, floor drain, and who can see or hear whom. They discover "
    "one shelf is damp and a 1987 maintenance ledger is missing. Do not leave the room, solve the mystery, or skip time."
)

LONG_STORY_CONTINUATIONS = [
    "Continue for four minutes in the same records room. Lysa marks the damp shelf in her red notebook while Tomas checks the fuse cabinet with his flashlight. They disagree about shutting power off. No time skip or departure.",
    "Continue immediately. Tomas finds a recent screw on the floor beneath the fuse cabinet and hands it to Lysa, who places it in a paper evidence fold inside the red notebook. Keep object holders and room zones exact.",
    "Continue for five minutes. A pump starts behind the west wall. Lysa still holds the notebook containing the evidence fold and screw; Tomas still has the flashlight. Their prior dispute over a condemned annex changes who takes the risk of checking the drain.",
    "Continue in the same room. Tomas kneels at the floor drain and gets rust water on his right trouser knee. Lysa moves to the worktable but keeps the notebook. They infer a practical cause without identifying who removed the ledger.",
    "Continue for six minutes at most. A security radio crackles beyond the stair door. Neither person leaves. Let Lysa want to answer while Tomas wants silence, and make their different goals change the plan.",
    "Continue immediately. Lysa writes the agreed timeline in the red notebook; the evidence fold and screw remain inside it. Tomas uses the flashlight to inspect the underside of the worktable. Preserve the wet shelf, pump sound, and stained trouser knee.",
    "Continue for five minutes. They find a penciled shelf code beneath the worktable and compare it with Lysa's notes. Deepen their trust cautiously without erasing their conflict. Do not recover the missing ledger or leave the basement.",
    "Continue for five minutes in the records room. Lysa and Tomas commit to a concrete next step for after this scene while preserving every object, injury/clothing change, and unresolved thread. End before they open the stair door or advance to the next location.",
]


@dataclass
class GenerationResult:
    scene: dict[str, Any]
    text: str
    metrics: dict[str, Any]


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 60,
) -> Any:
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
        raise RuntimeError(f"HTTP {error.code}: {detail[:800]}") from error


def request_status(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30,
) -> tuple[int, str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method=method,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def create_story(title: str) -> dict[str, Any]:
    return request_json("/sessions", method="POST", payload={"title": title})


def stream_generation(
    session_id: str,
    director_note: str,
    *,
    mode: str = "continue",
    target_scene_id: str | None = None,
) -> GenerationResult:
    payload = {"director_note": director_note, "mode": mode, "target_scene_id": target_scene_id}
    request = urllib.request.Request(
        f"{BASE_URL}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    started = time.monotonic()
    first_delta: float | None = None
    statuses: list[dict[str, Any]] = []
    streamed_chars = 0
    final_scene: dict[str, Any] | None = None
    with urllib.request.urlopen(request, timeout=900) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            event_type = event.get("type")
            if event_type == "status":
                statuses.append({
                    "stage": event.get("stage"),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                })
                print(f"  [{mode}] {event.get('stage')}: {event.get('message')}", flush=True)
            elif event_type == "delta":
                if first_delta is None:
                    first_delta = time.monotonic()
                streamed_chars += len(event.get("text") or "")
            elif event_type == "scene":
                final_scene = event.get("scene") or event
            elif event_type == "error":
                raise RuntimeError(event.get("detail") or "Generation stream failed")
    if not final_scene:
        raise RuntimeError("Generation completed without a saved scene")
    text = str(final_scene.get("generated_text") or "")
    stats = final_scene.get("generation_stats") or {}
    return GenerationResult(
        scene=final_scene,
        text=text,
        metrics={
            "mode": mode,
            "scene_id": final_scene.get("id"),
            "version_count": final_scene.get("version_count"),
            "word_count": len(re.findall(r"\b[\w'-]+\b", text)),
            "output_chars": len(text),
            "first_visible_prose_seconds": round(first_delta - started, 3) if first_delta else None,
            "total_seconds": round(time.monotonic() - started, 3),
            "streamed_chars": streamed_chars,
            "status_timeline": statuses,
            "model": stats.get("model") or stats.get("active_model"),
            "visible_tps": stats.get("visible_prose_tokens_per_second") or stats.get("tokens_per_second"),
            "raw_tps": stats.get("lmstudio_raw_tokens_per_second") or stats.get("raw_stream_tokens_per_second"),
            "planner_seconds": stats.get("planner_seconds") or stats.get("planning_seconds"),
            "prose_seconds": stats.get("prose_seconds"),
            "review_seconds": stats.get("review_seconds"),
            "repair_applied": bool(stats.get("repair_applied") or stats.get("repair_used")),
            "prompt_chars": stats.get("prompt_chars"),
            "prompt_tokens": stats.get("prompt_tokens"),
        },
    )


def text_checks(text: str, *, required: list[str], forbidden: list[str]) -> dict[str, Any]:
    lower = text.lower()
    return {
        "required": {term: term.lower() in lower for term in required},
        "forbidden": {term: term.lower() in lower for term in forbidden},
        "assistant_framing": any(term in lower for term in ["as an ai", "i can't help", "i cannot help", "here is the scene"]),
        "markdown_heading": bool(re.search(r"(?m)^#{1,6}\s", text)),
    }


def flatten_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value) + sum(flatten_count(item) for item in value)
    if isinstance(value, dict):
        return sum(flatten_count(item) for item in value.values())
    return 0


def wait_for_background_state(session_id: str, timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            latest = request_json(f"/sessions/{session_id}/story-state", timeout=30) or {}
            status = str(latest.get("status") or latest.get("last_extraction_status") or "").lower()
            if status not in {"queued", "running", "pending"} and flatten_count(latest) > 0:
                return latest
        except Exception:
            pass
        time.sleep(3)
    return latest


def wait_for_summary(session_id: str, timeout: float = 240) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        summary = request_json(f"/sessions/{session_id}/summary", timeout=30)
        if summary and summary.get("summary_text"):
            return summary
        time.sleep(3)
    return None


def delete_story(session_id: str) -> dict[str, Any]:
    response = request_json(f"/sessions/{session_id}?permanent=true", method="DELETE", timeout=60) or {}
    job_id = response.get("job_id") or response.get("id")
    if not job_id:
        return response
    deadline = time.monotonic() + 180
    latest = response
    while time.monotonic() < deadline:
        latest = request_json(f"/sessions/delete-jobs/{job_id}", timeout=30) or latest
        if latest.get("status") in {"completed", "failed"}:
            return latest
        time.sleep(2)
    return latest


def database_counts() -> dict[str, int]:
    tables = [
        "sessions", "scenes", "scene_versions", "characters", "session_characters",
        "story_memories", "story_state_runs", "generation_runs", "narration_jobs", "narration_chunks",
    ]
    result: dict[str, int] = {}
    with sqlite3.connect(DB_PATH) as connection:
        known = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            result[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in known else 0
        result["foreign_key_violations"] = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    return result


def cleanup_orphan_auto_characters() -> int:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute(
            """
            DELETE FROM characters
            WHERE auto_created = 1
              AND NOT EXISTS (
                  SELECT 1 FROM session_characters sc WHERE sc.character_id = characters.id
              )
            """
        )
        connection.commit()
        return int(cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0)


def cleanup_orphan_character_dependents() -> dict[str, int]:
    result: dict[str, int] = {}
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for table in ("character_visual_profiles",):
            cursor = connection.execute(
                f"""
                DELETE FROM {table}
                WHERE NOT EXISTS (
                    SELECT 1 FROM characters WHERE characters.id = {table}.character_id
                )
                """
            )
            result[table] = int(cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0)
        connection.commit()
    return result


def generation_prompt_sizes(session_id: str) -> list[int]:
    with sqlite3.connect(DB_PATH) as connection:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT prompt_chars FROM generation_runs WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
            if int(row[0] or 0) > 0
        ]


def deletion_fixture() -> dict[str, Any]:
    session = create_story("REVAMP GAUNTLET - Deletion Fixture")
    session_id = session["id"]
    auto_id = str(uuid4())
    manual_id = str(uuid4())
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO characters (id, name, auto_created) VALUES (?, ?, 1)",
            (auto_id, "Synthetic Auto Delete Character"),
        )
        connection.execute(
            "INSERT INTO characters (id, name, auto_created) VALUES (?, ?, 0)",
            (manual_id, "Synthetic Manual Retained Character"),
        )
        connection.execute(
            "INSERT INTO character_visual_profiles (character_id, age_marker) VALUES (?, 'adult')",
            (auto_id,),
        )
        connection.execute(
            "INSERT INTO character_visual_profiles (character_id, age_marker) VALUES (?, 'adult')",
            (manual_id,),
        )
        connection.execute(
            "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
            (str(uuid4()), session_id, auto_id),
        )
        connection.execute(
            "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
            (str(uuid4()), session_id, manual_id),
        )
        connection.commit()
    job = delete_story(session_id)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        auto_remaining = connection.execute("SELECT COUNT(*) FROM characters WHERE id = ?", (auto_id,)).fetchone()[0]
        manual_remaining = connection.execute("SELECT COUNT(*) FROM characters WHERE id = ?", (manual_id,)).fetchone()[0]
        auto_profile_remaining = connection.execute(
            "SELECT COUNT(*) FROM character_visual_profiles WHERE character_id = ?", (auto_id,)
        ).fetchone()[0]
        manual_profile_remaining = connection.execute(
            "SELECT COUNT(*) FROM character_visual_profiles WHERE character_id = ?", (manual_id,)
        ).fetchone()[0]
        connection.execute("DELETE FROM characters WHERE id = ?", (manual_id,))
        connection.commit()
    return {
        "job_status": job.get("status"),
        "auto_character_removed": auto_remaining == 0,
        "auto_visual_profile_removed": auto_profile_remaining == 0,
        "manual_character_preserved": manual_remaining == 1,
        "manual_visual_profile_preserved": manual_profile_remaining == 1,
        "manual_fixture_removed_after_verification": True,
        "deleted_counts": job.get("deleted_counts") or {},
    }


def tts_gauntlet(original_settings: dict[str, Any]) -> dict[str, Any]:
    test_settings = dict(original_settings)
    entries = list(test_settings.get("pronunciation_entries") or [])
    entries.append({
        "id": "revamp-aeron",
        "written_form": "Aeron",
        "spoken_form": "AIR-on",
        "scope": "global",
        "story_id": None,
        "enabled": True,
        "notes": "Synthetic acceptance fixture",
    })
    test_settings["pronunciation_entries"] = entries
    saved = request_json("/settings/tts", method="PUT", payload=test_settings, timeout=30)
    chunks = [
        f"Synthetic narration chunk {index + 1}. Aeron checks the lantern, pauses, and continues with a steady voice."
        for index in range(21)
    ]
    latencies: list[float] = []
    audio_urls: list[str] = []
    started = time.monotonic()
    for index, text in enumerate(chunks):
        chunk_started = time.monotonic()
        response = request_json(
            "/tts/synthesize",
            method="POST",
            payload={
                "text": text,
                "provider": "kokoro",
                "voice_profile_id": "natural_female_narrator",
                "voice": saved.get("tts_voice") or "af_aoede",
                "speed": saved.get("tts_speed") or 0.95,
                "chunk_index": index,
                "chunk_count": len(chunks),
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "follow_mode": "sentence",
            },
            timeout=180,
        )
        latencies.append(round(time.monotonic() - chunk_started, 3))
        audio_urls.append(response.get("audio_url") or "")
    cache_started = time.monotonic()
    cached = request_json(
        "/tts/synthesize",
        method="POST",
        payload={
            "text": chunks[0],
            "provider": "kokoro",
            "voice_profile_id": "natural_female_narrator",
            "voice": saved.get("tts_voice") or "af_aoede",
            "speed": saved.get("tts_speed") or 0.95,
            "chunk_index": 0,
            "chunk_count": len(chunks),
            "text_hash": hashlib.sha256(chunks[0].encode("utf-8")).hexdigest(),
            "follow_mode": "sentence",
        },
        timeout=60,
    )
    return {
        "provider": "kokoro",
        "profile": "natural_female_narrator",
        "voice": saved.get("tts_voice") or "af_aoede",
        "chunks_synthesized": len(audio_urls),
        "all_audio_urls_returned": all(audio_urls),
        "first_audio_seconds": latencies[0] if latencies else None,
        "median_chunk_seconds": sorted(latencies)[len(latencies) // 2] if latencies else None,
        "max_chunk_seconds": max(latencies) if latencies else None,
        "total_seconds": round(time.monotonic() - started, 3),
        "cache_reuse_seconds": round(time.monotonic() - cache_started, 3),
        "cache_same_url": cached.get("audio_url") == audio_urls[0] if audio_urls else False,
        "pronunciation_alias_saved": any(item.get("id") == "revamp-aeron" for item in saved.get("pronunciation_entries") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete StoryDriver synthetic acceptance gauntlet.")
    parser.add_argument("--keep-fixtures-on-failure", action="store_true")
    parser.add_argument("--failed-only", action="store_true", help="Rerun TTS, privacy, and deletion after a focused fix.")
    parser.add_argument("--long-story-only", action="store_true", help="Run the nine-scene retention threshold check.")
    args = parser.parse_args()

    if args.long_story_only:
        if not METRICS_PATH.exists():
            raise FileNotFoundError(f"Full-run metrics do not exist: {METRICS_PATH}")
        metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        metrics["long_story_rerun_started_at"] = utc_now()
        prior_errors = list(metrics.get("errors") or [])
        session_id: str | None = None
        long_results: list[GenerationResult] = []
        try:
            story = create_story("REVAMP GAUNTLET - Nine Scene Retention")
            session_id = story["id"]
            long_results.append(stream_generation(session_id, LONG_STORY_OPENING))
            for note in LONG_STORY_CONTINUATIONS:
                long_results.append(stream_generation(session_id, note))
            summary = wait_for_summary(session_id)
            state = wait_for_background_state(session_id)
            characters = request_json(f"/sessions/{session_id}/characters") or []
            names = sorted(
                {
                    str((item.get("character") or {}).get("name") or item.get("name") or "").strip()
                    for item in characters
                    if str((item.get("character") or {}).get("name") or item.get("name") or "").strip()
                }
            )
            prompt_sizes = generation_prompt_sizes(session_id)
            metrics["generations"].extend(result.metrics for result in long_results)
            metrics["stories"]["long_retention"] = {
                "scene_count": len(request_json(f"/sessions/{session_id}/scenes") or []),
                "summary_present": bool(summary and summary.get("summary_text")),
                "summary_chars": len((summary or {}).get("summary_text") or ""),
                "state_item_score": flatten_count(state),
                "character_names": names,
                "identity_filter_passed": names == ["Lysa Calder", "Tomas Reed"],
                "prompt_chars_first": prompt_sizes[0] if prompt_sizes else None,
                "prompt_chars_max": max(prompt_sizes) if prompt_sizes else None,
                "prompt_growth_bounded": bool(
                    prompt_sizes and max(prompt_sizes) <= max(50000, prompt_sizes[0] * 2)
                ),
            }
        except Exception as error:
            prior_errors.append(f"Long retention: {type(error).__name__}: {error}")
        finally:
            if session_id:
                try:
                    metrics["checks"]["long_story_deletion"] = delete_story(session_id)
                except Exception as error:
                    prior_errors.append(f"Long retention deletion: {error}")
        metrics["errors"] = prior_errors
        metrics["long_story_rerun_finished_at"] = utc_now()
        metrics["db_counts_after"] = database_counts()
        long_check = metrics.get("stories", {}).get("long_retention", {})
        metrics["passed"] = (
            not metrics["errors"]
            and long_check.get("scene_count") == 9
            and long_check.get("summary_present") is True
            and long_check.get("identity_filter_passed") is True
            and long_check.get("prompt_growth_bounded") is True
            and metrics["db_counts_after"].get("sessions") == 0
            and metrics["db_counts_after"].get("characters") == 0
        )
        METRICS_PATH.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Long-story acceptance result: {'PASS' if metrics['passed'] else 'FAIL'}")
        if metrics["errors"]:
            for error in metrics["errors"]:
                print(f"ERROR: {error}")
        return 0 if metrics["passed"] else 1

    if args.failed_only:
        if not METRICS_PATH.exists():
            raise FileNotFoundError(f"Full-run metrics do not exist: {METRICS_PATH}")
        metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        metrics["focused_rerun_started_at"] = utc_now()
        metrics["errors"] = []
        original_tts = request_json("/settings/tts")
        try:
            metrics["checks"]["orphan_character_dependents_removed"] = cleanup_orphan_character_dependents()
            metrics["checks"]["pre_fix_orphan_auto_characters_removed"] = cleanup_orphan_auto_characters()
            metrics["tts"] = tts_gauntlet(original_tts)
            external_settings = dict(original_tts)
            external_settings["kokoro_base_url"] = "https://example.invalid/tts"
            privacy_status_code, _ = request_status("/settings/tts", method="PUT", payload=external_settings)
            metrics["checks"]["external_tts_endpoint_rejected"] = privacy_status_code in {400, 422}
            metrics["checks"]["external_tts_endpoint_status"] = privacy_status_code
            metrics["checks"]["deletion_fixture"] = deletion_fixture()
        except Exception as error:
            metrics["errors"].append(f"{type(error).__name__}: {error}")
        finally:
            try:
                request_json("/settings/tts", method="PUT", payload=original_tts, timeout=30)
                metrics["checks"]["tts_settings_restored"] = True
            except Exception as error:
                metrics["checks"]["tts_settings_restored"] = False
                metrics["errors"].append(f"TTS restore failed: {error}")
        metrics["focused_rerun_finished_at"] = utc_now()
        metrics["db_counts_after"] = database_counts()
        deletion_ok = all(
            metrics["checks"].get("deletion_fixture", {}).get(key) is True
            for key in (
                "auto_character_removed",
                "auto_visual_profile_removed",
                "manual_character_preserved",
                "manual_visual_profile_preserved",
            )
        )
        tts_ok = bool(metrics.get("tts", {}).get("all_audio_urls_returned"))
        long_check = metrics.get("stories", {}).get("long_retention")
        long_ok = not long_check or all(
            (
                long_check.get("scene_count") == 9,
                long_check.get("summary_present") is True,
                long_check.get("identity_filter_passed") is True,
                long_check.get("prompt_growth_bounded") is True,
            )
        )
        metrics["passed"] = (
            not metrics["errors"]
            and metrics["db_counts_after"].get("sessions") == 0
            and metrics["db_counts_after"].get("characters") == 0
            and metrics["db_counts_after"].get("foreign_key_violations") == 0
            and metrics["checks"].get("external_tts_endpoint_rejected") is True
            and deletion_ok
            and tts_ok
            and long_ok
        )
        METRICS_PATH.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Focused acceptance result: {'PASS' if metrics['passed'] else 'FAIL'}")
        if metrics["errors"]:
            for error in metrics["errors"]:
                print(f"ERROR: {error}")
        return 0 if metrics["passed"] else 1

    metrics: dict[str, Any] = {
        "started_at": utc_now(),
        "base_url": BASE_URL,
        "db_bytes_before": DB_PATH.stat().st_size,
        "db_counts_before": database_counts(),
        "stories": {},
        "generations": [],
        "checks": {},
        "errors": [],
    }
    created_session_ids: list[str] = []
    original_tts = request_json("/settings/tts")
    should_cleanup = True

    try:
        diagnostics = request_json("/diagnostics", timeout=60)
        metrics["checks"]["privacy_diagnostics"] = diagnostics.get("privacy")
        metrics["checks"]["active_model"] = diagnostics.get("model_settings", {}).get("model")
        metrics["checks"]["writing_path"] = {
            key: diagnostics.get("model_settings", {}).get(key)
            for key in ("prose_prompt_mode", "writing_process_mode", "writing_path", "app_planning_enabled")
        }

        print("[A/D/E/F/G/H/J] Modern integrated story", flush=True)
        modern = create_story("REVAMP GAUNTLET - Modern Integrated")
        modern_id = modern["id"]
        created_session_ids.append(modern_id)
        modern_results: list[GenerationResult] = []
        opening = stream_generation(modern_id, MODERN_OPENING)
        modern_results.append(opening)
        metrics["generations"].append(opening.metrics)
        for note in MODERN_CONTINUATIONS:
            result = stream_generation(modern_id, note)
            modern_results.append(result)
            metrics["generations"].append(result.metrics)

        scenes_before_versions = request_json(f"/sessions/{modern_id}/scenes")
        target_scene_id = modern_results[1].scene["id"]
        version_counts = [next(scene for scene in scenes_before_versions if scene["id"] == target_scene_id)["version_count"]]
        for mode, note in [
            ("rewrite", "Rewrite the same key handoff scene from a closer Jules viewpoint. Preserve every event, location, object holder, and time boundary; add no new story beat."),
            ("revise", "Revise the current version for sharper distinct dialogue and cleaner blocking only. Keep Micah as the final holder of the brass utility key and do not advance time."),
            ("regenerate", "Regenerate this same scene with equivalent events and continuity. It must remain the key handoff scene, not a continuation."),
        ]:
            result = stream_generation(modern_id, note, mode=mode, target_scene_id=target_scene_id)
            metrics["generations"].append(result.metrics)
            scenes_now = request_json(f"/sessions/{modern_id}/scenes")
            target_now = next(scene for scene in scenes_now if scene["id"] == target_scene_id)
            version_counts.append(target_now["version_count"])
        scenes_after_versions = request_json(f"/sessions/{modern_id}/scenes")
        modern_state = wait_for_background_state(modern_id)
        modern_memories = request_json(f"/sessions/{modern_id}/memories") or []
        modern_summary = request_json(f"/sessions/{modern_id}/summary") or {}
        combined_modern = "\n".join(item.text for item in modern_results)
        metrics["stories"]["modern"] = {
            "scene_count": len(scenes_after_versions),
            "opening": text_checks(opening.text, required=["Dana", "Jules", "Micah", "apartment"], forbidden=["next morning", "hours later"]),
            "continuity": text_checks(combined_modern, required=["brass", "key", "wet", "palm", "gauze", "landlord"], forbidden=["teleported"]),
            "version_counts": version_counts,
            "version_scene_count_stable": len(scenes_before_versions) == len(scenes_after_versions),
            "state_item_score": flatten_count(modern_state),
            "memory_count": len(modern_memories) if isinstance(modern_memories, list) else flatten_count(modern_memories),
            "summary_present": bool(modern_summary),
        }

        print("[B] Medieval introduction boundary", flush=True)
        medieval = create_story("REVAMP GAUNTLET - Medieval Opening")
        medieval_id = medieval["id"]
        created_session_ids.append(medieval_id)
        medieval_result = stream_generation(medieval_id, MEDIEVAL_OPENING)
        metrics["generations"].append(medieval_result.metrics)
        metrics["stories"]["medieval"] = {
            "scene_count": len(request_json(f"/sessions/{medieval_id}/scenes")),
            "opening": text_checks(
                medieval_result.text,
                required=["Elian", "Sabine", "Odo", "ledger", "map", "hearth"],
                forbidden=["next morning", "rode out", "the battle began", "rescued the cart"],
            ),
        }

        print("[C] Science-fiction technical continuity", flush=True)
        scifi = create_story("REVAMP GAUNTLET - Science Fiction")
        scifi_id = scifi["id"]
        created_session_ids.append(scifi_id)
        scifi_opening = stream_generation(scifi_id, SCIFI_OPENING)
        scifi_continue = stream_generation(scifi_id, SCIFI_CONTINUE)
        metrics["generations"].extend([scifi_opening.metrics, scifi_continue.metrics])
        scifi_state = wait_for_background_state(scifi_id)
        metrics["stories"]["science_fiction"] = {
            "scene_count": len(request_json(f"/sessions/{scifi_id}/scenes")),
            "opening": text_checks(scifi_opening.text, required=["Rhea", "Sol", "Imani", "wafer", "scrubber"], forbidden=["technobabble", "docked"]),
            "continuation": text_checks(scifi_continue.text, required=["Sol", "wafer", "log"], forbidden=["identified the saboteur", "after docking"]),
            "state_item_score": flatten_count(scifi_state),
        }

        print("[I] Adult fictional capability", flush=True)
        adult = create_story("REVAMP GAUNTLET - Adult Fiction")
        adult_id = adult["id"]
        created_session_ids.append(adult_id)
        adult_result = stream_generation(adult_id, ADULT_SCENE)
        metrics["generations"].append(adult_result.metrics)
        adult_lower = adult_result.text.lower()
        metrics["stories"]["adult_fiction"] = {
            "scene_count": len(request_json(f"/sessions/{adult_id}/scenes")),
            "checks": text_checks(adult_result.text, required=["Elena", "Noor", "consent"], forbidden=["as an ai", "i cannot", "i can't"]),
            "adult_status_present": any(term in adult_lower for term in ["thirty", "mid-thirties", "thirties", "adult"]),
            "direct_intimacy_signal_count": sum(adult_lower.count(term) for term in ["kiss", "mouth", "skin", "touch", "desire", "breath"]),
        }

        print("[K] Kokoro progressive narration", flush=True)
        metrics["tts"] = tts_gauntlet(original_tts)

        print("[L] Endpoint privacy rejection", flush=True)
        external_settings = dict(original_tts)
        external_settings["kokoro_base_url"] = "https://example.invalid/tts"
        privacy_status_code, _ = request_status("/settings/tts", method="PUT", payload=external_settings)
        metrics["checks"]["external_tts_endpoint_rejected"] = privacy_status_code in {400, 422}
        metrics["checks"]["external_tts_endpoint_status"] = privacy_status_code

    except Exception as error:
        metrics["errors"].append(f"{type(error).__name__}: {error}")
        should_cleanup = not args.keep_fixtures_on_failure
    finally:
        try:
            request_json("/settings/tts", method="PUT", payload=original_tts, timeout=30)
            metrics["checks"]["tts_settings_restored"] = True
        except Exception as error:
            metrics["checks"]["tts_settings_restored"] = False
            metrics["errors"].append(f"TTS restore failed: {error}")

        if should_cleanup:
            deletion_results = []
            for session_id in reversed(created_session_ids):
                try:
                    deletion_results.append(delete_story(session_id))
                except Exception as error:
                    deletion_results.append({"session_id": session_id, "status": "failed", "error": str(error)})
            metrics["deletion_results"] = [
                {"status": item.get("status"), "session_id": item.get("session_id"), "error": item.get("error")}
                for item in deletion_results
            ]

    metrics["finished_at"] = utc_now()
    metrics["db_bytes_after"] = DB_PATH.stat().st_size
    metrics["db_counts_after"] = database_counts()
    metrics["passed"] = not metrics["errors"] and metrics["db_counts_after"].get("sessions") == 0
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Acceptance metrics: {METRICS_PATH}")
    print(f"Result: {'PASS' if metrics['passed'] else 'FAIL'}")
    if metrics["errors"]:
        for error in metrics["errors"]:
            print(f"ERROR: {error}")
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
