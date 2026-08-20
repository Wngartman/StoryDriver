from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.utils.openssl_dlls import add_openssl_dll_directory  # noqa: E402

add_openssl_dll_directory()

from app.database import db_session, init_db  # noqa: E402
from app.memory.characters import upsert_detected_characters  # noqa: E402
from app.services.image_prompt_service import fallback_prompt, load_visual_context  # noqa: E402
from app.memory.engine import (  # noqa: E402
    create_run,
    detect_story_state_conflicts,
    merge_state_payload,
    update_run,
    utc_now,
)


REPORT_PATH = BACKEND / "data" / "logs" / "CHARACTER_STATE_CONSISTENCY_REPORT.md"
SESSION_TITLE = "StoryDriver Character State Consistency Smoke Test"

INTRO_TEXT = (
    "Elara Voss, an adult woman with dark braided hair, a narrow tired face, and a red cloak, "
    "leaned over the farmhouse table. Lyra Nale, an adult woman with cropped brown hair and a compact build, "
    "carried the bronze compass in her gloved hand. Maren Keth, an adult woman with fair hair, broad shoulders, "
    "and a pale scar at her jaw, watched from the door."
)


def insert_scene(session_id: str, text: str, note: str) -> tuple[str, str]:
    scene_id = str(uuid4())
    version_id = str(uuid4())
    with db_session() as db:
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
            VALUES (?, ?, ?, ?, 'continue')
            """,
            (scene_id, session_id, note, text),
        )
        db.execute(
            """
            INSERT INTO scene_versions (
                id, scene_id, session_id, director_note, generated_text, mode, version_index
            )
            VALUES (?, ?, ?, ?, ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, note, text),
        )
    return scene_id, version_id


def create_session() -> str:
    session_id = str(uuid4())
    with db_session() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, SESSION_TITLE))
    return session_id


def auto_create_test_characters(session_id: str, scene_id: str, version_id: str) -> dict[str, str]:
    detected = [
        {
            "name": "Elara Voss",
            "role": "oldest sister",
            "personality": "controlled and tactical",
            "appearance": "dark braided hair; narrow tired face; adult woman",
            "relationships": "sister of Lyra Nale and Maren Keth",
            "current_state": "planning a rescue at the farmhouse table",
            "image_prompt": "",
            "visual_profile": {
                "base_visual_description": "dark braided hair; narrow tired face; lean adult sister",
                "face_description": "narrow tired face",
                "hair": "dark braided hair",
                "body_build": "lean build",
                "age_marker": "adult woman",
                "default_outfit": "red cloak and worn farm clothes",
                "distinctive_marks": "",
                "color_palette": "red cloak, dark hair, worn brown leather",
            },
            "major": True,
            "confidence": 0.92,
            "reason": "Introduced as one of the three central sisters with action and visual detail.",
        },
        {
            "name": "Lyra Nale",
            "role": "middle sister",
            "personality": "watchful and practical",
            "appearance": "cropped brown hair; compact build; adult woman",
            "relationships": "sister of Elara Voss and Maren Keth",
            "current_state": "carrying the bronze compass",
            "image_prompt": "",
            "visual_profile": {
                "base_visual_description": "cropped brown hair; compact adult build; sharp watchful face",
                "face_description": "sharp watchful face",
                "hair": "cropped brown hair",
                "body_build": "compact build",
                "age_marker": "adult woman",
                "default_outfit": "patched blue wool coat and gloves",
                "distinctive_marks": "",
                "color_palette": "blue wool, brown hair, dark gloves",
            },
            "major": True,
            "confidence": 0.91,
            "reason": "Introduced as one of the central sisters and shown carrying the compass.",
        },
        {
            "name": "Maren Keth",
            "role": "youngest sister",
            "personality": "blunt and protective",
            "appearance": "fair hair; broad shoulders; pale scar at her jaw",
            "relationships": "sister of Elara Voss and Lyra Nale",
            "current_state": "watching the farmhouse door",
            "image_prompt": "",
            "visual_profile": {
                "base_visual_description": "fair hair; broad shoulders; adult sister",
                "face_description": "pale scar at her jaw",
                "hair": "fair hair",
                "body_build": "broad shoulders",
                "age_marker": "adult woman",
                "default_outfit": "plain gray tunic and heavy boots",
                "distinctive_marks": "pale scar at her jaw",
                "color_palette": "gray cloth, fair hair, scuffed boots",
            },
            "major": True,
            "confidence": 0.9,
            "reason": "Introduced as one of the central sisters with distinct visual traits.",
        },
    ]
    result = upsert_detected_characters(
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id,
        scene_text=INTRO_TEXT,
        detected=detected,
        existing={},
    )
    if len(result["created"]) != 3:
        raise RuntimeError(f"Expected three auto-created characters, got {result}")
    with db_session() as db:
        rows = db.execute(
            """
            SELECT c.name, c.id
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ?
            """,
            (session_id,),
        ).fetchall()
    return {row["name"]: row["id"] for row in rows}


