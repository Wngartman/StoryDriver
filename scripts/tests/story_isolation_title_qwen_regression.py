from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
TEMP_ROOT = ROOT / "backend" / "data" / "temp" / "story_isolation_title_qwen_regression"
os.environ["STORYDRIVER_DATA_DIR"] = str(TEMP_ROOT)
os.environ["STORYDRIVER_DB_PATH"] = str(TEMP_ROOT / "app.db")
sys.path.insert(0, str(ROOT / "backend"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def insert_story(db, title: str = "Untitled Story") -> str:
    story_id = str(uuid4())
    db.execute(
        "INSERT INTO sessions (id, title, title_source, auto_title_status) VALUES (?, ?, 'placeholder', 'skipped')",
        (story_id, title),
    )
    return story_id


def insert_scene(db, story_id: str, text: str, note: str) -> tuple[str, str]:
    scene_id = str(uuid4())
    version_id = str(uuid4())
    db.execute(
        "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, 'continue')",
        (scene_id, story_id, note, text),
    )
    db.execute(
        """
        INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
        VALUES (?, ?, ?, ?, ?, 'continue', 1)
        """,
        (version_id, scene_id, story_id, note, text),
    )
    return scene_id, version_id


def foundation_cases() -> dict:
    from app.database import db_session
    from app.memory.foundation import apply_foundation_to_story, foundation_from_manual_inputs, save_story_foundation

    with db_session() as db:
        story_a = insert_story(db)
        story_b = insert_story(db)
    note_a = (
        "Mara Ellis and June Avery reach an abandoned station. Their loyalty carries unresolved tension. "
        "Mara holds a brass key and June carries a canvas pack."
    )
    note_b = "A medieval man alone in the woods prepares a dry shelter before night. No magic."
    foundation_a = foundation_from_manual_inputs(
        director_note=note_a,
        manual_characters=[{"name": "Mara Ellis"}, {"name": "June Avery"}],
        world_notes={},
    )
    foundation_b = foundation_from_manual_inputs(director_note=note_b, manual_characters=[], world_notes={})
    saved_a = save_story_foundation(
        session_id=story_a,
        foundation=foundation_a,
        source_director_note=note_a,
        status="fallback",
        source_kind="fallback",
        source_generation_id=str(uuid4()),
    )
    saved_b = save_story_foundation(
        session_id=story_b,
        foundation=foundation_b,
        source_director_note=note_b,
        status="fallback",
        source_kind="fallback",
        source_generation_id=str(uuid4()),
    )
    apply_foundation_to_story(story_a, foundation_a)
    apply_foundation_to_story(story_b, foundation_b)
    names_a = {row["name"] for row in saved_a["foundation"]["characters"]}
    names_b = {row["name"] for row in saved_b["foundation"]["characters"]}
    require({"Mara Ellis", "June Avery"}.issubset(names_a), "Story A fixture was not derived correctly.")
    require(len(names_b) == 1, f"Story B should have one protagonist, found {sorted(names_b)}.")
    require(not names_b.intersection({"Mara Ellis", "June Avery"}), "Fallback reused Story A names.")
    require(saved_b["session_id"] == story_b, "Foundation row lost story scope.")
    require(saved_b["source_kind"] == "fallback", "Fallback provenance was not retained.")
    require(len(saved_b["source_opening_note_checksum"]) == 64, "Opening-note checksum is missing.")
    with db_session() as db:
        attached_b = {
            row["name"]
            for row in db.execute(
                """
                SELECT c.name FROM session_characters sc
                JOIN characters c ON c.id = sc.character_id
                WHERE sc.session_id = ?
                """,
                (story_b,),
            ).fetchall()
        }
        require(attached_b == names_b, f"Story B cards are not isolated: {sorted(attached_b)}")
    return {"story_a": story_a, "story_b": story_b, "story_b_names": sorted(names_b)}


async def title_case(manual_override: bool = False, invalid_target: bool = False) -> None:
    from app.database import db_session
    import app.routes.sessions as sessions_route

    with db_session() as db:
        story_a = insert_story(db)
        story_b = insert_story(db)
        scene_a, version_a = insert_scene(db, story_a, "A synthetic opening about a copper bell.", "Open quietly.")
        scene_b, version_b = insert_scene(db, story_b, "A synthetic opening about a winter road.", "Open alone.")
        job_id = str(uuid4())
        db.execute(
            """
            UPDATE sessions SET auto_title_status='pending', auto_title_job_id=?,
                auto_title_scene_id=?, auto_title_version_id=? WHERE id=?
            """,
            (job_id, scene_a, version_a, story_a),
        )

    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_title(_story_id: str, _text: str, director_note: str = "") -> str:
        started.set()
        if manual_override:
            await release.wait()
        return "The Copper Bell"

    original = sessions_route.generate_short_title
    sessions_route.generate_short_title = fake_title
    try:
        task = asyncio.create_task(
            sessions_route.run_auto_title_update(
                story_a,
                scene_text="A synthetic opening about a copper bell.",
                director_note="Open quietly.",
                source="background",
                job_id=job_id,
                scene_id=scene_b if invalid_target else scene_a,
                version_id=version_b if invalid_target else version_a,
            )
        )
        if manual_override:
            await started.wait()
            with db_session() as db:
                db.execute(
                    """
                    UPDATE sessions SET title='My Manual Title', title_source='user_set',
                        auto_title_status='user_set', auto_title_job_id=NULL,
                        auto_title_scene_id=NULL, auto_title_version_id=NULL WHERE id=?
                    """,
                    (story_a,),
                )
            release.set()
        await task
    finally:
        sessions_route.generate_short_title = original

    with db_session() as db:
        row_a = db.execute("SELECT * FROM sessions WHERE id=?", (story_a,)).fetchone()
        row_b = db.execute("SELECT * FROM sessions WHERE id=?", (story_b,)).fetchone()
    if invalid_target:
        require(row_a["title"] == "Untitled Story", "A cross-story title target was accepted.")
    elif manual_override:
        require(row_a["title"] == "My Manual Title", "A stale title job overwrote a manual title.")
    else:
        require(row_a["title"] == "The Copper Bell", "The captured title job did not update its story.")
        require(row_a["auto_title_status"] == "generated", "The title job did not complete.")
    require(row_b["title"] == "Untitled Story", "Story A's title job changed Story B.")


def narration_scope_case() -> None:
    from app.database import db_session
    from app.schemas import TTSSynthesizeRequest, TTSSynthesizeResponse
    from app.tts.service import persist_narration_response

    with db_session() as db:
        story_a = insert_story(db)
        story_b = insert_story(db)
        scene_a, version_a = insert_scene(db, story_a, "First local narration fixture.", "Narrate A.")
        scene_b, version_b = insert_scene(db, story_b, "Second local narration fixture.", "Narrate B.")
    job_id = str(uuid4())
    response = TTSSynthesizeResponse(
        provider="high_quality_local",
        effective_provider="high_quality_local",
        effective_voice="Serena",
        effective_model="Qwen3-TTS-12Hz-0.6B-CustomVoice",
        cache_key="fixture-cache",
        duration=1.0,
    )
    payload_a = TTSSynthesizeRequest(
        text="First local narration fixture.", provider="high_quality_local",
        session_id=story_a, scene_id=scene_a, version_id=version_a,
        narration_job_id=job_id, chunk_index=0, chunk_count=1, text_hash="prose-a",
    )
    persist_narration_response(payload_a, response)
    payload_b = payload_a.model_copy(
        update={"session_id": story_b, "scene_id": scene_b, "version_id": version_b, "text_hash": "prose-b"}
    )
    persist_narration_response(payload_b, response)
    with db_session() as db:
        job = db.execute("SELECT * FROM narration_jobs WHERE id=?", (job_id,)).fetchone()
        chunks = db.execute("SELECT * FROM narration_chunks WHERE job_id=?", (job_id,)).fetchall()
    require(job["session_id"] == story_a, "A reused narration job crossed stories.")
    require(job["effective_model"].startswith("Qwen3-TTS"), "Effective model was not persisted.")
    require(job["prose_checksum"] == "prose-a", "Narration prose identity changed across stories.")
    require(len(chunks) == 1 and chunks[0]["text_hash"] == "prose-a", "Cross-story chunks mixed.")


def source_contract_case(case: str) -> None:
    store = (ROOT / "frontend" / "src" / "store" / "useAppStore.js").read_text(encoding="utf-8")
    player = (ROOT / "frontend" / "src" / "components" / "NarrationMiniPlayer.jsx").read_text(encoding="utf-8")
    feed = (ROOT / "frontend" / "src" / "components" / "StoryFeed.jsx").read_text(encoding="utf-8")
    controller = (ROOT / "frontend" / "src" / "services" / "ttsController.js").read_text(encoding="utf-8")
    ui_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend" / "src" / "components").glob("*.jsx")
    )
    if case in {"frontend", "mobile"}:
        require("storyContextRequestSequence" in store, "Story Details has no stale-response guard.")
        require("get().activeSessionId !== sessionId" in store, "Story Details responses are not active-story checked.")
        require("streamingScene.session_id === activeSession?.id" in feed, "Streaming prose is not story scoped.")
        require("currentNarrationSessionId" in store, "Narration does not retain its source story.")
    if case == "product":
        require("Qwen3-TTS 0.6B" in player and "Kokoro fallback" in player, "Effective product labels are missing.")
        require("High-quality local" not in ui_text and "High quality local" not in ui_text, "Generic provider wording remains in normal UI.")
    if case in {"scheduler", "style", "breath"}:
        require("Qwen ordered batch producer failed" in controller, "The ordered producer is missing.")
        require("for (let batchStart = premiumBatchStart(startIndex)" in controller, "Qwen production is not continuous.")
        require("Math.min(1500" in controller, "Frontend pause metadata is not clamped.")
        require("Math.min(1200" in controller, "Playback pause enforcement is missing.")


async def run(case: str) -> None:
    from app.database import init_db

    init_db()
    if case in {"cross_story", "foundation"}:
        foundation_cases()
    elif case == "title":
        await title_case()
    elif case == "manual_title":
        await title_case(manual_override=True)
    elif case == "async_scope":
        await title_case(invalid_target=True)
    elif case == "narration_scope":
        narration_scope_case()
    elif case in {"frontend", "mobile", "product", "scheduler", "style", "breath"}:
        source_contract_case(case)
    else:
        raise ValueError(f"Unknown case: {case}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case")
    args = parser.parse_args()
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(run(args.case))
        print(f"PASS: {args.case}")
        return 0
    finally:
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
