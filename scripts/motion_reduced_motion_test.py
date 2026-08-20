from pathlib import Path


root = Path(__file__).resolve().parents[1]
styles = (root / "frontend/src/styles.css").read_text(encoding="utf-8")
settings = (root / "frontend/src/services/displaySettings.js").read_text(encoding="utf-8")
schema = (root / "backend/app/schemas.py").read_text(encoding="utf-8")
for needle in ('data-motion="off"', "prefers-reduced-motion: reduce", 'data-motion="full"'):
    if needle not in styles:
        raise AssertionError(f"motion CSS missing {needle}")
if 'motion: "subtle"' not in settings or 'Literal["off", "subtle", "full"]' not in schema:
    raise AssertionError("motion setting contract is incomplete")
print("motion/reduced-motion contract: PASS")
