from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.generation.pipeline import deterministic_quality_review, deterministic_scene_plan
from app.memory.engine import physical_position_is_usable


BACKEND = "http://localhost:8001"
DB_PATH = ROOT / "backend" / "data" / "app.db"
METRICS = ROOT / "backend" / "data" / "logs" / "LONG_STORY_CONTINUITY_GAUNTLET_LATEST.json"
TEMP_OUTPUTS = ROOT / "backend" / "data" / "temp" / "product_polish_quality_outputs.json"

OPENING = (
    "Begin a present-day novel in the basement workshop of an aging Denver apartment building. Introduce four clearly adult friends, all in their thirties: "
    "Mara Vale, a guarded maintenance electrician in a navy work coat; June Park, an exacting tenant organizer in a red scarf; "
    "Ellis Grant, a warm but conflict-avoidant paramedic with a bandaged right palm; and Rowan Price, the anxious building bookkeeper whose left ankle is healing. "
    "A brass utility key belongs to Mara and begins clipped inside her coat. A black rent ledger lies on the workbench. Keep the entire scene in the workshop during the same twenty minutes. "
    "Introduce personalities, appearances, old friendship pressure, and the failing boiler. Do not contact the landlord, leave the building, reveal Rowan's secret, or resolve the rent dispute yet."
)

CONTINUATIONS = [
    "Continue in the workshop minutes later. June asks to borrow Mara's brass utility key to inspect the locked boiler panel. Dramatize the handoff clearly: Mara remains the owner; June becomes the holder. Ellis objects because of his injured palm and Rowan wants them to stop asking questions. Do not leave the workshop.",
    "Continue without a time skip. June opens the boiler panel while standing beside its north side; Mara kneels by the tool chest; Ellis waits near the stairs; Rowan blocks the inner storage door. Let each initiate a plausible action and keep the key in June's hand.",
    "Continue into the adjacent basement laundry room through the east door. Track all four entries. Rowan secretly slides the black rent ledger behind the loose wall tile beside the old dryer while the others argue; make the hiding action clear but only Rowan sees exactly where it goes. June still holds Mara's key.",
    "Continue in the laundry room. Mara notices the ledger is missing, and Rowan promises he will explain before midnight but lies about having moved it. June wants immediate honesty; Ellis wants to protect Rowan. Let the friendship history shape their choices. No one leaves the basement.",
    "Continue after a leaking pipe sprays the group. Mara removes her soaked navy work coat and hangs it on the pipe rail, revealing a gray thermal shirt. June wraps her red scarf around Ellis's injured palm as a temporary clean layer. Track clothing and objects; do not advance more than ten minutes.",
    "Continue in the same room. Ellis realizes Rowan is lying because Rowan knows which ledger page is missing. Let Ellis confront him quietly while June and Mara disagree about whether loyalty requires concealment or truth. Rowan does not confess the ledger's location yet.",
    "Continue as Rowan's healing left ankle gives way near the dryer. Ellis catches him despite the bandaged palm. Mara brings a folding chair from beside the utility sink; June keeps the key. Update blocking and injury limits without turning this into an emergency-room scene.",
    "Continue into the basement corridor. Mara and Ellis support Rowan between them; June walks ahead with the brass key. Stop at the locked courtyard door before anyone opens it. Keep the argument active and the object holders unambiguous.",
    "Continue at the courtyard door. June returns Mara's brass utility key into Mara's open hand because Mara must unlock the door herself. Mara remains owner and becomes holder again. Rowan asks them not to go outside; Ellis wants air for Rowan. End before the group crosses the threshold.",
    "Continue into the enclosed stone courtyard for no more than fifteen minutes. Mara stands by the north gate, June near the dry fountain, Ellis lowers Rowan onto the bench. June admits she is afraid Mara will choose the building over their friendship; Mara's attraction to June complicates her answer but does not erase the practical conflict.",
    "Continue in the courtyard. Rowan reveals the landlord ordered him to alter rent records but still hides where he put the ledger. Ellis wants to protect tenants; June wants proof; Mara wants to inspect the boiler first. Each person should press a different plan and initiate action.",
    "Continue as light rain starts. June gives Ellis back his damp red scarf and puts on a spare green rain shell from her tote. Rowan can walk only with support. Mara stores the brass key in her trouser pocket. Keep clothing, injury, and holder state exact.",
    "Continue back through the courtyard door into the basement corridor. Track the entry order and who can hear whom: Mara first, June second, Ellis supporting Rowan last. A slammed lobby door upstairs interrupts them, but do not introduce a new character.",
    "Continue in the corridor. Mara commits to repairing the boiler before confronting the landlord; June commits to preserving evidence; Ellis commits to keeping Rowan from bearing weight on the ankle. Rowan objects to all three plans and starts toward the laundry room anyway.",
    "Continue as June physically steps between Rowan and the laundry-room door without touching him. Let Rowan's shame, Ellis's protectiveness, Mara's suspicion, and June's anger change the relationship. No repeated speeches and no artificial cliffhanger.",
    "Continue when Rowan finally admits he hid the rent ledger somewhere in the laundry room but refuses to identify the exact place until Mara promises not to turn it over alone. Mara makes a narrow promise to show it to the whole group first. The ledger remains hidden.",
    "Continue back in the workshop, revisiting the original location after the intervening scenes. Mara retrieves her navy work coat from the pipe rail but does not put it on because it is still wet. June checks the boiler panel; Ellis seats Rowan near the stairs. Preserve all current injuries and clothing.",
    "Continue without restating the old event for the model: Rowan asks Mara to retrieve what he hid earlier, from the exact place where he hid it, while June watches. The prose should naturally recall the object and hiding location from story memory. Stop once Mara has recovered it and everyone can see it.",
    "Continue in the workshop. Mara places the recovered evidence flat on the workbench rather than handing it to one person. Let June and Rowan negotiate what truth they owe each other, Ellis challenge Mara's promise, and the relationships move in a concrete way.",
    "Continue for one final focused beat in the workshop before midnight. The four adults agree on a shared immediate plan for the boiler and evidence, but the landlord conflict remains unresolved. End with a natural handoff, not a cliffhanger or complete-story conclusion.",
]