def merge_payload(session_id: str, scene_id: str, version_id: str, payload: dict) -> list[str]:
    run_id = create_run(session_id, scene_id, version_id)
    warnings = merge_state_payload(
        run_id=run_id,
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id,
        payload=payload,
    )
    update_run(
        run_id,
        status="completed",
        completed_at=utc_now(),
        raw_response=json.dumps(payload, ensure_ascii=False),
        warnings_json=json.dumps(warnings, ensure_ascii=False),
    )
    return warnings


def run_state_sequence(session_id: str) -> tuple[list[str], dict[str, str]]:
    scene1_id, version1_id = insert_scene(session_id, INTRO_TEXT, "Introduce sisters for consistency smoke.")
    character_ids = auto_create_test_characters(session_id, scene1_id, version1_id)
    warnings: list[str] = []
    warnings.extend(
        merge_payload(
            session_id,
            scene1_id,
            version1_id,
            {
                "character_updates": [
                    {
                        "character_name": "Elara Voss",
                        "state_type": "appearance",
                        "key": "current_outfit",
                        "value": "wearing a red cloak at the farmhouse table",
                        "action": "replace",
                        "confidence": 0.94,
                        "confidence_reason": "The scene directly states Elara wears it.",
                    },
                    {
                        "character_name": "Lyra Nale",
                        "state_type": "location",
                        "key": "current_location",
                        "value": "at the farmhouse table",
                        "action": "replace",
                        "confidence": 0.9,
                    },
                    {
                        "character_name": "Maren Keth",
                        "state_type": "appearance",
                        "key": "injury_left_forearm",
                        "value": "fresh cut on left forearm",
                        "action": "update",
                        "confidence": 0.95,
                    },
                ],
                "object_updates": [
                    {
                        "name": "bronze compass",
                        "object_key": "bronze_compass_consistency",
                        "state_type": "ownership",
                        "value": "carried in Lyra's gloved hand",
                        "owner_character_name": "Lyra Nale",
                        "confidence": 0.96,
                    }
                ],
                "warnings": [],
            },
        )
    )

    scene2_text = (
        "Lyra Nale gave the bronze compass to Elara Voss before Maren Keth wrapped the fresh cut on her left forearm "
        "in a clean bandage."
    )
    scene2_id, version2_id = insert_scene(session_id, scene2_text, "Transfer object and bandage wound.")
    warnings.extend(
        merge_payload(
            session_id,
            scene2_id,
            version2_id,
            {
                "character_updates": [
                    {
                        "character_name": "Maren Keth",
                        "state_type": "appearance",
                        "key": "injury_left_forearm",
                        "value": "left forearm bandaged after the cut",
                        "action": "update",
                        "confidence": 0.93,
                    }
                ],
                "object_updates": [
                    {
                        "name": "bronze compass",
                        "object_key": "bronze_compass_consistency",
                        "state_type": "ownership",
                        "value": "transferred from Lyra to Elara",
                        "owner_character_name": "Elara Voss",
                        "confidence": 0.96,
                    }
                ],
                "warnings": [],
            },
        )
    )

    scene3_text = (
        "Weeks later, Maren Keth's left forearm had healed into a thin scar. Lyra Nale had cut her hair short at the jaw "
        "and traded the blue coat for a patched gray one."
    )
    scene3_id, version3_id = insert_scene(session_id, scene3_text, "Advance healing and fashion changes.")
    warnings.extend(
        merge_payload(
            session_id,
            scene3_id,
            version3_id,
            {
                "character_updates": [
                    {
                        "character_name": "Maren Keth",
                        "state_type": "appearance",
                        "key": "injury_left_forearm",
                        "value": "left forearm healed into a thin scar",
                        "action": "update",
                        "confidence": 0.95,
                    },
                    {
                        "character_name": "Lyra Nale",
                        "state_type": "appearance",
                        "key": "hair_style",
                        "value": "hair cut short at the jaw",
                        "action": "replace",
                        "confidence": 0.92,
                    },
                    {
                        "character_name": "Lyra Nale",
                        "state_type": "appearance",
                        "key": "outfit_coat",
                        "value": "wearing a patched gray coat",
                        "action": "replace",
                        "confidence": 0.91,
                    },
                ],
                "warnings": [],
            },
        )
    )
    return warnings, character_ids


