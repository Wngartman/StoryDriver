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
from app.services.image_prompt_service import (  # noqa: E402
    fallback_prompt,
    load_visual_context,
    resolve_image_prompt_style,
    select_visual_beat,
)
from app.services.image_workflows import scan_workflows  # noqa: E402
from app.services.scene_image_prompts import (  # noqa: E402
    load_scene_image_prompt_settings,
    save_scene_image_prompt_settings,
)


REPORT_PATH = BACKEND / "data" / "logs" / "IMAGE_PROMPT_QUALITY_REPORT.md"
CLEANUP_REPORT_PATH = BACKEND / "data" / "logs" / "IMAGE_PROMPT_CLEANUP_REPORT.md"
TEST_STORY_TITLE = "StoryDriver Image Prompt Quality Test"


def ensure_session() -> str:
    with db_session() as db:
        row = db.execute("SELECT id FROM sessions WHERE title = ? ORDER BY created_at ASC LIMIT 1", (TEST_STORY_TITLE,)).fetchone()
        if row:
            return row["id"]
        session_id = str(uuid4())
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, TEST_STORY_TITLE))
        return session_id


def ensure_character(session_id: str, name: str, *, role: str, base_visual: str, outfit: str, lora: str) -> str:
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
                INSERT INTO characters (id, name, role, personality, appearance, image_prompt, lora_trigger)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (character_id, name, role, "prompt-quality smoke character", base_visual, base_visual, lora),
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
                "grounded, tired face; no beauty lighting",
                "short black hair" if name == "Mara" else "dark unkempt hair",
                "lean practical build" if name != "Rowan" else "broad tired build",
                "fictional adult",
                outfit,
                "scar over left eyebrow" if name == "Mara" else "weathered hands",
                "muddy reds, charcoal, wet leather",
                "wrong face, plastic skin, clean costume",
                lora,
                "",
                "",
                "Keep stable face, hair, build, and distinctive marks.",
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


def ensure_world_and_object_state(session_id: str) -> None:
    with db_session() as db:
        existing = db.execute("SELECT id FROM world_notes WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
        if existing:
            db.execute(
                """
                UPDATE world_notes
                SET setting = ?, tone = ?, locations = ?
                WHERE session_id = ?
                """,
                (
                    "stormy dark fantasy frontier; ruined chapel; wet bandit roads",
                    "grounded gritty tone, low magic, tense campfire quiet",
                    "camp, muddy road, ruined chapel",
                    session_id,
                ),
            )
        else:
            db.execute(
                """
                INSERT INTO world_notes (id, session_id, setting, tone, locations)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    session_id,
                    "stormy dark fantasy frontier; ruined chapel; wet bandit roads",
                    "grounded gritty tone, low magic, tense campfire quiet",
                    "camp, muddy road, ruined chapel",
                ),
            )
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
                    'hidden under Elias dark coat', 'Elias', 0.99, 1)
            """,
            (str(uuid4()), session_id),
        )


def ensure_test_scene(session_id: str) -> tuple[str, str]:
    with db_session() as db:
        row = db.execute(
            """
            SELECT s.id AS scene_id, v.id AS version_id
            FROM scenes s
            JOIN scene_versions v ON v.scene_id = s.id
            WHERE s.session_id = ? AND s.director_note = 'Image prompt quality workflow flexibility test'
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row:
            return row["scene_id"], row["version_id"]
        scene_id = str(uuid4())
        version_id = str(uuid4())
        text = "Mara studies the ruined chapel from the muddy road, red cloak pulled tight in the rain."
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
            VALUES (?, ?, 'Image prompt quality workflow flexibility test', ?, 'continue')
            """,
            (scene_id, session_id, text),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, 'Image prompt quality workflow flexibility test', ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, text),
        )
        return scene_id, version_id


def ensure_blank_story_image_style(session_id: str) -> None:
    with db_session() as db:
        db.execute(
            """
            INSERT INTO session_image_settings (
                session_id, image_prompt_style, preferred_visual_tone,
                realism_notes, lighting_camera_notes, default_negative_prompt
            )
            VALUES (?, '', '', '', '', '')
            ON CONFLICT(session_id) DO UPDATE SET
                image_prompt_style = '',
                preferred_visual_tone = '',
                realism_notes = '',
                lighting_camera_notes = '',
                default_negative_prompt = ''
            """,
            (session_id,),
        )


