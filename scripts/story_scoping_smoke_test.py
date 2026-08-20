from __future__ import annotations

from uuid import uuid4

from app.database import db_session, init_db
from app.routes.images import load_session_image_settings, save_session_image_settings
from app.schemas import SessionImageSettingsUpdate
from app.services.image_workflows import scan_workflows
from app.generation.prompt_builder import build_scene_prompt, load_active_characters, load_world_notes
from app.services.scene_image_prompts import (
    load_scene_image_prompt_settings,
    resolve_prompt_workflow,
    save_scene_image_prompt_settings,
)


def insert_story(seed: str, title: str, characters: list[tuple[str, str]], world_setting: str) -> tuple[str, str, str]:
    session_id = f"story-scope-{seed}-{uuid4().hex[:8]}"
    scene_id = f"story-scope-scene-{seed}-{uuid4().hex[:8]}"
    version_id = f"story-scope-version-{seed}-{uuid4().hex[:8]}"
    with db_session() as db:
        db.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", (session_id, title))
        for name, role in characters:
            character_id = f"story-scope-character-{seed}-{uuid4().hex[:8]}"
            db.execute(
                """
                INSERT INTO characters (id, name, role, personality, appearance, current_state)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    character_id,
                    name,
                    role,
                    "test character for story scoping",
                    f"{name} visual marker",
                    f"{name} current state marker",
                ),
            )
            db.execute(
                "INSERT INTO session_characters (id, session_id, character_id, is_active) VALUES (?, ?, ?, 1)",
                (f"story-scope-link-{uuid4().hex[:8]}", session_id, character_id),
            )
        db.execute(
            """
            INSERT INTO world_notes (id, session_id, setting, tone, rules, locations, factions, conflicts, history)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"story-scope-world-{uuid4().hex[:8]}",
                session_id,
                world_setting,
                f"{seed} tone marker",
                f"{seed} rules marker",
                f"{seed} location marker",
                f"{seed} faction marker",
                f"{seed} conflict marker",
                f"{seed} history marker",
            ),
        )
        db.execute(
            """
            INSERT INTO scenes (id, session_id, director_note, generated_text, mode)
            VALUES (?, ?, ?, ?, 'continue')
            """,
            (scene_id, session_id, f"{seed} director note", f"{seed} generated scene text"),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, f"{seed} director note", f"{seed} generated version text"),
        )
    return session_id, scene_id, version_id


def assert_contains(text: str, expected: str) -> None:
    if expected not in text:
        raise AssertionError(f"Expected {expected!r} in prompt/settings output.")


def assert_not_contains(text: str, unexpected: str) -> None:
    if unexpected in text:
        raise AssertionError(f"Unexpected cross-story value {unexpected!r} leaked into prompt/settings output.")


def main() -> None:
    init_db()
    workflows = scan_workflows()
    if len(workflows) < 2:
        raise AssertionError("Need at least two workflow files to verify per-story workflow differences.")
    workflow_a = workflows[0].id
    workflow_b = workflows[1].id

    story_a, scene_a, version_a = insert_story(
        "a",
        "StoryDriver Scoping Test A - Three Sisters",
        [("Elara Scopecheck", "streetwise sister"), ("Maren Scopecheck", "farm scout")],
        "no-magic medieval frontier scope marker",
    )
    story_b, scene_b, version_b = insert_story(
        "b",
        "StoryDriver Scoping Test B - Sci-Fi Detective",
        [("Detective Voss Scopecheck", "orbital investigator"), ("Unit K Scopecheck", "forensic android")],
        "rainy orbital detective city scope marker",
    )

    save_session_image_settings(
        story_a,
        SessionImageSettingsUpdate(
            selected_workflow_id=workflow_a,
            image_resource_mode="image_priority",
            auto_image_on_narrate=True,
            generate_image_while_narrating=True,
        ),
    )
    save_session_image_settings(
        story_b,
        SessionImageSettingsUpdate(
            selected_workflow_id=workflow_b,
            image_resource_mode="manual",
            auto_image_on_narrate=False,
            generate_image_while_narrating=False,
        ),
    )
    save_scene_image_prompt_settings(story_a, scene_a, version_a, selected_workflow_id=workflow_b)

    chars_a = load_active_characters(story_a)
    chars_b = load_active_characters(story_b)
    world_a = load_world_notes(story_a) or {}
    world_b = load_world_notes(story_b) or {}
    prompt_a = build_scene_prompt(
        session_id=story_a,
        director_note="continue the sisters' grounded rescue plan",
        mode="continue",
        recent_scenes=[],
    )
    prompt_b = build_scene_prompt(
        session_id=story_b,
        director_note="continue the detective case",
        mode="continue",
        recent_scenes=[],
    )
    story_a_settings = load_session_image_settings(story_a)
    story_b_settings = load_session_image_settings(story_b)
    scene_settings = load_scene_image_prompt_settings(story_a, scene_a, version_a)
    scene_workflow, _ = resolve_prompt_workflow(story_a, scene_id=scene_a, version_id=version_a)
    story_a_workflow, _ = resolve_prompt_workflow(story_a)
    story_b_workflow, _ = resolve_prompt_workflow(story_b)

    assert_contains(prompt_a, "Elara Scopecheck")
    assert_contains(prompt_a, "no-magic medieval frontier scope marker")
    assert_not_contains(prompt_a, "Detective Voss Scopecheck")
    assert_not_contains(prompt_a, "rainy orbital detective city scope marker")
    assert_contains(prompt_b, "Detective Voss Scopecheck")
    assert_contains(prompt_b, "rainy orbital detective city scope marker")
    assert_not_contains(prompt_b, "Elara Scopecheck")
    assert_not_contains(prompt_b, "no-magic medieval frontier scope marker")
    assert story_a_settings.selected_workflow_id == workflow_a
    assert story_b_settings.selected_workflow_id == workflow_b
    assert story_a_settings.image_resource_mode == "image_priority"
    assert story_b_settings.image_resource_mode == "manual"
    assert scene_settings["selected_workflow_id"] == workflow_b
    assert story_a_workflow and story_a_workflow.id == workflow_a
    assert story_b_workflow and story_b_workflow.id == workflow_b
    assert scene_workflow and scene_workflow.id == workflow_b

    print("story_scoping_smoke_test: PASS")
    print(f"story_a={story_a} characters={[item['name'] for item in chars_a]} world={world_a.get('setting')}")
    print(f"story_b={story_b} characters={[item['name'] for item in chars_b]} world={world_b.get('setting')}")
    print(f"story_a_workflow={workflow_a}")
    print(f"story_b_workflow={workflow_b}")
    print(f"story_a_scene_override={workflow_b}")


if __name__ == "__main__":
    main()