def assert_true(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def query_one(sql: str, args: tuple = ()):
    with db_session() as db:
        return db.execute(sql, args).fetchone()


def query_all(sql: str, args: tuple = ()):
    with db_session() as db:
        return db.execute(sql, args).fetchall()


def run() -> int:
    init_db()
    session_id = create_session()
    warnings, _character_ids = run_state_sequence(session_id)
    failures: list[str] = []

    profile_rows = query_all(
        """
        SELECT c.name, c.appearance, c.image_prompt, cvp.base_visual_description,
               cvp.hair, cvp.default_outfit, cvp.auto_created_confidence,
               cvp.source_scene_id, cvp.source_version_id
        FROM session_characters sc
        JOIN characters c ON c.id = sc.character_id
        JOIN character_visual_profiles cvp ON cvp.character_id = c.id
        WHERE sc.session_id = ?
        ORDER BY c.name
        """,
        (session_id,),
    )
    assert_true(len(profile_rows) == 3, f"Expected 3 visual profiles, found {len(profile_rows)}.", failures)
    for row in profile_rows:
        assert_true(row["base_visual_description"], f"{row['name']} missing base visual description.", failures)
        assert_true(row["auto_created_confidence"] >= 0.9, f"{row['name']} confidence not preserved.", failures)
        assert_true(row["source_scene_id"], f"{row['name']} missing visual source scene.", failures)
        assert_true("said" not in (row["image_prompt"] or "").lower(), f"{row['name']} image prompt leaked prose.", failures)

    elara_outfit = query_one(
        """
        SELECT value, confidence, archived
        FROM character_live_state
        WHERE session_id = ? AND character_name = 'Elara Voss' AND key = 'current_outfit'
        ORDER BY archived ASC, updated_at DESC
        LIMIT 1
        """,
        (session_id,),
    )
    assert_true(bool(elara_outfit), "Elara current outfit missing.", failures)
    if elara_outfit:
        assert_true("red cloak" in elara_outfit["value"].lower(), "Elara current outfit did not persist.", failures)
        assert_true(elara_outfit["confidence"] >= 0.9, "Explicit outfit confidence too low.", failures)

    maren_injury_rows = query_all(
        """
        SELECT key, value, confidence, archived
        FROM character_live_state
        WHERE session_id = ? AND character_name = 'Maren Keth' AND key = 'injury_left_forearm'
        ORDER BY archived ASC, updated_at DESC
        """,
        (session_id,),
    )
    active_maren = [row for row in maren_injury_rows if not row["archived"]]
    assert_true(len(active_maren) == 1, f"Expected one active Maren injury state, found {len(active_maren)}.", failures)
    if active_maren:
        assert_true("scar" in active_maren[0]["value"].lower(), "Maren injury did not transition to scar.", failures)
        assert_true(active_maren[0]["confidence"] >= 0.9, "Explicit healed-scar confidence too low.", failures)

    active_objects = query_all(
        """
        SELECT owner_character_name, value, archived
        FROM object_state
        WHERE session_id = ? AND object_key = 'bronze_compass_consistency'
        ORDER BY archived ASC, updated_at DESC
        """,
        (session_id,),
    )
    active_owner_rows = [row for row in active_objects if not row["archived"]]
    archived_owner_rows = [row for row in active_objects if row["archived"]]
    assert_true(len(active_owner_rows) == 1, f"Expected one active compass owner, found {len(active_owner_rows)}.", failures)
    if active_owner_rows:
        assert_true(active_owner_rows[0]["owner_character_name"] == "Elara Voss", "Compass owner did not transfer to Elara.", failures)
    assert_true(
        any(row["owner_character_name"] == "Lyra Nale" for row in archived_owner_rows),
        "Previous Lyra compass ownership was not archived.",
        failures,
    )

    conflicts = detect_story_state_conflicts(session_id)
    assert_true(not conflicts, f"Unexpected conflicts after safe merge: {[item.message for item in conflicts]}", failures)

    scene_text = "Elara Voss pauses at the camp edge with the bronze compass while Maren Keth checks the scar on her left forearm."
    prompt_data = fallback_prompt(
        scene_text=scene_text,
        negative_prompt="",
        visual_context=load_visual_context(session_id, scene_text),
        workflow_notes="rough grounded dark fantasy realism, imperfect camera quality, slight grain",
    )
    prompt = prompt_data["prompt"].lower()
    assert_true("elara" in prompt and "red cloak" in prompt, "Image prompt missed Elara base/current visual state.", failures)
    assert_true("bronze compass" in prompt, "Image prompt missed current object ownership.", failures)
    assert_true("scar" in prompt, "Image prompt missed Maren healed injury state.", failures)
    assert_true("fresh cut" not in prompt, "Image prompt leaked stale fresh wound state.", failures)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# Character State Consistency Report",
                "",
                "## Visual extraction changes",
                "- Auto-created characters now get concise visual profiles with stable identity fields: base description, face, hair, build, adult marker, default outfit, distinctive marks, palette, source scene/version, and auto-created confidence.",
                "- Auto-created image prompts are built from compact visual traits instead of nearby prose paragraphs.",
                "- Manual/base visual fields are not overwritten by live state updates.",
                "",
                "## Confidence calibration",
                "- The extractor prompt now uses explicit bands: direct statements 0.85-0.98, repeated implication 0.70-0.85, weak implication 0.45-0.65, guesses 0.25-0.45 or skipped.",
                "- Merge defaults now avoid zero/0.50 drift when a structured update omits confidence, while still skipping low-confidence changes.",
                "",
                "## Object/location accuracy",
                "- Object ownership/held-by updates normalize to ownership.",
                "- Ownership transfers archive the previous active owner and create a new active owner row.",
                "- Current outfit/location keys replace stale current-state rows, and injury keys canonicalize by body part.",
                "",
                "## Conflict detection",
                "- Existing owner/location/outfit conflict checks remain active.",
                "- Injury conflict detection now flags obvious fresh/open/bandaged versus healed/scar contradictions on the same body area.",
                "",
                "## Image prompt continuity",
                "- Image prompts combine character name, base visual profile, LoRA trigger text if configured, live outfit, live injury/healing, dirt/weather effects, carried objects, and current scene location.",
                "- Stale archived visual state is excluded from prompt context.",
                f"- Character continuity used: {prompt_data.get('continuity_used', {})}",
                "",
                "## Controlled smoke",
                f"- Session id: {session_id}",
                f"- Auto-created profiles: {len(profile_rows)}",
                f"- State merge warnings: {warnings or 'none'}",
                f"- Conflicts after safe merge: {len(conflicts)}",
                f"- Result: {'PASS' if not failures else 'FAIL'}",
                "",
                "## Tests run",
                "- scripts\\character_state_consistency_smoke_test.bat",
                "",
                "## Failures",
                *(f"- {failure}" for failure in failures),
                "",
                "## Remaining limitations",
                "- This smoke test validates deterministic extraction/merge behavior without spending a live LM Studio extraction pass.",
                "- Truly ambiguous pronoun ownership still depends on the local model returning a cautious, high-evidence JSON update.",
                "- Long-term visual consistency still depends on ComfyUI workflow/model behavior and any LoRA/reference setup configured there.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    if failures:
        print("\n".join(failures))
        print(f"Report written: {REPORT_PATH}")
        return 1
    print("Character state consistency smoke passed.")
    print(f"Report written: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
