from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.database import db_session
from app.memory.characters import (
    compact_visual_fragment,
    concise_image_prompt_for_character,
    fallback_visual_profile,
    load_scene_text,
    looks_contaminated_visual_text,
    visual_hint_count,
)


PROFILE_FIELDS = (
    "base_visual_description",
    "face_description",
    "hair",
    "body_build",
    "age_marker",
    "default_outfit",
    "distinctive_marks",
    "color_palette",
    "negative_prompt",
    "z_image_lora_trigger",
    "alternate_lora_triggers",
    "preferred_voice",
    "visual_consistency_notes",
)
VISUAL_CORE_FIELDS = (
    "base_visual_description",
    "face_description",
    "hair",
    "body_build",
    "age_marker",
    "default_outfit",
    "distinctive_marks",
    "color_palette",
)
BACKUP_PATH = DATA_DIR / "logs" / "character_visual_repair_backup.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def row_dict(row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()} if row else {}


def default_profile() -> dict[str, Any]:
    return {field: "" for field in PROFILE_FIELDS} | {"used_in_image_prompts": 1}


def profile_from_row(row) -> dict[str, Any]:
    profile = default_profile()
    if row:
        for key in row.keys():
            profile[key] = row[key]
    return profile


def backed_up_records() -> list[dict[str, Any]]:
    if not BACKUP_PATH.exists():
        return []
    try:
        parsed = json.loads(BACKUP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return parsed if isinstance(parsed, list) else []


def append_backup(entry: dict[str, Any]) -> None:
    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    records = backed_up_records()
    records.append(entry)
    BACKUP_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def profile_identity_values(profile: dict[str, Any]) -> list[str]:
    return [
        str(profile.get("base_visual_description") or ""),
        str(profile.get("face_description") or ""),
        str(profile.get("hair") or ""),
        str(profile.get("body_build") or ""),
        str(profile.get("age_marker") or ""),
        str(profile.get("distinctive_marks") or ""),
        str(profile.get("default_outfit") or ""),
    ]


def appearance_summary(profile: dict[str, Any]) -> str:
    base = compact_visual_fragment(profile.get("base_visual_description"), 360, require_visual_hint=True, max_words=46)
    if base:
        return base
    bits = [
        compact_visual_fragment(profile.get("hair"), 120, require_visual_hint=True),
        compact_visual_fragment(profile.get("face_description"), 160, require_visual_hint=True),
        compact_visual_fragment(profile.get("body_build"), 160, require_visual_hint=True),
        compact_visual_fragment(profile.get("distinctive_marks"), 160, require_visual_hint=True),
    ]
    return "; ".join(bit for bit in bits if bit)[:360]


def needs_visual_review(profile: dict[str, Any], character: dict[str, Any]) -> bool:
    if any(visual_hint_count(value) for value in profile_identity_values(profile)):
        return False
    return not compact_visual_fragment(character.get("appearance"), 260, require_visual_hint=True)


def build_scene_profile(scene_text: str, name: str) -> dict[str, str]:
    if not scene_text:
        return default_profile()
    return fallback_visual_profile(scene_text, name)


def merge_clean_card_visuals(suggested: dict[str, Any], character: dict[str, Any]) -> dict[str, Any]:
    name = str(character.get("name") or "")
    merged = dict(suggested)
    card_fragments = [
        compact_visual_fragment(character.get("appearance"), 420, require_visual_hint=True, max_words=52),
        compact_visual_fragment(character.get("image_prompt"), 420, require_visual_hint=True, max_words=52),
    ]
    seen: set[str] = set()
    clean_card_parts: list[str] = []
    for fragment in card_fragments:
        key = fragment.lower()
        if fragment and key not in seen:
            clean_card_parts.append(fragment)
            seen.add(key)
    clean_card_text = "; ".join(clean_card_parts)
    if not clean_card_text:
        return merged
    card_profile = fallback_visual_profile(f"{name} {clean_card_text}.", name)
    for field in VISUAL_CORE_FIELDS:
        if not str(merged.get(field) or "") and str(card_profile.get(field) or ""):
            merged[field] = card_profile[field]
    if not str(merged.get("base_visual_description") or ""):
        merged["base_visual_description"] = clean_card_text[:420]
    return merged


def load_source_scene_text(
    *,
    session_id: str | None,
    scene_id: str | None,
    version_id: str | None,
    profile: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    resolved_session_id = session_id
    resolved_scene_id = scene_id or profile.get("source_scene_id")
    resolved_version_id = version_id or profile.get("source_version_id")
    if not resolved_session_id or not resolved_scene_id:
        return "", resolved_scene_id, resolved_version_id
    text, loaded_version_id = load_scene_text(resolved_session_id, resolved_scene_id, resolved_version_id)
    return text, resolved_scene_id, loaded_version_id or resolved_version_id


def source_ids_from_private_notes(character: dict[str, Any]) -> tuple[str | None, str | None]:
    notes = str(character.get("private_notes") or "")
    scene_match = re.search(r"Source scene:\s*([0-9a-fA-F-]{20,})", notes)
    version_match = re.search(r"Source version:\s*([0-9a-fA-F-]{20,})", notes)
    return (
        scene_match.group(1) if scene_match else None,
        version_match.group(1) if version_match else None,
    )


def choose_character_session(db, character_id: str, session_id: str | None) -> str | None:
    if session_id:
        return session_id
    row = db.execute(
        """
        SELECT session_id
        FROM session_characters
        WHERE character_id = ?
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        (character_id,),
    ).fetchone()
    return row["session_id"] if row else None


def load_character_and_profile(db, character_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    character_row = db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    if not character_row:
        raise ValueError("Character not found")
    profile_row = db.execute(
        "SELECT * FROM character_visual_profiles WHERE character_id = ?",
        (character_id,),
    ).fetchone()
    return row_dict(character_row), profile_from_row(profile_row)


def upsert_profile(db, character_id: str, profile: dict[str, Any]) -> None:
    data = default_profile()
    data.update({field: str(profile.get(field) or "") for field in PROFILE_FIELDS})
    data["used_in_image_prompts"] = 1 if profile.get("used_in_image_prompts", 1) != 0 else 0
    data["auto_created_confidence"] = float(profile.get("auto_created_confidence") or 0)
    data["source_scene_id"] = profile.get("source_scene_id")
    data["source_version_id"] = profile.get("source_version_id")
    db.execute(
        """
        INSERT INTO character_visual_profiles (
            character_id, base_visual_description, face_description, hair, body_build,
            age_marker, default_outfit, distinctive_marks, color_palette,
            negative_prompt, z_image_lora_trigger, alternate_lora_triggers,
            preferred_voice, visual_consistency_notes, used_in_image_prompts,
            auto_created_confidence, source_scene_id, source_version_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            used_in_image_prompts = excluded.used_in_image_prompts,
            auto_created_confidence = excluded.auto_created_confidence,
            source_scene_id = excluded.source_scene_id,
            source_version_id = excluded.source_version_id,
            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (
            character_id,
            data["base_visual_description"],
            data["face_description"],
            data["hair"],
            data["body_build"],
            data["age_marker"],
            data["default_outfit"],
            data["distinctive_marks"],
            data["color_palette"],
            data["negative_prompt"],
            data["z_image_lora_trigger"],
            data["alternate_lora_triggers"],
            data["preferred_voice"],
            data["visual_consistency_notes"],
            data["used_in_image_prompts"],
            data["auto_created_confidence"],
            data["source_scene_id"],
            data["source_version_id"],
        ),
    )


def repair_character_visual_profile(
    *,
    character_id: str,
    session_id: str | None = None,
    scene_id: str | None = None,
    version_id: str | None = None,
    mode: str = "repair",
    apply_changes: bool = True,
) -> dict[str, Any]:
    with db_session() as db:
        character, profile = load_character_and_profile(db, character_id)
        resolved_session_id = choose_character_session(db, character_id, session_id)
        note_scene_id, note_version_id = source_ids_from_private_notes(character)
        scene_text, resolved_scene_id, resolved_version_id = load_source_scene_text(
            session_id=resolved_session_id,
            scene_id=scene_id or note_scene_id,
            version_id=version_id or note_version_id,
            profile=profile,
        )
        suggested = merge_clean_card_visuals(build_scene_profile(scene_text, character["name"]), character)
        updated_profile = dict(profile)
        changes: dict[str, Any] = {}
        warnings: list[str] = []

        appearance_contaminated = looks_contaminated_visual_text(character.get("appearance"), require_visual_hint=False)
        image_prompt_contaminated = looks_contaminated_visual_text(character.get("image_prompt"), require_visual_hint=False)

        for field in VISUAL_CORE_FIELDS:
            current_raw = profile.get(field)
            current = compact_visual_fragment(current_raw, 420 if field == "base_visual_description" else 220)
            current_contaminated = looks_contaminated_visual_text(current_raw)
            suggestion = compact_visual_fragment(
                suggested.get(field),
                420 if field == "base_visual_description" else 220,
                require_visual_hint=field not in {"color_palette"},
                max_words=50 if field == "base_visual_description" else 34,
            )
            should_fill = mode == "extract" and not current
            should_repair = mode == "repair" and (not current or current_contaminated)
            if suggestion and (should_fill or should_repair):
                updated_profile[field] = suggestion
                changes[f"visual_profile.{field}"] = suggestion
            elif mode == "repair" and current_contaminated:
                updated_profile[field] = ""
                changes[f"visual_profile.{field}"] = ""

        clean_appearance = appearance_summary(updated_profile)
        character_updates: dict[str, str] = {}
        if mode == "clear_contaminated":
            if appearance_contaminated:
                character_updates["appearance"] = clean_appearance
                changes["appearance"] = clean_appearance
            else:
                warnings.append("Appearance did not look contaminated.")
        elif appearance_contaminated:
            character_updates["appearance"] = clean_appearance
            changes["appearance"] = clean_appearance
        elif not compact_visual_fragment(character.get("appearance"), 420, require_visual_hint=True) and clean_appearance:
            character_updates["appearance"] = clean_appearance
            changes["appearance"] = clean_appearance

        clean_image_prompt = concise_image_prompt_for_character(character["name"], updated_profile)
        has_identity_prompt = visual_hint_count(clean_image_prompt) > 0 and clean_image_prompt.strip().lower() != character["name"].strip().lower()
        if image_prompt_contaminated:
            character_updates["image_prompt"] = clean_image_prompt if has_identity_prompt else ""
            changes["image_prompt"] = character_updates["image_prompt"]
        elif (
            not compact_visual_fragment(character.get("image_prompt"), 520, require_visual_hint=True)
            and clean_image_prompt
            and has_identity_prompt
        ):
            character_updates["image_prompt"] = clean_image_prompt
            changes["image_prompt"] = clean_image_prompt

        note = str(updated_profile.get("visual_consistency_notes") or "")
        original_note = note
        should_mark_review = mode != "repair" or appearance_contaminated or image_prompt_contaminated or bool(scene_text)
        if should_mark_review and needs_visual_review(updated_profile, character):
            review_note = "Needs visual review: not enough clean supported appearance detail found."
            if review_note not in note:
                updated_profile["visual_consistency_notes"] = "; ".join(part for part in [note, review_note] if part)
            warnings.append("Not enough clean visual evidence was found; marked for review.")
        elif changes and mode in {"repair", "extract"}:
            repair_note = f"Visual profile {mode} ran from scene {resolved_scene_id or 'unspecified'}."
            if repair_note not in note:
                updated_profile["visual_consistency_notes"] = "; ".join(part for part in [note, repair_note] if part)
        if updated_profile.get("visual_consistency_notes") != original_note:
            changes["visual_profile.visual_consistency_notes"] = updated_profile.get("visual_consistency_notes", "")

        updated_profile["source_scene_id"] = resolved_scene_id or profile.get("source_scene_id")
        updated_profile["source_version_id"] = resolved_version_id or profile.get("source_version_id")
        if changes:
            updated_profile["auto_created_confidence"] = max(float(profile.get("auto_created_confidence") or 0), 0.72)

        backup_entry = {
            "created_at": utc_now(),
            "mode": mode,
            "apply_changes": apply_changes,
            "character_id": character_id,
            "character": character,
            "visual_profile": profile,
            "source": {
                "session_id": resolved_session_id,
                "scene_id": resolved_scene_id,
                "version_id": resolved_version_id,
            },
            "changes": changes,
            "warnings": warnings,
        }
        if changes:
            append_backup(backup_entry)

        if apply_changes and changes:
            if character_updates:
                assignments = ", ".join(f"{field} = ?" for field in character_updates)
                db.execute(
                    f"UPDATE characters SET {assignments}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                    (*character_updates.values(), character_id),
                )
            upsert_profile(db, character_id, updated_profile)

        refreshed_character, refreshed_profile = load_character_and_profile(db, character_id)

    return {
        "character": refreshed_character,
        "visual_profile": refreshed_profile,
        "changes": changes,
        "warnings": warnings,
        "backup_path": str(BACKUP_PATH),
        "source_scene_id": resolved_scene_id,
        "source_version_id": resolved_version_id,
        "applied": bool(apply_changes and changes),
    }


def scan_and_repair_visual_profiles(*, apply_changes: bool = False) -> dict[str, Any]:
    with db_session() as db:
        rows = db.execute("SELECT id FROM characters ORDER BY updated_at DESC").fetchall()
    results = []
    for row in rows:
        result = repair_character_visual_profile(
            character_id=row["id"],
            mode="repair",
            apply_changes=apply_changes,
        )
        if result["changes"] or result["warnings"]:
            results.append(result)
    return {
        "apply_changes": apply_changes,
        "checked": len(rows),
        "affected": len(results),
        "backup_path": str(BACKUP_PATH),
        "results": results,
    }
