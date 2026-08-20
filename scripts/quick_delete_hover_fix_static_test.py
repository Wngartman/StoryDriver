from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "frontend" / "src" / "components" / "Sidebar.jsx"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    source = SIDEBAR.read_text(encoding="utf-8")

    assert_true('aria-label={`Delete story ${session.title}`}' in source, "Sidebar story rows need an accessible delete action.")
    assert_true("opacity-0" in source and "group-hover:opacity-100" in source, "Delete action should stay quiet until its row is hovered.")
    assert_true("focus:opacity-100" in source, "Keyboard focus must reveal the delete action.")
    assert_true("onDeleteSession?.(session);" in source, "Delete action must send the complete story to the confirmation workflow.")
    assert_true("event.stopPropagation();" in source, "Quick-delete must stop propagation so row selection does not fire.")
    assert_true('title="Delete story"' in source, "Delete action should have a familiar tooltip.")
    assert_true("Trash2" in source, "Delete action should use the established icon library.")

    print("quick_delete_hover_fix_static_test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
