from pathlib import Path
import sys


root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))

from app.memory.characters import (  # noqa: E402
    character_name_evidence,
    filter_detected_characters,
    has_character_evidence,
)


invalid_cases = {
    "Wait": '"Wait here," Mara said. "Wait until the lamps go out."',
    "Stop": '"Stop at the door," Mara said. "Stop before dawn."',
    "Look": '"Look toward the bridge." Later she added, "Look once more."',
    "Listen": '"Listen carefully," Mara warned. "Listen for the bell."',
}
for candidate, scene in invalid_cases.items():
    evidence = character_name_evidence(scene, candidate)
    if has_character_evidence(scene, candidate):
        raise AssertionError(f"imperative {candidate} was accepted as a character: {evidence}")
    detected = filter_detected_characters(
        scene,
        [{"name": candidate, "major": True, "confidence": 0.99}],
        [],
    )
    if detected:
        raise AssertionError(f"detector confidence bypassed context validation for {candidate}")

valid_names = ("Hope", "May", "Will", "Rose", "Grace", "Faith", "Hunter")
for candidate in valid_names:
    scene = f'{candidate} checked the latch. "Not yet," {candidate} said, and stepped away.'
    if not has_character_evidence(scene, candidate):
        raise AssertionError(f"legitimate character name {candidate} was rejected")
    detected = filter_detected_characters(
        scene,
        [{"name": candidate, "major": True, "confidence": 0.9}],
        [],
    )
    if [item["name"] for item in detected] != [candidate]:
        raise AssertionError(f"legitimate character {candidate} did not survive filtering")

introduced = "The courier was named Wait. Wait said the eastern road was closed."
if not has_character_evidence(introduced, "Wait"):
    raise AssertionError("explicitly introduced action-word name was rejected")

print("invalid character extraction contract: PASS")
