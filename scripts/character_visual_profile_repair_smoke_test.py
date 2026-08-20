from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.memory.characters import (  # noqa: E402
    fallback_visual_profile,
    looks_contaminated_visual_text,
    normalized_visual_profile_from_item,
)


def main() -> int:
    scene = (
        "Elara stood at the farmhouse table with dark braided hair, strong shoulders, "
        "a worn red cloak, and a grime-streaked forearm. "
        "Kaelen moved silently near the door, lean and sharp-eyed, her heavy work boots dark with mud. "
        "Lyra, pale and younger, held a basin of water in both hands, her blonde hair loose around her face."
    )
    expected = {
        "Elara": ("dark braided hair", "red cloak"),
        "Kaelen": ("heavy work boots", ""),
        "Lyra": ("blonde hair", "pale"),
    }
    for name, terms in expected.items():
        profile = fallback_visual_profile(scene, name)
        joined = " ".join(str(value) for value in profile.values())
        assert profile["base_visual_description"], f"{name} base visual was empty"
        assert not looks_contaminated_visual_text(profile["base_visual_description"], require_visual_hint=True)
        assert any(term and term in joined for term in terms), f"{name} missing expected visual terms: {profile}"

    item = {
        "name": "Elara",
        "base_appearance_summary": (
            "The sun was a dying ember behind the jagged silhouette. "
            "Elara said nothing for three paragraphs."
        ),
        "visual_profile": {
            "hair": "dark braided hair",
            "body_build": "strong shoulders",
            "default_outfit": "worn red cloak",
        },
    }
    profile = normalized_visual_profile_from_item(scene, item, "Elara")
    assert "dying ember" not in profile["base_visual_description"]
    assert profile["hair"] == "dark braided hair"
    assert profile["body_build"] == "strong shoulders"
    assert profile["default_outfit"] == "worn red cloak"
    print("character visual profile repair smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
