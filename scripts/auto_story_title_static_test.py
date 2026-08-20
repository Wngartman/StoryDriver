from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.auto_title import (  # noqa: E402
    can_auto_title_row,
    clean_generated_title,
    generated_title_is_usable,
    is_generic_title,
    title_is_user_set,
)


class FakeRow(dict):
    def keys(self):
        return super().keys()


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    check(is_generic_title("Untitled Story"), "Untitled Story should be generic.")
    check(is_generic_title("Untitled 4"), "Numbered untitled placeholders should be generic.")
    check(is_generic_title("New Story 12"), "Numbered new-story placeholders should be generic.")
    check(not is_generic_title("The Lantern Below"), "Specific story titles should not be generic.")

    check(clean_generated_title('Title: "The Lantern Below"') == "The Lantern Below", "Title prefix/quotes should be removed.")
    check(generated_title_is_usable("The Lantern Below"), "Specific title should be accepted.")
    check(not generated_title_is_usable("Untitled Story"), "Placeholder title should be rejected.")
    check(not generated_title_is_usable("Here is a title"), "Explanatory output should be rejected.")
    check(not generated_title_is_usable("Chapter One"), "Chapter labels should be rejected.")

    placeholder = FakeRow(
        title="Untitled Story",
        title_source="placeholder",
        auto_title_status="skipped",
    )
    manual = FakeRow(
        title="Untitled Story",
        title_source="user_set",
        auto_title_status="user_set",
    )
    titled = FakeRow(
        title="The Lantern Below",
        title_source="auto",
        auto_title_status="generated",
    )
    check(can_auto_title_row(placeholder), "Generic placeholder should be eligible for auto-title.")
    check(title_is_user_set(manual), "Manual title flag should be recognized.")
    check(not can_auto_title_row(manual), "Manual titles must not be auto-titled.")
    check(not can_auto_title_row(titled), "Existing specific titles must not be auto-titled.")

    print("auto_story_title_static_test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