TARGETED = {
    "contemporary_adventure_romance": (
        "Open a contemporary adventure-romance novel beside a flooded mountain road at dusk. Adult search volunteer Lena Ortiz and adult civil engineer Ash Mercer prepare ropes and a first-aid pack before crossing toward a stranded hiker. Establish mutual attraction through practical choices and old disagreement. Keep them on the safe bank for this scene; do not perform the rescue or skip ahead.",
        ["Lena", "Ash", "rope", "first-aid"],
        ["rescue was over", "days later"],
    ),
    "medieval": (
        "Open a grounded medieval no-magic adventure in a miller's hall before dawn. Introduce adult siblings Elian, Sabine, and Odo around a hearth with a ledger and road map. They plan how to recover a stolen grain cart but must not leave, reach battle, use magic, or complete the rescue. Give each a different goal and clear blocking.",
        ["Elian", "Sabine", "Odo", "ledger", "map"],
        ["battle began", "rode out", "magic spell"],
    ),
    "science_fiction": (
        "Open on a damaged survey ship's life-support deck. Adult engineer Rhea, medic Sol, and pilot Imani diagnose a carbon-scrubber fault and debate whether to use a suspect memory wafer. Keep technical dialogue human and understandable. Do not dock, identify a saboteur, or solve the larger mystery.",
        ["Rhea", "Sol", "Imani", "scrubber", "wafer"],
        ["docked", "saboteur was"],
    ),
    "mystery": (
        "Open a contemporary locked-office mystery. Adult archivist Celia and detective Tomas inspect a wet footprint, a switched brass key, and an untouched window while the museum director pressures them to leave. Keep the scene to first observations; do not reveal the culprit.",
        ["Celia", "Tomas", "footprint", "key", "window"],
        ["the culprit was", "case was solved"],
    ),
    "quiet_relationship": (
        "Write a quiet present-day relationship scene in one apartment kitchen over fifteen minutes. Adult siblings Mira and Dev sort their late father's unopened letter beside two cooling cups of tea. Mira wants to read it; Dev wants to wait. Let affection, resentment, and distinct private reasons shape small actions. Do not open the letter, leave the kitchen, reconcile everything, or skip time.",
        ["Mira", "Dev", "letter", "tea"],
        ["opened the letter", "the next morning"],
    ),
    "horror": (
        "Open a grounded psychological horror story in an isolated roadside motel office during a storm. Adult night clerk Tamsin Reed and adult stranded driver Joel Vance hear a room telephone ring even though the room key still hangs on the board. Build dread through practical investigation, sound, and conflicting motives. Do not reveal a monster, explain the call, or leave the office yet.",
        ["Tamsin", "Joel", "telephone", "key"],
        ["the monster was", "the call was explained"],
    ),
    "adult_fiction": (
        "Write a relationship-driven intimate scene between Elena, age thirty-four, and Noor, age thirty-six, established adult partners at home. They explicitly confirm consent and talk through a recent breach of trust before choosing direct, sensual intimacy. Keep agency mutual, psychology specific, and prose fictional rather than clinical or moralizing.",
        ["Elena", "Noor"],
        ["as an ai", "i cannot", "i can't help"],
    ),
    "long_chapter": (
        "Write a long, continuous chapter of roughly 1,400 to 1,900 words set during one storm-bound evening in a rural train station. Introduce adult stationmaster Ada Venn, adult courier Bram Holt, and adult physician Sera Lin. A locked dispatch case belongs to Bram and remains in his hands while they decide whether a washed-out bridge warning is genuine. Deepen introductions, plans, relationship pressure, blocking, and weather constraints without a time skip. Do not open the case, board a train, reach the bridge, or solve who sent the warning.",
        ["Ada", "Bram", "Sera", "dispatch case", "bridge"],
        ["opened the case", "the next day", "reached the bridge"],
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request(path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 60) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BACKEND + path, method=method, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def stream_generation(session_id: str, note: str, mode: str = "continue", target_scene_id: str | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{BACKEND}/sessions/{session_id}/generate-stream",
        method="POST",
        data=json.dumps({"director_note": note, "mode": mode, "target_scene_id": target_scene_id}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    started = time.perf_counter()
    first_delta = None
    scene = None
    stages: list[str] = []
    with urllib.request.urlopen(req, timeout=900) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "status":
                stage = str(event.get("stage") or "")
                if stage:
                    stages.append(stage)
                    print(f"  {mode}: {stage}", flush=True)
            elif event.get("type") == "delta" and first_delta is None:
                first_delta = time.perf_counter()
            elif event.get("type") == "scene":
                scene = event.get("scene") or event
            elif event.get("type") == "error":
                raise RuntimeError(event.get("detail") or "generation failed")
    if not scene:
        raise RuntimeError("generation returned no scene")
    text = str(scene.get("generated_text") or "")
    stats = scene.get("generation_stats") or {}
    stage_timings = stats.get("stage_timings") or {}
    prompt_diagnostics = stats.get("prompt_diagnostics") or {}
    return {
        "scene_id": scene.get("id"),
        "version_id": scene.get("active_version_id") or ((scene.get("versions") or [{}])[-1].get("id")),
        "version_count": scene.get("version_count"),
        "text": text,
        "word_count": len(re.findall(r"\b[\w'-]+\b", text)),
        "first_visible_seconds": round(first_delta - started, 3) if first_delta else None,
        "total_seconds": round(time.perf_counter() - started, 3),
        "planner_seconds": stats.get("planning_time_seconds") or stage_timings.get("scene_planning_seconds"),
        "prose_seconds": stage_timings.get("prose_model_seconds") or stats.get("elapsed_seconds"),
        "review_seconds": stats.get("review_time_seconds") or stage_timings.get("review_seconds"),
        "repair_applied": bool(stats.get("repair_ran") or stats.get("repair_applied") or stats.get("repair_used")),
        "prompt_chars": prompt_diagnostics.get("total_prompt_chars") or stats.get("prompt_chars"),
        "visible_tps": stats.get("visible_prose_tokens_per_second") or stats.get("tokens_per_second"),
        "stages": stages,
    }


def wait_for_state(session_id: str, scene_id: str, timeout: float = 210) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = request(f"/sessions/{session_id}/story-state", timeout=30) or {}
        run = latest.get("latest_run") or {}
        if run.get("scene_id") == scene_id and run.get("status") in {"completed", "skipped", "failed"}:
            return latest
        time.sleep(2)
    return latest


def delete_story(session_id: str) -> dict[str, Any]:
    result = request(f"/sessions/{session_id}?permanent=true", "DELETE") or {}
    job_id = result.get("job_id") or result.get("id")
    if not job_id:
        return result
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        job = request(f"/sessions/delete-jobs/{job_id}") or {}
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(1)
    return {"status": "timeout", "job_id": job_id}


def used_db_bytes() -> int:
    with sqlite3.connect(DB_PATH) as db:
        page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
        free_count = int(db.execute("PRAGMA freelist_count").fetchone()[0])
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    return max(0, page_count - free_count) * page_size


def session_db_counts(session_id: str) -> dict[str, int]:
    tables = (
        "scenes", "scene_versions", "generation_runs", "story_state_runs", "character_live_state",
        "character_state_events", "relationship_state", "relationship_state_events", "emotional_memories",
        "world_live_state", "scene_live_state", "object_state", "plot_threads", "state_snapshots",
        "story_memories", "session_summaries", "narration_jobs", "narration_chunks", "next_prompt_memory_cache_v3",
    )
    with sqlite3.connect(DB_PATH) as db:
        known = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            table: int(db.execute(f"SELECT count(*) FROM {table} WHERE session_id=?", (session_id,)).fetchone()[0])
            for table in tables
            if table in known and "session_id" in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        }


def canonical_checks(session_id: str) -> dict[str, Any]:
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        duplicate_locations = db.execute(
            """
            SELECT character_name, count(*) count FROM character_live_state
            WHERE session_id=? AND key='current_location' AND archived=0 AND disabled=0
            GROUP BY lower(character_name) HAVING count(*) > 1
            """,
            (session_id,),
        ).fetchall()
        duplicate_holders = db.execute(
            """
            SELECT object_key, count(DISTINCT lower(holder_character_name)) count FROM object_state
            WHERE session_id=? AND holder_character_name != '' AND archived=0 AND disabled=0
            GROUP BY object_key HAVING count > 1
            """,
            (session_id,),
        ).fetchall()
        generic_relationships = db.execute(
            """
            SELECT content FROM relationship_state WHERE session_id=? AND archived=0 AND disabled=0
              AND lower(trim(content)) IN ('active bond','core cast','relationship pressure affects choices','useful mix of trust, pressure, and disagreement')
            """,
            (session_id,),
        ).fetchall()
        invalid_positions = db.execute(
            """
            SELECT value FROM character_live_state WHERE session_id=? AND key='room_position' AND archived=0 AND disabled=0
              AND (lower(value) LIKE '%introduced or present%' OR lower(value) LIKE '%straps of%hurt%')
            """,
            (session_id,),
        ).fetchall()
        all_position_rows = db.execute(
            "SELECT character_name,value FROM character_live_state WHERE session_id=? AND key='room_position' AND archived=0 AND disabled=0",
            (session_id,),
        ).fetchall()
        invalid_positions = [*invalid_positions, *(row for row in all_position_rows if not physical_position_is_usable(row["value"]))]
        object_rows = [dict(row) for row in db.execute(
            "SELECT object_key,name,owner_character_name,holder_character_name,current_location,placement_state,condition,visibility FROM object_state WHERE session_id=? AND archived=0 AND disabled=0",
            (session_id,),
        ).fetchall()]
        malformed_suffixes = ("_from", "_tight", "_tightly", "_close", "_closely", "_firm", "_firmly")
        malformed_objects = [
            item for item in object_rows
            if item["object_key"].endswith(malformed_suffixes)
            or item["name"].lower().endswith(tuple(suffix.replace("_", " ") for suffix in malformed_suffixes))
        ]
        key_rows = [item for item in object_rows if "key" in item["object_key"].split("_")]
        brass_key = next((item for item in key_rows if "brass" in item["object_key"]), None)
    return {
        "duplicate_character_locations": [dict(row) for row in duplicate_locations],
        "duplicate_object_holders": [dict(row) for row in duplicate_holders],
        "generic_relationships": [dict(row) for row in generic_relationships],
        "invalid_positions": [dict(row) for row in invalid_positions],
        "malformed_objects": malformed_objects,
        "key_alias_count": len(key_rows),
        "brass_key_owner_preserved": bool(brass_key and brass_key["owner_character_name"] == "Mara Vale"),
        "brass_key_holder_preserved": bool(brass_key and brass_key["holder_character_name"] == "Mara Vale"),
        "objects": object_rows,
    }


def memory_probe(session_id: str) -> dict[str, Any]:
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    from app.memory.context import build_next_prompt_memory_pack

    started = time.perf_counter()
    relevant = build_next_prompt_memory_pack(
        session_id,
        relevance_text="Rowan asks Mara to recover the hidden rent evidence from its old hiding place in the laundry room.",
        context_kind="writer",
    )
    relevant_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    unrelated = build_next_prompt_memory_pack(
        session_id,
        relevance_text="The group discusses the boiler pressure and Ellis's injured palm.",
        context_kind="writer",
    )
    unrelated_ms = (time.perf_counter() - started) * 1000
    return {
        "relevant_ms": round(relevant_ms, 3),
        "unrelated_ms": round(unrelated_ms, 3),
        "relevant_chars": relevant["prompt_chars"],
        "unrelated_chars": unrelated["prompt_chars"],
        "relevant_old_memory_present": any(term in relevant["rendered_context"].lower() for term in ("ledger", "loose wall tile", "old dryer")),
        "bounded": relevant["prompt_chars"] <= 8000 and unrelated["prompt_chars"] <= 8000,
    }


def tts_stress(session_id: str, scene: dict[str, Any]) -> dict[str, Any]:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", scene["text"]) if item.strip()]
    while len(sentences) < 21:
        sentences.append(f"Local continuity narration checkpoint {len(sentences) + 1}.")
    chunks = sentences[:21]
    latencies: list[float] = []
    urls: list[str] = []
    for index, text in enumerate(chunks):
        started = time.perf_counter()
        response = request(
            "/tts/synthesize",
            "POST",
            {
                "text": text,
                "provider": "kokoro",
                "voice_profile_id": "natural_female_narrator",
                "speed": 0.95,
                "session_id": session_id,
                "scene_id": scene["scene_id"],
                "version_id": scene["version_id"],
                "chunk_index": index,
                "chunk_count": len(chunks),
            },
            timeout=180,
        )
        latencies.append(time.perf_counter() - started)
        urls.append(str(response.get("audio_url") or ""))
    repeat = request(
        "/tts/synthesize",
        "POST",
        {
            "text": chunks[0], "provider": "kokoro", "voice_profile_id": "natural_female_narrator", "speed": 0.95,
            "session_id": session_id, "scene_id": scene["scene_id"], "version_id": scene["version_id"], "chunk_index": 0, "chunk_count": len(chunks),
        },
        timeout=180,
    )
    return {
        "chunks": len(chunks),
        "all_audio_urls": all(urls),
        "first_audio_seconds": round(latencies[0], 3),
        "median_chunk_seconds": round(statistics.median(latencies), 3),
        "max_chunk_seconds": round(max(latencies), 3),
        "cache_reuse": bool(repeat.get("cached")),
    }


def text_contract(text: str, required: list[str], forbidden: list[str]) -> dict[str, Any]:
    lower = text.lower()
    return {
        "required": {term: term.lower() in lower for term in required},
        "forbidden": {term: term.lower() not in lower for term in forbidden},
        "assistant_tone_absent": not any(term in lower for term in ("as an ai", "here is the scene", "what happens next")),
        "word_count": len(re.findall(r"\b[\w'-]+\b", text)),
    }


def movement_quality(text: str) -> dict[str, Any]:
    paragraphs = [re.sub(r"\s+", " ", item.strip().lower()) for item in re.split(r"\n\s*\n", text) if item.strip()]
    sentences = [re.sub(r"\s+", " ", item.strip().lower()) for item in re.split(r"(?<=[.!?])\s+", text) if len(item.split()) >= 5]
    repeated_paragraphs = len(paragraphs) - len(set(paragraphs))
    repeated_sentences = len(sentences) - len(set(sentences))
    lower = text.lower()
    generic_cliffhanger = any(
        phrase in lower
        for phrase in ("little did they know", "everything was about to change", "to be continued", "this was only the beginning")
    )
    return {
        "paragraph_count": len(paragraphs),
        "repeated_paragraphs": repeated_paragraphs,
        "repeated_sentences": repeated_sentences,
        "generic_cliffhanger": generic_cliffhanger,
        "assistant_tone": any(phrase in lower for phrase in ("as an ai", "here is the scene", "what happens next")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-scenes", type=int, default=20, choices=range(1, 21))
    args = parser.parse_args()
    original_model = request("/settings/model")
    original_tts = request("/settings/tts")
    created: list[str] = []
    result: dict[str, Any] = {
        "started_at": utc_now(), "requested_real_scenes": args.real_scenes, "main_generations": [], "version_generations": [],
        "targeted": {}, "state_runs": [], "errors": [],
    }
    temporary_outputs: dict[str, Any] = {"main": [], "targeted": {}}
    db_before = used_db_bytes()
    try:
        request(
            "/settings/model",
            "PUT",
            {
                **original_model,
                "active_preset_id": None,
                "model": original_model.get("model") or "gemma4-26b-a4b-uncensored-hauhaucs-balanced",
                "writing_length_mode": "beat",
                "max_tokens": max(8000, int(original_model.get("max_tokens") or 0)),
                "streaming": True,
                "writing_path": "deliberate_pipeline",
                "writing_process_mode": "deliberate",
                "app_planning_enabled": True,
            },
        )
        main_story = request("/sessions", "POST", {"title": "LONG CONTINUITY GAUNTLET DISPOSABLE"})
        main_id = main_story["id"]
        created.append(main_id)
        notes = [OPENING, *CONTINUATIONS][: args.real_scenes]
        for index, note in enumerate(notes):
            print(f"[main] scene {index + 1}/{len(notes)}", flush=True)
            generated = stream_generation(main_id, note)
            result["main_generations"].append({
                **{key: value for key, value in generated.items() if key != "text"},
                "movement_quality": movement_quality(generated["text"]),
            })
            temporary_outputs["main"].append({"scene": index + 1, "text": generated["text"]})
            state = wait_for_state(main_id, generated["scene_id"])
            run = state.get("latest_run") or {}
            result["state_runs"].append({"scene": index + 1, "scene_id": generated["scene_id"], "status": run.get("status"), "error": run.get("error")})

            if index == min(8, len(notes) - 1):
                target_scene_id = generated["scene_id"]
                for version_mode, version_note in (
                    ("rewrite", "Rewrite this same courtyard-door scene from June's close-third viewpoint. Preserve the key handoff, all four adults, the corridor location, and the exact ending boundary. Add no later beat."),
                    ("regenerate", "Regenerate the same target scene with equivalent events and blocking. Mara must end as owner and holder of the brass utility key. Do not continue beyond the locked courtyard door."),
                    ("revise", "Revise this same scene for clearer positions, distinct objections, and stronger relationship pressure. Preserve every event and end before anyone crosses the threshold."),
                ):
                    version = stream_generation(main_id, version_note, version_mode, target_scene_id)
                    version_state = wait_for_state(main_id, target_scene_id)
                    result["version_generations"].append(
                        {
                            **{key: value for key, value in version.items() if key != "text"},
                            "mode": version_mode,
                            "same_scene": version["scene_id"] == target_scene_id,
                            "state_status": (version_state.get("latest_run") or {}).get("status"),
                        }
                    )

        scenes = request(f"/sessions/{main_id}/scenes") or []
        result["scene_count"] = len(scenes)
        result["version_scene_count_stable"] = len(scenes) == len(notes)
        result["canonical"] = canonical_checks(main_id)
        result["memory"] = memory_probe(main_id)
        result["session_rows"] = session_db_counts(main_id)
        db_during = used_db_bytes()
        growth = max(0, db_during - db_before)
        result["storage"] = {
            "used_db_bytes_before": db_before,
            "used_db_bytes_during": db_during,
            "growth_bytes": growth,
            "bytes_per_scene": round(growth / max(1, len(notes))),
            "projected_1000_scene_bytes": round(growth / max(1, len(notes)) * 1000),
        }
        final_scene = {**result["main_generations"][-1], "text": next(item for item in reversed(scenes) if item["id"] == result["main_generations"][-1]["scene_id"])["generated_text"]}
        result["tts"] = tts_stress(main_id, final_scene)

        request("/settings/model", "PUT", {**request("/settings/model"), "writing_length_mode": "scene"})
        for label, (note, required, forbidden) in TARGETED.items():
            print(f"[targeted] {label}", flush=True)
            target_length = "chapter" if label == "long_chapter" else "scene"
            current_target_settings = request("/settings/model")
            if current_target_settings.get("writing_length_mode") != target_length:
                request("/settings/model", "PUT", {**current_target_settings, "writing_length_mode": target_length})
            story = request("/sessions", "POST", {"title": f"TARGETED {label.upper()} DISPOSABLE"})
            created.append(story["id"])
            generated = stream_generation(story["id"], note)
            hard_plan = deterministic_scene_plan(
                session_id=story["id"],
                mode="continue",
                director_note=note,
                writing_length={"mode": target_length, "label": target_length.title(), "min_words": 0, "max_words": 1900},
                recent_scenes=[],
                session_summary=None,
                target_scene=None,
                world_notes=None,
                active_characters=[],
            )
            hard_review = deterministic_quality_review(
                scene_plan=hard_plan,
                draft_text=generated["text"],
                writing_length={"min_words": 0},
            )
            result["targeted"][label] = {
                **text_contract(generated["text"], required, forbidden),
                "movement_quality": movement_quality(generated["text"]),
                "hard_contract_review": hard_review,
                "metrics": {key: value for key, value in generated.items() if key != "text"},
            }
            temporary_outputs["targeted"][label] = generated["text"]

        canonical = result["canonical"]
        if (
            canonical["duplicate_character_locations"]
            or canonical["duplicate_object_holders"]
            or canonical["generic_relationships"]
            or canonical["invalid_positions"]
            or canonical["malformed_objects"]
            or canonical["key_alias_count"] != 1
            or not canonical["brass_key_owner_preserved"]
            or not canonical["brass_key_holder_preserved"]
        ):
            result["errors"].append("canonical continuity checks found active conflicts or generic facts")
        if not result["memory"]["relevant_old_memory_present"] or not result["memory"]["bounded"] or result["memory"]["relevant_ms"] >= 100:
            result["errors"].append(f"memory retrieval contract failed: {result['memory']}")
        if not result["version_scene_count_stable"] or not all(item["same_scene"] for item in result["version_generations"]):
            result["errors"].append("rewrite/revise/regenerate appended or changed the target scene")
        if not all(item["status"] in {"completed", "skipped"} for item in result["state_runs"]):
            result["errors"].append("one or more real Story State runs failed")
        if not all(result["tts"].get(key) for key in ("all_audio_urls", "cache_reuse")) or result["tts"]["chunks"] < 21:
            result["errors"].append(f"Kokoro stress failed: {result['tts']}")
        for label, check in result["targeted"].items():
            if not all(check["required"].values()) or not all(check["forbidden"].values()) or not check["assistant_tone_absent"]:
                result["errors"].append(f"targeted prose contract failed: {label}")
            if (check.get("hard_contract_review") or {}).get("severity") == "major":
                result["errors"].append(f"targeted hard-contract review failed: {label}: {check['hard_contract_review'].get('issues')}")
            if label == "long_chapter" and int(check.get("word_count") or 0) < 1200:
                result["errors"].append(f"long chapter was shorter than 1200 words: {check.get('word_count')}")
            movement = check.get("movement_quality") or {}
            if movement.get("repeated_paragraphs") or movement.get("repeated_sentences") or movement.get("generic_cliffhanger") or movement.get("assistant_tone"):
                result["errors"].append(f"targeted movement/repetition contract failed: {label}: {movement}")
        for scene in result["main_generations"]:
            movement = scene.get("movement_quality") or {}
            if movement.get("repeated_paragraphs") or movement.get("repeated_sentences") or movement.get("generic_cliffhanger") or movement.get("assistant_tone"):
                result["errors"].append(f"connected-story movement/repetition contract failed: {movement}")
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        request("/settings/model", "PUT", original_model)
        request("/settings/tts", "PUT", original_tts)
        result["settings_restored"] = request("/settings/model") == original_model and request("/settings/tts") == original_tts
        result["deletions"] = [delete_story(session_id) for session_id in reversed(created)]
        result["db_integrity"] = sqlite3.connect(DB_PATH).execute("PRAGMA integrity_check").fetchone()[0]
        result["finished_at"] = utc_now()
        result["passed"] = (
            not result["errors"] and result["settings_restored"] and result["db_integrity"] == "ok"
            and all(item.get("status") == "completed" for item in result["deletions"])
        )
        METRICS.parent.mkdir(parents=True, exist_ok=True)
        METRICS.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        TEMP_OUTPUTS.parent.mkdir(parents=True, exist_ok=True)
        TEMP_OUTPUTS.write_text(json.dumps(temporary_outputs, indent=2), encoding="utf-8")
    print(f"Metrics: {METRICS}")
    print("PASS" if result["passed"] else "FAIL")
    for error in result["errors"]:
        print(f"  {error}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