def assert_contains(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle.lower() not in text.lower():
        failures.append(f"Missing {label}: {needle}")


def assert_not_contains(text: str, needle: str, label: str, failures: list[str]) -> None:
    if needle.lower() in text.lower():
        failures.append(f"Unexpected {label}: {needle}")


def build_prompt(session_id: str, scene: str, style_info: dict, workflow_notes: str | None = None) -> dict:
    context = load_visual_context(session_id, scene)
    return fallback_prompt(
        scene_text=scene,
        negative_prompt="",
        visual_context=context,
        workflow_notes=workflow_notes if workflow_notes is not None else style_info["text"],
        style_used=style_info["label"],
        style_negative_prompt=style_info["negative_prompt"],
    )


def run() -> int:
    init_db()
    session_id = ensure_session()
    mara_id = ensure_character(
        session_id,
        "Mara",
        role="thief/scout",
        base_visual="Mara has short black hair, guarded eyes, and a scar over her left eyebrow.",
        outfit="green cloak and travel leathers",
        lora="mara_lora_v1",
    )
    elias_id = ensure_character(
        session_id,
        "Elias",
        role="nervous scholar",
        base_visual="Elias has dark unkempt hair, silver spectacles, and a nervous narrow face.",
        outfit="dark coat with ink-stained cuffs",
        lora="elias_lora_v1",
    )
    ensure_character(
        session_id,
        "Rowan",
        role="tired mercenary",
        base_visual="Rowan has a broad tired frame, stubble, and an old sword belt.",
        outfit="heavy weathered cloak",
        lora="rowan_lora_v1",
    )
    replace_character_state(session_id, mara_id, "Mara", "current_outfit", "wearing a red cloak torn at the hem")
    replace_character_state(session_id, mara_id, "Mara", "injury_left_shoulder", "left shoulder bandaged after the fight")
    replace_character_state(session_id, mara_id, "Mara", "current_surface_state", "mud on boots, rain-wet cloak, tired face")
    replace_character_state(session_id, elias_id, "Elias", "current_object", "clutching a bronze compass half-hidden in his coat")
    ensure_world_and_object_state(session_id)
    ensure_blank_story_image_style(session_id)

    style_info = resolve_image_prompt_style("", "", "", "")
    failures: list[str] = []
    examples: list[tuple[str, dict]] = []
    workflow_flex_result = "SKIPPED - no ready workflow found"

    campfire_scene = (
        "Mara, Elias, and Rowan sit close around a low campfire, using a dirty map stone to plan the ruined chapel route. "
        "Rain taps through the branches. Elias keeps one hand inside his coat, hiding the bronze compass, while Rowan says they will wait until dawn."
    )
    campfire = build_prompt(session_id, campfire_scene, style_info)
    examples.append(("campfire planning", campfire))
    assert_contains(campfire["visual_beat"], "campfire", "planning visual beat", failures)
    assert_contains(campfire["prompt"], "campfire", "campfire prompt", failures)
    assert_contains(campfire["prompt"], "bronze compass", "hidden object in planning prompt", failures)
    assert_not_contains(campfire["prompt"], "charging", "invented action", failures)

    wound_scene = (
        "Mara sits under the canvas edge after the fight, red cloak torn at the hem, tightening the bandage on her left shoulder "
        "while rainwater and mud streak her boots."
    )
    wound = build_prompt(session_id, wound_scene, style_info)
    examples.append(("post-fight wound", wound))
    assert_contains(wound["prompt"], "red cloak", "current outfit", failures)
    assert_contains(wound["prompt"], "left shoulder bandaged", "current injury", failures)
    assert_contains(wound["prompt"], "mud", "grit/dirty state", failures)
    assert_not_contains(wound["prompt"], "green cloak", "stale outfit", failures)

    contaminated_notes = "\n".join(
        [
            "Preferred StoryDriver workflow: Lonecat ZIT NSFW 8.0.1",
            "Positive prompt node 1342.positive",
            "Negative prompt node 1317.negative",
            "Seed node 1307.seed",
            "Detected automatically by StoryDriver.",
            wound_scene,
            wound_scene,
        ]
    )
    contaminated = build_prompt(session_id, wound_scene, style_info, contaminated_notes)
    examples.append(("sanitized contaminated workflow notes", contaminated))
    for needle in [
        "Preferred StoryDriver workflow",
        "Positive prompt node",
        "Negative prompt node",
        "Seed node",
        "Detected automatically",
        "1342.positive",
        "1317.negative",
    ]:
        assert_not_contains(contaminated["prompt"], needle, "workflow/debug prompt leakage", failures)
    if contaminated["prompt"].lower().count("mara sits under the canvas edge") > 1:
        failures.append("Contaminated prompt repeated a long scene fragment.")

    quiet_scene = "Mara and Elias speak quietly in the doorway of the ruined chapel, neither drawing steel, both listening to rain in the broken nave."
    quiet = build_prompt(session_id, quiet_scene, style_info)
    examples.append(("quiet dialogue", quiet))
    assert_contains(quiet["visual_beat"], "speak quietly", "quiet dialogue beat", failures)
    assert_not_contains(quiet["prompt"], "battle", "invented combat", failures)

    travel_scene = "Mara walks alone down the muddy road toward camp, her red cloak soaked by cold rain and the chapel fading behind her."
    travel = build_prompt(session_id, travel_scene, style_info)
    examples.append(("travel", travel))
    assert_contains(travel["prompt"], "muddy road", "travel location", failures)
    assert_contains(travel["prompt"], "rain", "weather", failures)

    mara_only_scene = "Mara crouches beside the guttering fire, checking the bandage on her left shoulder in silence."
    mara_only = build_prompt(session_id, mara_only_scene, style_info)
    examples.append(("one-character", mara_only))
    if "Elias" in mara_only.get("characters_included", []):
        failures.append("Elias was included in a Mara-only image prompt.")
    assert_not_contains(mara_only["prompt"], "Elias", "absent character name in one-character prompt", failures)
    assert_not_contains(mara_only["prompt"], "elias_lora_v1", "absent character LoRA trigger", failures)

    negative = wound["negative_prompt"]
    for needle in ["plastic skin", "glamour lighting", "extra fingers", "modern objects"]:
        assert_contains(negative, needle, f"strong negative prompt term {needle}", failures)
    for needle in ["slight grain", "practical camera angle", "rough"]:
        assert_contains(wound["prompt"], needle, f"gritty style term {needle}", failures)
    for needle in ["node ", "positive:", "negative:", "Preferred StoryDriver workflow", "workflow notes"]:
        assert_not_contains(wound["prompt"], needle, f"mapping/debug leakage {needle}", failures)
    if wound.get("style_used") != "Gritty Dark Fantasy Realism":
        failures.append(f"Expected default gritty style, got {wound.get('style_used')}")

    ready_workflows = [workflow for workflow in scan_workflows() if workflow.status == "ready"]
    if ready_workflows:
        scene_id, version_id = ensure_test_scene(session_id)
        workflow_id = ready_workflows[0].id
        saved = save_scene_image_prompt_settings(session_id, scene_id, version_id, workflow_id)
        loaded = load_scene_image_prompt_settings(session_id, scene_id, version_id)
        if saved.get("selected_workflow_id") != workflow_id or loaded.get("selected_workflow_id") != workflow_id:
            failures.append("Scene-level workflow override did not persist.")
        cleared = save_scene_image_prompt_settings(session_id, scene_id, version_id, None)
        if cleared.get("selected_workflow_id") is not None:
            failures.append("Scene-level workflow override did not clear back to story default.")
        workflow_flex_result = f"PASS - saved and cleared scene override for {ready_workflows[0].name}"

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    before_shape = "cinematic scene illustration, broad scene excerpt, character bits, generic negative prompt"
    after_shape = wound["prompt"]
    lines = [
        "# Image Prompt Quality Report",
        "",
        "## Prompt changes",
        "- Added default gritty dark fantasy realism style when a story has no custom visual direction.",
        "- Fallback prompt generation now chooses a concrete visual beat from the selected scene instead of summarizing the whole scene.",
        "- Prompt language now favors rough grounded stills, imperfect camera quality, slight grain, practical camera angles, dirt, sweat, mud, realistic wounds, and natural light.",
        "- Negative prompts now discourage plastic skin, glamour lighting, sterile studio look, overly clean clothing, generic fantasy poster composition, extra limbs/fingers, anime/cartoon drift, and accidental modern objects.",
        "",
        "## Style presets added",
        "- Gritty Dark Fantasy Realism",
        "- Somber Fantasy LoRA Style",
        "- Rough Medieval Documentary",
        "- Cleaner Realistic",
        "- Custom",
        "",
        "## Test scenes",
        "- Campfire planning scene",
        "- Post-fight wound scene",
        "- Quiet dialogue scene",
        "- Travel scene",
        "- Multi-character object/secret scene",
        "- One-character scene",
        f"- Workflow flexibility: {workflow_flex_result}",
        "",
        "## Before / After Prompt Shape",
        "",
        f"- Before: {before_shape}",
        f"- After: {after_shape}",
        "",
        "## Example results",
    ]
    for label, prompt_data in examples:
        lines.extend(
            [
                "",
                f"### {label}",
                f"- Visual beat: {prompt_data.get('visual_beat')}",
                f"- Characters included: {', '.join(prompt_data.get('characters_included') or []) or 'none'}",
                f"- Style used: {prompt_data.get('style_used')}",
                f"- Prompt excerpt: {prompt_data.get('prompt', '')[:700]}",
                f"- Negative excerpt: {prompt_data.get('negative_prompt', '')[:450]}",
                f"- Warnings: {', '.join(prompt_data.get('quality_warnings') or []) or 'none'}",
            ]
        )
    lines.extend(
        [
            "",
            "## Assertions",
            f"- Result: {'PASS' if not failures else 'FAIL'}",
            *(f"- {failure}" for failure in failures),
            "",
            "## Remaining limitations",
            "- This test validates prompt assembly and continuity without spending a real ComfyUI generation.",
            "- LM Studio may still occasionally return a weak prompt; deterministic fallback remains grounded and continuity-safe.",
            "- Real image fidelity still depends on the selected ComfyUI workflow and any loaded LoRA/model settings.",
        ]
    )
    report_text = "\n".join(lines) + "\n"
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    cleanup_lines = [
        "# Image Prompt Cleanup Report",
        "",
        "## Prompt contamination fix",
        "- Final positive prompts are rebuilt from sanitized style fragments, selected visual beat, character visual profiles, live visual state, relevant objects, and world/location bits.",
        "- Raw workflow notes, node mappings, StoryDriver debug labels, JSON/control text, and long repeated scene excerpts are stripped before prompt assembly.",
        "- LM Studio can suggest a visual beat, but StoryDriver no longer trusts a raw model-returned positive prompt as the final prompt.",
        "",
        "## Character visual context changes",
        "- Character prompt fragments prioritize stable identity plus current outfit, injuries, dirt/wetness, and carried objects.",
        "- LoRA trigger text is inserted as plain prompt tokens only; ComfyUI still owns LoRA loading.",
        "- Broad biography, role/personality prose, and stale archived state are excluded from final prompt assembly.",
        "",
        "## Visual beat improvements",
        "- The beat selector scores sentence/pair units instead of whole paragraphs so planning, wounds, travel, and quiet dialogue stay concrete.",
        "- Dialogue/planning scenes stay in the present scene moment instead of inventing future action.",
        "",
        "## Workflow flexibility changes",
        "- StoryDriver supports global, per-story, and scene/version workflow selection while keeping ComfyUI in charge of model, sampler, LoRA, size, and other workflow settings.",
        "- Prompt cache keys include workflow id, so changing workflow can prepare a separate prompt draft for the same scene.",
        f"- Smoke result: {workflow_flex_result}",
        "",
        "## Before / After example",
        f"- Before: {before_shape}",
        f"- After: {after_shape}",
        "",
        "## Tests",
        f"- Image prompt quality smoke: {'PASS' if not failures else 'FAIL'}",
        *(f"- {failure}" for failure in failures),
        "",
        "## Remaining limitations",
        "- Real image fidelity still depends on the selected ComfyUI workflow, model, sampler, and LoRA setup.",
        "- The prompt builder remains intentionally concise; highly specialized workflow-specific prompt syntax should live in story visual direction or ComfyUI workflow design, not debug notes.",
    ]
    CLEANUP_REPORT_PATH.write_text("\n".join(cleanup_lines) + "\n", encoding="utf-8")
    print(f"Report written: {REPORT_PATH}")
    print(f"Cleanup report written: {CLEANUP_REPORT_PATH}")
    if failures:
        print("\n".join(failures))
        return 1
    print("Image prompt quality smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
