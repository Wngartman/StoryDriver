from pathlib import Path


root = Path(__file__).resolve().parents[1]
source = (root / "frontend/src/components/WorkspaceBackground.jsx").read_text(encoding="utf-8")
schema = (root / "backend/app/schemas.py").read_text(encoding="utf-8")
for needle in (
    "window.setInterval",
    "window.clearInterval",
    "visibilitychange",
    "background_pause_while_writing",
    'settings.background_rotation_order === "random"',
    "selectedItems.length < 2",
):
    if needle not in source:
        raise AssertionError(f"rotation contract missing {needle}")
if source.count("window.setInterval") != 1:
    raise AssertionError("background rotation must use exactly one interval")
if "value and value < 30" not in schema:
    raise AssertionError("backend does not enforce a safe custom rotation interval")
print("background rotation contract: PASS")
