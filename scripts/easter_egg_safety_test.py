from pathlib import Path


root = Path(__file__).resolve().parents[1]
brand = (root / "frontend/src/components/BrandMark.jsx").read_text(encoding="utf-8")
top = (root / "frontend/src/components/TopBar.jsx").read_text(encoding="utf-8")
combined = brand + top
if combined.count("The page is listening.") != 1:
    raise AssertionError("Ink Path phrase must exist exactly once")
for needle in ("window.setTimeout(trigger, 2000)", "titleClickTimesRef.current.length < 5", "setShowConstellation(true)"):
    if needle not in combined:
        raise AssertionError(f"Easter egg trigger missing {needle}")
for forbidden in ("generateScene(", "saveUiSettings(", "updateSessionTitle(", "deleteSession(", "Audio("):
    if forbidden in combined:
        raise AssertionError(f"Easter egg code performs unsafe action: {forbidden}")
if combined.count("createPortal(") != 2:
    raise AssertionError("exactly two local Easter egg overlays are required")
print("Easter egg safety contract: PASS")
