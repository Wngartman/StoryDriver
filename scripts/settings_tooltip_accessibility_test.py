from pathlib import Path


root = Path(__file__).resolve().parents[1]
help_source = (root / "frontend/src/components/SettingHelp.jsx").read_text(encoding="utf-8")
registry = (root / "frontend/src/settings/explanations.js").read_text(encoding="utf-8")
for needle in (
    "window.setTimeout(() => setOpen(true), 750)",
    "onFocus={() => show(false)}",
    "onClick={(event) =>",
    'role="tooltip"',
    "createPortal",
    'event.key === "Escape"',
    'aria-label={`About ${name}`}',
):
    if needle not in help_source:
        raise AssertionError(f"accessible setting help missing {needle}")
for key in ("temperature", "top_p", "seed", "memory_budget", "motion", "reading_width", "rotation"):
    if f"  {key}:" not in registry:
        raise AssertionError(f"central explanation registry missing {key}")
print("settings tooltip accessibility contract: PASS")
