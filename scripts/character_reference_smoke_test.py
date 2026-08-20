from __future__ import annotations

import base64
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
from app.routes.images import create_pending_generated_image  # noqa: E402
from app.schemas import ImageWorkflowConfig  # noqa: E402
from app.services.character_references import (  # noqa: E402
    references_for_generation,
    store_character_reference,
    workflow_supports_reference_mapping,
)
from app.services.image_prompt_service import fallback_prompt, load_visual_context  # noqa: E402
from app.services.image_workflows import inject_workflow_values  # noqa: E402


REPORT_PATH = BACKEND / "data" / "logs" / "CHARACTER_REFERENCE_IMAGE_REPORT.md"
TEST_STORY_TITLE = "StoryDriver Character Reference Smoke Test"
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def ensure_session() -> str:
    with db_session() as db:
        row = db.execute("SELECT id FROM sessions WHERE title = ? LIMIT 1", (TEST_STORY_TITLE,)).fetchone()
        if row:
            return row["id"]
        session_id = str(uuid4())
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, TEST_STORY_TITLE))
        return session_id


def ensure_scene(session_id: str) -> tuple[str, str]:
    with db_session() as db:
        row = db.execute(
            "SELECT id FROM scenes WHERE session_id = ? ORDER BY created_at ASC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row:
            scene_id = row["id"]
        else:
            scene_id = str(uuid4())
            db.execute(
                """
                INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
                VALUES (?, ?, 'reference smoke', ?, 'continue')
                """,
                (
                    scene_id,
                    session_id,
                    "Mara sits by the campfire wearing a red cloak, her left shoulder bandaged after the fight.",
                ),
            )
        version = db.execute(
            "SELECT id FROM scene_versions WHERE scene_id = ? ORDER BY version_index DESC LIMIT 1",
            (scene_id,),
        ).fetchone()
        if version:
            return scene_id, version["id"]
        version_id = f"{scene_id}-v1"
        db.execute(
            """
            INSERT INTO scene_versions (
                id, scene_id, session_id, director_note, generated_text, mode, version_index
            )
            SELECT ?, id, session_id, director_note, generated_text, mode, 1
            FROM scenes
            WHERE id = ?
            """,
            (version_id, scene_id),
        )
        return scene_id, version_id


def ensure_character(session_id: str, name: str = "Mara") -> str:
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
                VALUES (?, ?, 'thief/scout', 'guarded, sarcastic', ?, ?, 'mara_lora_v1')
                """,
                (
                    character_id,
                    name,
                    "short black hair, scar over left eyebrow, lean practical build",
                    "short black hair, scar over left eyebrow, guarded eyes",
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, 1)
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
                visual_consistency_notes = excluded.visual_consistency_notes,
                used_in_image_prompts = excluded.used_in_image_prompts
            """,
            (
                character_id,
                "Mara has short black hair, guarded eyes, and a scar over her left eyebrow.",
                "grounded face, alert eyes, scar over left eyebrow",
                "short black hair",
                "lean practical build",
                "fictional adult",
                "green cloak and travel leathers",
                "scar over left eyebrow",
                "deep red cloak, worn leather, rain-dark cloth",
                "wrong face, wrong hair, extra fingers",
                "mara_lora_v1",
                "Keep stable face, hair, build, and scar consistent across images.",
            ),
        )
        for key, value in (
            ("current_outfit", "wearing a red cloak torn at the shoulder"),
            ("injury_left_shoulder", "left shoulder bandaged and healing"),
        ):
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
                    confidence, manual_override
                )
                VALUES (?, ?, ?, ?, 'appearance', ?, ?, 0.99, 1)
                """,
                (str(uuid4()), session_id, character_id, name, key, value),
            )
        return character_id


def ensure_reference(character_id: str) -> dict:
    with db_session() as db:
        row = db.execute(
            """
            SELECT *
            FROM character_reference_images
            WHERE character_id = ? AND archived = 0
            ORDER BY is_primary DESC, updated_at DESC
            LIMIT 1
            """,
            (character_id,),
        ).fetchone()
        if row and Path(row["image_path"]).exists():
            return dict(row)
        reference = store_character_reference(
            db,
            character_id=character_id,
            filename="mara_reference.png",
            content_base64=TINY_PNG_BASE64,
            source="smoke test placeholder",
            notes="Tiny placeholder reference used only to validate reference plumbing.",
            is_primary=True,
        )
        return reference.model_dump()


def assert_true(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def run() -> int:
    init_db()
    session_id = ensure_session()
    scene_id, version_id = ensure_scene(session_id)
    mara_id = ensure_character(session_id)
    reference = ensure_reference(mara_id)
    failures: list[str] = []

    scene_text = "Mara sits by the campfire after the fight, rain hissing around the coals while she tightens the bandage at her left shoulder."
    context = load_visual_context(session_id, scene_text)
    prompt = fallback_prompt(
        scene_text=scene_text,
        negative_prompt="wrong character, plastic skin",
        visual_context=context,
        workflow_notes="gritty dark fantasy realism, rough documentary still",
    )
    prompt_text = prompt["prompt"].lower()
    assert_true("red cloak" in prompt_text, "Prompt should use live red cloak state.", failures)
    assert_true("green cloak" not in prompt_text, "Prompt should not leak stale default green cloak.", failures)
    assert_true("left shoulder bandaged" in prompt_text, "Prompt should include current bandaged injury.", failures)
    assert_true("mara_lora_v1" in prompt_text, "Prompt should include Mara LoRA trigger as text.", failures)

    no_reference_config = ImageWorkflowConfig(
        workflow_file="reference-smoke.json",
        positive_prompt_node_id="1",
        positive_prompt_input="text",
    )
    reference_config = ImageWorkflowConfig(
        workflow_file="reference-smoke.json",
        positive_prompt_node_id="1",
        positive_prompt_input="text",
        reference_image_node_id="2",
        reference_image_input="image",
        seed_node_id="3",
        seed_input="seed",
        output_node_id="4",
    )
    assert_true(not workflow_supports_reference_mapping(no_reference_config), "Text-only workflow should not support reference mapping.", failures)
    assert_true(workflow_supports_reference_mapping(reference_config), "Reference workflow config should support reference mapping.", failures)

    with db_session() as db:
        no_refs, no_ref_notes = references_for_generation(
            db,
            session_id=session_id,
            characters_included=["Mara"],
            config=no_reference_config,
        )
        refs, ref_notes = references_for_generation(
            db,
            session_id=session_id,
            characters_included=["Mara"],
            config=reference_config,
        )
        multi_refs, multi_notes = references_for_generation(
            db,
            session_id=session_id,
            characters_included=["Mara", "Elias"],
            config=reference_config,
        )
    assert_true(not no_refs and no_ref_notes, "Text-only workflow should fall back with a note.", failures)
    assert_true(len(refs) == 1 and refs[0].get("character_name") == "Mara", "Mapped workflow should choose Mara primary reference.", failures)
    assert_true(not multi_refs and multi_notes, "Multiple characters should not misuse one reference image.", failures)

    workflow_payload = inject_workflow_values(
        {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "old"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": ""}},
            "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
            "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
        },
        reference_config,
        prompt=prompt["prompt"],
        negative_prompt=prompt["negative_prompt"],
        seed=42,
        reference_image_path=refs[0]["image_path"] if refs else "",
    )
    assert_true(workflow_payload["1"]["inputs"]["text"] == prompt["prompt"], "Positive prompt injection failed.", failures)
    assert_true(workflow_payload["2"]["inputs"]["image"] == refs[0]["image_path"], "Reference image injection failed.", failures)
    assert_true(workflow_payload["3"]["inputs"]["seed"] == 42, "Seed injection failed.", failures)

    image = create_pending_generated_image(
        session_id=session_id,
        scene_id=scene_id,
        version_id=version_id,
        workflow_id="reference-smoke",
        workflow_name="Reference Smoke",
        prompt=prompt["prompt"],
        negative_prompt=prompt["negative_prompt"],
        seed=42,
        characters_included=prompt["characters_included"],
        continuity_used=prompt["continuity_used"],
        reference_images_used=refs,
        live_state_used={"continuity_used": prompt["continuity_used"]},
    )
    assert_true("Mara" in image.characters_included, "Generated image metadata should record included character.", failures)
    assert_true(bool(image.reference_images_used), "Generated image metadata should record reference images used.", failures)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# Character Reference Image Report",
                "",
                "## Character reference system added",
                "- Added optional character reference image records stored under `backend\\data\\character_refs`.",
                "- Reference images preserve source, notes, primary flag, archived flag, timestamps, and safe static URL metadata.",
                "- UI removal archives records instead of deleting source/user data.",
                "",
                "## Workflow mapping added",
                "- Workflow configs now support optional reference image, img2img, character reference, and denoise/strength mapping fields.",
                "- Workflow scanning reports likely reference/image input nodes when an exported API workflow exposes them.",
                "",
                "## When references are used",
                "- References are used only when the selected workflow has an explicit image/reference mapping.",
                "- StoryDriver uses one primary reference only when exactly one included character is detected and has a primary reference image.",
                "- Multi-character scenes fall back to text-only consistency unless a future workflow has explicit multi-reference mappings.",
                "",
                "## Fallback behavior",
                f"- No-reference workflow notes: {no_ref_notes}",
                f"- Multi-character fallback notes: {multi_notes}",
                "- Missing reference files are archived and ignored rather than breaking generation.",
                "",
                "## Continuity metadata",
                "- Generated images now record characters_included, continuity_used, reference_images_used, and live_state_used metadata.",
                f"- Smoke image metadata reference count: {len(image.reference_images_used)}",
                "",
                "## Tests run",
                "- scripts\\character_reference_smoke_test.bat",
                "",
                "## Smoke result",
                f"- Reference file: {reference.get('image_path')}",
                f"- Prompt characters: {', '.join(prompt['characters_included']) or 'none'}",
                f"- Reference selected: {refs[0].get('image_path') if refs else 'none'}",
                f"- Result: {'PASS' if not failures else 'FAIL'}",
                "",
                "## Remaining limitations",
                "- Reference/image input compatibility is workflow-specific. ComfyUI owns actual reference/img2img node behavior.",
                "- StoryDriver does not support multi-reference routing yet; it falls back to text prompt consistency for multi-character scenes.",
                "- Real character-consistency testing still requires a ComfyUI workflow with a validated reference/image input mapping.",
                "",
                "## Next recommended step",
                "- Export or select a reference-capable API workflow, map the known image input under Advanced mapping, then generate one Auto Image Priority test image.",
                "",
                "## Failures",
                *(f"- {failure}" for failure in failures),
            ]
        ),
        encoding="utf-8",
    )
    if failures:
        print("\n".join(failures))
        print(f"Report written: {REPORT_PATH}")
        return 1
    print("Character reference smoke passed.")
    print(f"Report written: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
