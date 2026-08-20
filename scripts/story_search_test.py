from pathlib import Path


root = Path(__file__).resolve().parents[1]
source = (root / "frontend/src/components/Sidebar.jsx").read_text(encoding="utf-8")
for needle in (
    "window.setTimeout",
    "toLocaleLowerCase",
    "filteredSessions",
    'event.key !== "Escape"',
    "sessions.filter",
):
    if needle not in source:
        raise AssertionError(f"story search contract missing {needle}")
if "api." in source:
    raise AssertionError("sidebar search must filter the loaded local story list without per-keystroke API calls")
print("story search contract: PASS")
