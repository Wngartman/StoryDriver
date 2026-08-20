from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import DB_PATH  # noqa: E402
from app.memory.characters import ACTION_LIKE_NAME_WORDS  # noqa: E402


ALLOWED_AUTO_CURRENT_STATES = {"", "introduced in the current scene"}
AUTO_NOTE_PREFIX = "Auto-created by StoryDriver from a generated scene."


def row_dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row else {}


def reference_counts(db: sqlite3.Connection, character_id: str, character_name: str) -> dict[str, int]:
    queries = {
        "session_links": (
            "SELECT COUNT(*) FROM session_characters WHERE character_id = ?",
            (character_id,),
        ),
        "live_state": (
            "SELECT COUNT(*) FROM character_live_state WHERE character_id = ? OR lower(character_name) = lower(?)",
            (character_id, character_name),
        ),
        "state_events": (
            "SELECT COUNT(*) FROM character_state_events WHERE character_id = ? OR lower(character_name) = lower(?)",
            (character_id, character_name),
        ),
        "relationships": (
            "SELECT COUNT(*) FROM relationship_state WHERE character_a_id = ? OR character_b_id = ? "
            "OR lower(character_a_name) = lower(?) OR lower(character_b_name) = lower(?)",
            (character_id, character_id, character_name, character_name),
        ),
        "objects": (
            "SELECT COUNT(*) FROM object_state WHERE owner_character_id = ? OR holder_character_id = ? "
            "OR lower(owner_character_name) = lower(?) OR lower(holder_character_name) = lower(?)",
            (character_id, character_id, character_name, character_name),
        ),
        "emotional_memories": (
            "SELECT COUNT(*) FROM emotional_memories WHERE json_valid(related_characters_json) "
            "AND EXISTS (SELECT 1 FROM json_each(related_characters_json) WHERE lower(value) = lower(?))",
            (character_name,),
        ),
        "reference_images": (
            "SELECT COUNT(*) FROM character_reference_images WHERE character_id = ?",
            (character_id,),
        ),
    }
    counts: dict[str, int] = {}
    for key, (query, params) in queries.items():
        counts[key] = int(db.execute(query, params).fetchone()[0])
    return counts


def assess(db: sqlite3.Connection, character_id: str) -> dict:
    character = row_dict(db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone())
    if not character:
        return {"character_id": character_id, "exists": False, "eligible": False, "reasons": ["not_found"]}
    visual = row_dict(
        db.execute("SELECT * FROM character_visual_profiles WHERE character_id = ?", (character_id,)).fetchone()
    )
    counts = reference_counts(db, character_id, str(character.get("name") or ""))
    blocking_references = {
        key: value for key, value in counts.items() if key != "session_links" and value
    }
    reasons: list[str] = []
    if int(character.get("auto_created") or 0) != 1:
        reasons.append("not_auto_created")
    if str(character.get("name") or "").casefold() not in ACTION_LIKE_NAME_WORDS:
        reasons.append("name_not_action_like")
    if str(character.get("private_notes") or "").strip() and not str(character["private_notes"]).startswith(AUTO_NOTE_PREFIX):
        reasons.append("manual_private_notes")
    for field in ("personality", "appearance", "relationships", "voice", "lora_trigger"):
        if str(character.get(field) or "").strip():
            reasons.append(f"populated_{field}")
    if str(character.get("current_state") or "").strip() not in ALLOWED_AUTO_CURRENT_STATES:
        reasons.append("meaningful_current_state")
    if blocking_references:
        reasons.append("referenced_by_continuity_data")
    if visual and any(
        str(visual.get(field) or "").strip()
        for field in (
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
        )
    ):
        reasons.append("populated_visual_identity")
    return {
        "character_id": character_id,
        "name": character.get("name") or "",
        "exists": True,
        "auto_created": bool(character.get("auto_created")),
        "eligible": not reasons,
        "reasons": reasons,
        "references": counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Review or remove a proven invalid automatic character card.")
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--character-id", required=True)
    parser.add_argument("--expected-name", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = sqlite3.connect(args.database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        result = assess(db, args.character_id)
        if not result.get("exists"):
            print(json.dumps({**result, "mode": "apply" if args.apply else "dry_run"}, indent=2))
            return 0
        if str(result.get("name") or "").casefold() != args.expected_name.casefold():
            raise SystemExit("Refusing cleanup: expected name does not match the current record.")
        if not args.apply:
            print(json.dumps({**result, "mode": "dry_run"}, indent=2))
            return 0 if result["eligible"] else 2
        if not result["eligible"]:
            raise SystemExit(f"Refusing cleanup: {', '.join(result['reasons'])}")
        db.execute("BEGIN IMMEDIATE")
        locked_result = assess(db, args.character_id)
        if not locked_result.get("eligible") or str(locked_result.get("name") or "").casefold() != args.expected_name.casefold():
            db.rollback()
            raise SystemExit("Refusing cleanup: record changed after the safety review.")
        deleted = db.execute("DELETE FROM characters WHERE id = ?", (args.character_id,)).rowcount
        foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()
        if deleted != 1 or foreign_key_errors:
            db.rollback()
            raise SystemExit("Cleanup failed integrity verification; transaction rolled back.")
        db.commit()
        print(json.dumps({**locked_result, "mode": "apply", "deleted": True}, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
