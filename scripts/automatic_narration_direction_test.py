from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(r"D:\StoryDriver")
BACKEND = ROOT / "backend"
WORKER = ROOT / "tts_engines" / "qwen3_tts" / "service" / "app.py"
QWEN_PYTHON = ROOT / "tts_engines" / "qwen3_tts" / "venv" / "Scripts" / "python.exe"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def worker_cache_checks() -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("storydriver_qwen_worker_direction_test", WORKER)
    require(spec is not None and spec.loader is not None, "Could not import Qwen worker.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.SynthesizeRequest.model_rebuild()
    base = {
        "text": '"Stay here," Mara said.',
        "voice": "Test Voice",
        "profile_id": "custom:test",
        "custom_voice_id": "test",
        "voice_prompt_path": str(ROOT / "backend" / "data" / "voices" / "prompts" / "test.pt"),
        "voice_prompt_checksum": "normal-checksum",
        "voice_prompt_revision": 3,
        "pronunciation_revision": "pronunciation-v1",
        "normalization_version": "normalization-v1",
    }
    keys: dict[str, str] = {}
    for style in ("normal", "soft", "whisper", "heightened"):
        request = module.SynthesizeRequest(
            **base,
            style_reference=style,
            style_classification=style,
            intensity_bucket="low" if style == "normal" else "medium",
        )
        keys[style] = module.Runtime.cache_key(request)
    require(len(set(keys.values())) == 4, "Normal, soft, whisper, and heightened cache keys collided.")

    same_reference = module.SynthesizeRequest(
        **base,
        style_reference="normal",
        style_classification="whisper",
        intensity_bucket="high",
    )
    require(
        module.Runtime.cache_key(same_reference) != keys["normal"],
        "Style classification did not separate cache entries when references matched.",
    )
    instructed = same_reference.model_copy(update={"generation_instruction": "restrained whisper"})
    require(
        module.Runtime.cache_key(instructed) != module.Runtime.cache_key(same_reference),
        "Generation instruction is missing from cache identity.",
    )
    revised = same_reference.model_copy(update={"voice_prompt_revision": 4})
    require(
        module.Runtime.cache_key(revised) != module.Runtime.cache_key(same_reference),
        "Custom voice revision is missing from cache identity.",
    )
    return {"style_keys": keys, "same_reference_key": module.Runtime.cache_key(same_reference)}


def main() -> int:
    if "--worker-cache" in sys.argv:
        print(json.dumps(worker_cache_checks(), indent=2))
        return 0

    sys.path.insert(0, str(BACKEND))
    from app.tts.custom_voices import prompt_for_style
    from app.tts.prosody import narration_direction

    cases = (
        ('"Stay here," Mara said.', "normal"),
        ('"Stay here," Mara said softly.', "soft"),
        ('"Don\'t move," Mara whispered.', "whisper"),
        ('"Run!" Mara shouted.', "heightened"),
        ('"I\'m fine," she said, though her voice broke.', "distressed"),
        ('She leaned closer. "I missed you."', "soft"),
        ("The dark corridor seemed to narrow around them.", "normal"),
        ('"What?" she said.', "normal"),
        ('"Stop!" she said.', "normal"),
        ('"Wait," she said in a clipped voice.', "tense"),
        ('"There is nothing left," he said in a hollow voice.', "somber"),
        ('"Come closer," she murmured against his ear.', "intimate"),
    )
    observed: list[dict[str, object]] = []
    forbidden_tags = ("[whisper]", "[softly]", "[angry]", "[short pause]")
    for source, expected in cases:
        before = source.encode("utf-8")
        result = narration_direction(source)
        require(result["style"] == expected, f"Expected {expected} for {source!r}, received {result}.")
        require(source.encode("utf-8") == before, "Narration classification changed source prose bytes.")
        require(result["audible_text_unchanged"] is True, "Classifier did not guarantee unchanged audible text.")
        require(not any(tag in source.lower() for tag in forbidden_tags), "A control tag entered source prose.")
        require(0 <= int(result["pause_after_ms"]) <= 1200, "Pause metadata exceeded its bound.")
        observed.append(
            {
                "style": result["style"],
                "emotion": result["emotion"],
                "intensity_bucket": result["intensity_bucket"],
                "pause_after_ms": result["pause_after_ms"],
                "source_cue": result["source_cue"],
            }
        )

    require(narration_direction('"Stop!" she said.')["style"] == "normal", "Exclamation mark triggered shouting.")
    require(
        narration_direction("The dark corridor seemed to narrow around them.")["style"] != "whisper",
        "Dark narration triggered whispering.",
    )
    require(
        narration_direction('"Don\'t move," Mara whispered.')["pause_after_ms"] == 250,
        "Explicit whisper did not receive the bounded delivery pause.",
    )
    require(
        narration_direction("End of paragraph.", paragraph_break_after=True)["pause_after_ms"] == 380,
        "Paragraph pause was not derived.",
    )
    require(
        narration_direction("End of scene.", scene_break_after=True)["pause_after_ms"] == 900,
        "Scene pause was not derived.",
    )

    fake_voice = {
        "prompt_paths": {"normal": "normal.pt", "soft": "soft.pt"},
        "prompt_checksums": {"normal": "n", "soft": "s"},
    }
    require(prompt_for_style(fake_voice, "whisper")[2] == "soft", "Whisper did not fall back to soft reference.")
    require(prompt_for_style(fake_voice, "heightened")[2] == "normal", "Heightened did not fall back safely.")
    require(prompt_for_style(fake_voice, "distressed")[2] == "soft", "Distressed did not select a close reference.")

    worker_source = WORKER.read_text(encoding="utf-8")
    require("librosa.effects.pitch_shift" not in worker_source, "Worker contains crude pitch shifting.")
    require("generation_instruction\": request.generation_instruction" in worker_source, "Worker cache omits instruction identity.")
    completed = subprocess.run(
        [str(QWEN_PYTHON), str(Path(__file__)), "--worker-cache"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    require(completed.returncode == 0, f"Worker cache checks failed: {completed.stderr or completed.stdout}")

    frontend_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend" / "src").rglob("*.js")
    ) + "\n" + "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend" / "src").rglob("*.jsx")
    )
    for tag in forbidden_tags:
        require(tag not in frontend_source.lower(), f"Visible narration control tag remains in frontend source: {tag}")

    print(json.dumps({"status": "pass", "classification_cases": observed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
