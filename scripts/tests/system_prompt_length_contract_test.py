from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas import (  # noqa: E402
    ModelPresetCreate,
    ModelPresetUpdate,
    ModelSettings,
    ProsePromptPreviewRequest,
)


def main() -> int:
    large_prompt = "Long system prompt contract.\n" * 10_000
    assert len(large_prompt) > 250_000

    assert ModelSettings(system_prompt=large_prompt).system_prompt == large_prompt
    assert ProsePromptPreviewRequest(system_prompt_override=large_prompt).system_prompt_override == large_prompt
    assert ModelPresetCreate(name="Large prompt", system_prompt=large_prompt).system_prompt == large_prompt
    assert ModelPresetUpdate(system_prompt=large_prompt).system_prompt == large_prompt

    modal_source = (ROOT / "frontend" / "src" / "components" / "ModelSettingsModal.jsx").read_text(encoding="utf-8")
    system_prompt_section = modal_source.split('title="System Prompt"', 1)[1].split('title="Writing Engine"', 1)[0]
    assert "maxLength" not in system_prompt_section
    assert "StoryDriver does not cap its length" in system_prompt_section

    print(f"PASS: system prompt accepted {len(large_prompt):,} characters without an application cap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
