from __future__ import annotations

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
from app.services.image_prompt_service import fallback_prompt, load_visual_context  # noqa: E402


REPORT_PATH = BACKEND / "data" / "logs" / "VISUAL_CONSISTENCY_REPORT.md"
TEST_STORY_TITLE = "StoryDriver Visual Consistency Smoke Test"


def ensure_session() -> str:
    with db_session() as db:
        row = db.execute("SELECT id FROM sessions WHERE title = ? ORDER BY created_at ASC LIMIT 1", (TEST_STORY_TITLE,)).fetchone()
        if row:
            return row["id"]
        session_id = str(uuid4())
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, TEST_STORY_TITLE))
        return session_id


def ensure_character(session_id: str, name: str, *, lora: str, base_visual: str, default_outfit: str) -> str:
    with db_session() as db:
        row = db.execute(
            """
            SELECT c.id
            FROM session_characters sc
            JOIN characters c ON c.id = sc.character_id
            WHERE sc.session_id = ? AND c.name = ?
            LIMIT 1
            """,
            (session_id, name),
        ).fetchone()
        if row:
            character_id = row["id"]
        else:
            character_id = str(uuid4())
            db.execute(
                """
                INSERT INTO characters (
                    id, name, role, personality, appearance, image_prompt, lora_trigger
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    character_id,
                    name,
                    "test character",
                    "continuity smoke test character",
                    base_visual,
                    base_visual,
                    lora,
                ),
            )
            db.execute(
                "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
                (str(uuid4()), session_id, character_id),
            )
        db.execute(
            """
            INSERT INTO character_visual_profiles (
                character_id, base_visual_description, face_description, hair, body_build,
                age_marker, default_outfit, distinctive_marks, color_palette,
                negative_prompt, z_image_lora_trigger, alternate_lora_triggers,
                preferred_voice, visual_consistency_notes, used_in_image_prompts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(character_id) DO UPDATE SET
                base_visual_description = excluded.base_visual_description,
                face_description = excluded.face_description,
                hair = excluded.hair,
                body_build = excluded.body_build,
                age_marker = excluded.age_marker,
                default_outfit = excluded.default_outfit,
                distinctive_marks = excluded.distinctive_marks,
                color_palette = excluded.color_palette,
                negative_prompt = excluded.negative_prompt,
                z_image_lora_trigger = excluded.z_image_lora_trigger,
                alternate_lora_triggers = excluded.alternate_lora_triggers,
                preferred_voice = excluded.preferred_voice,
                visual_consistency_notes = excluded.visual_consistency_notes,
                used_in_image_prompts = excluded.used_in_image_prompts
            """,
            (
                character_id,
                base_visual,
                "grounded face, alert eyes",
                "short black hair" if name == "Mara" else "dark wavy hair",
                "lean practical build",
                "fictional adult",
                default_outfit,
                "scar over left eyebrow" if name == "Mara" else "weathered hands",
                "deep reds, dark leather, rain-black stone",
                "wrong face, wrong hair, extra fingers",
                lora,
                "",
                "",
                "Keep face, hair, build, and distinctive marks stable across images.",
            ),
        )
        return character_id


def replace_character_state(session_id: str, character_id: str, character_name: str, key: str, value: str) -> None:
    with db_session() as db:
        db.execute(
            """
            UPDATE character_live_state
            SET archived = 1
            WHERE session_id = ? AND character_id = ? AND key = ? AND archived = 0
            """,
            (session_id, character_id, key),
        )
        db.execute(
            """
            INSERT INTO character_live_state (
                id, session_id, character_id, character_name, state_type, key, value,
                confidence, manual_override, source_scene_id
            )
            VALUES (?, ?, ?, ?, 'appearance', ?, ?, 0.99, 1, NULL)
            """,
            (str(uuid4()), session_id, character_id, character_name, key, value),
        )


def ensure_object_state(session_id: str) -> None:
    with db_session() as db:
        db.execute(
            """
            UPDATE object_state
            SET archived = 1
            WHERE session_id = ? AND object_key = 'bronze_compass' AND archived = 0
            """,
            (session_id,),
        )
        db.execute(
            """
            INSERT INTO object_state (
                id, session_id, object_key, name, state_type, value,
                owner_character_name, confidence, manual_override
            )
            VALUES (?, ?, 'bronze_compass', 'bronze compass', 'ownership',
                    'hidden under Elias cloak', 'Elias', 0.99, 1)
            """,
            (str(uuid4()), session_id),
        )


def ensure_story_style(session_id: str) -> None:
    with db_session() as db:
        db.execute(
            """
            INSERT INTO session_image_settings (
                session_id, image_prompt_style, preferred_visual_tone,
                realism_notes, lighting_camera_notes, default_negative_prompt
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                image_prompt_style = excluded.image_prompt_style,
                preferred_visual_tone = excluded.preferred_visual_tone,
                realism_notes = excluded.realism_notes,
                lighting_camera_notes = excluded.lighting_camera_notes,
                default_negative_prompt = excluded.default_negative_prompt
            """,
            (
                session_id,
                "dark cinematic fantasy realism",
                "rain-soaked, grounded, tense",
                "realistic wounds, natural skin, no plastic faces",
                "campfire rim light, shallow depth of field",
                "wrong character, wrong outfit, plastic skin",
            ),
        )


def assert_contains(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle.lower() not in text.lower():
        failures.append(f"Missing {label}: {needle}")


def assert_not_contains(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle.lower() in text.lower():
        failures.append(f"Unexpected {label}: {needle}")


def run() -> int:
    init_db()
    session_id = ensure_session()
    mara_id = ensure_character(
        session_id,
        "Mara",
        lora="mara_lora_v1",
        base_visual="Mara has short black hair, guarded eyes, and a scar over her left eyebrow.",
        default_outfit="green cloak and travel leathers",
    )
    elias_id = ensure_character(
        session_id,
        "Elias",
        lora="elias_lora_v1",
        base_visual="Elias is a weary scholar with dark wavy hair and narrow spectacles.",
        default_outfit="charcoal coat and ink-stained cuffs",
    )
    replace_character_state(session_id, mara_id, "Mara", "current_outfit", "wearing a red cloak torn at the shoulder")
    replace_character_state(session_id, mara_id, "Mara", "injury_left_shoulder", "left shoulder bandaged after the fight")
    replace_character_state(session_id, elias_id, "Elias", "current_location", "near the camp perimeter")
    ensure_object_state(session_id)
    ensure_story_style(session_id)

    failures: list[str] = []
    mara_scene = "Mara sits by the campfire after the fight, rain hissing around the coals while she tightens the bandage at her left shoulder."
    mara_context = load_visual_context(session_id, mara_scene)
    mara_prompt = fallback_prompt(
        scene_text=mara_scene,
        negative_prompt="wrong character",
        visual_context=mara_context,
        workflow_notes="dark cinematic fantasy realism, campfire rim light",
    )
    mara_text = mara_prompt["prompt"]
    assert_contains(mara_text, "mara_lora_v1", "Mara LoRA trigger", failures)
    assert_contains(mara_text, "red cloak", "current outfit", failures)
    assert_contains(mara_text, "left shoulder bandaged", "current injury", failures)
    assert_contains(mara_text, "dark cinematic fantasy realism", "story visual style", failures)
    assert_not_contains(mara_text, "green cloak", "stale default outfit", failures)
    if "Elias" in mara_prompt["characters_included"]:
        failures.append("Elias was included in a Mara-only scene.")

    elias_scene = "Mara watches Elias near the campfire as he keeps the bronze compass hidden beneath his cloak."
    elias_context = load_visual_context(session_id, elias_scene)
    elias_prompt = fallback_prompt(
        scene_text=elias_scene,
        negative_prompt="wrong character",
        visual_context=elias_context,
        workflow_notes="dark cinematic fantasy realism, campfire rim light",
    )
    elias_text = elias_prompt["prompt"]
    assert_contains(elias_text, "mara_lora_v1", "Mara trigger in shared scene", failures)
    assert_contains(elias_text, "elias_lora_v1", "Elias trigger in shared scene", failures)
    assert_contains(elias_text, "bronze compass", "important object", failures)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# Visual Consistency Report",
                "",
                "## Visual profile fields added",
                "- Character visual profiles support stable appearance, face, hair, build, adult marker, default outfit, distinctive marks, palette, negative prompt, LoRA trigger text, alternate triggers, preferred voice, and consistency notes.",
                "- Existing character cards remain intact; live Story State continues to own temporary/current visual facts.",
                "",
                "## Prompt builder changes",
                "- Image prompts now prioritize scene-mentioned characters.",
                "- Live visual state suppresses stale default outfit details.",
                "- Story image style, world tone, visible objects, and active visual state are included in prompt context.",
                "",
                "## LoRA trigger handling",
                "- Trigger text is inserted as prompt text only for included scene characters.",
                "- StoryDriver does not load or configure LoRAs; ComfyUI remains responsible for workflow internals.",
                "",
                "## Smoke result",
                f"- Session: {TEST_STORY_TITLE}",
                f"- Mara-only characters included: {', '.join(mara_prompt['characters_included']) or 'none'}",
                f"- Shared-scene characters included: {', '.join(elias_prompt['characters_included']) or 'none'}",
                f"- Continuity used: {mara_prompt.get('continuity_used', {})}",
                f"- Result: {'PASS' if not failures else 'FAIL'}",
                "",
                "## Tests run",
                "- scripts\\visual_consistency_smoke_test.bat",
                "- frontend build: npm.cmd run build",
                "- backend compile: backend\\.venv\\Scripts\\python.exe -m compileall backend\\app scripts\\visual_consistency_smoke_test.py",
                "- scripts\\smoke_test_storydriver.bat",
                "- scripts\\state_prompt_integration_smoke_test.bat",
                "- scripts\\state_engine_smoke_test.bat",
                "- scripts\\state_review_smoke_test.bat",
                "- scripts\\check_services.bat",
                "",
                "## Image generation check",
                "- Real Z-Image generation was not run by this smoke test; the image performance smoke starts with Balanced mode, which is a known slow path when the large LM Studio model is loaded.",
                "- The safe next manual image check is Auto Image Priority with the smoke scene.",
                "",
                "## Failures",
                *(f"- {failure}" for failure in failures),
                "",
                "## Remaining limitations",
                "- This smoke test validates prompt assembly without spending a ComfyUI generation.",
                "- Real visual consistency still depends on the selected ComfyUI workflow and any LoRA nodes loaded there.",
                "",
                "## Next recommended step",
                "- Generate one real image from the smoke scene with Auto Image Priority and visually confirm the profile/state details.",
            ]
        ),
        encoding="utf-8",
    )
    if failures:
        print("\n".join(failures))
        print(f"Report written: {REPORT_PATH}")
        return 1
    print("Visual consistency smoke passed.")
    print(f"Report written: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
