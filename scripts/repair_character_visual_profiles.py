from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import db_session  # noqa: E402
from app.services.character_visual_repair import (  # noqa: E402
    repair_character_visual_profile,
    scan_and_repair_visual_profiles,
)


def character_ids_for_name(name: str) -> list[str]:
    with db_session() as db:
        rows = db.execute(
            "SELECT id FROM characters WHERE lower(name) = lower(?) ORDER BY updated_at DESC",
            (name,),
        ).fetchall()
    return [row["id"] for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely scan or repair contaminated StoryDriver character visual profile fields.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply high-confidence repairs. Default is dry-run.")
    parser.add_argument("--character-id", help="Repair one character id.")
    parser.add_argument("--name", help="Repair characters with this exact name.")
    parser.add_argument("--session-id", help="Use this story/session when extracting from a scene.")
    parser.add_argument("--scene-id", help="Use this source scene for extraction/repair.")
    parser.add_argument("--version-id", help="Use this source scene version for extraction/repair.")
    parser.add_argument(
        "--mode",
        choices=("repair", "extract", "clear_contaminated"),
        default="repair",
        help="Repair contaminated fields, extract from scene, or only clear contaminated appearance.",
    )
    args = parser.parse_args()

    if args.character_id or args.name:
        ids = [args.character_id] if args.character_id else character_ids_for_name(args.name or "")
        results = [
            repair_character_visual_profile(
                character_id=character_id,
                session_id=args.session_id,
                scene_id=args.scene_id,
                version_id=args.version_id,
                mode=args.mode,
                apply_changes=args.apply,
            )
            for character_id in ids
            if character_id
        ]
        output = {
            "apply_changes": args.apply,
            "checked": len(ids),
            "affected": sum(1 for result in results if result["changes"] or result["warnings"]),
            "results": results,
        }
    else:
        output = scan_and_repair_visual_profiles(apply_changes=args.apply)

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
