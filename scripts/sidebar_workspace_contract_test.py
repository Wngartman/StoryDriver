from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, *needles: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise AssertionError(f"{path} missing: {missing}")


require(
    "frontend/src/components/Sidebar.jsx",
    'collapsed ? "Open sidebar" : "Close sidebar"',
    'aria-label="Search stories"',
    "No matching stories.",
    "onDeleteSession?.(session)",
)
require(
    "frontend/src/components/AppShell.jsx",
    'data-sidebar-collapsed',
    "saveUiSettings({ sidebar_collapsed:",
)
require(
    "frontend/src/styles.css",
    ".sd-sidebar-shell",
    'data-sidebar-collapsed="true"',
    "width: 68px",
)
print("sidebar workspace contract: PASS")
