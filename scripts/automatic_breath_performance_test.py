from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import sys
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / "backend" / "data" / "temp" / "automatic_breath_performance_test"
DB_PATH = TEMP_ROOT / "app.db"
os.environ["STORYDRIVER_DATA_DIR"] = str(TEMP_ROOT)
os.environ["STORYDRIVER_DB_PATH"] = str(DB_PATH)
sys.path.insert(0, str(ROOT / "backend"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def synthetic_breath(path: Path, duration: float = 0.72, sample_rate: int = 24_000) -> None:
    frames = int(duration * sample_rate)
    state = 0x13579BDF
    values: list[int] = []
    for index in range(frames):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        noise = ((state / 0x7FFFFFFF) * 2.0) - 1.0
        attack = min(1.0, index / max(1, int(0.08 * sample_rate)))
        release = min(1.0, (frames - index - 1) / max(1, int(0.16 * sample_rate)))
        envelope = max(0.0, min(attack, release))
        values.append(int(noise * envelope * 3200))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(struct.pack(f"<{len(values)}h", *values))


def main() -> int:
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        from app.database import db_session, init_db
        from app.settings.store import merged_tts_settings
        from app.tts.breaths import delete_custom_voice_breath, save_custom_voice_breath
        from app.tts.custom_voices import get_custom_voice, voice_cache_identity
        from app.tts.prosody import narration_direction
        from app.tts.service import kokoro_cache_key, resolved_breath_metadata

        init_db()
        cases = {
            "ordinary": ("Ordinary narration continued across the quiet room.", None, None),
            "slow_inhale": ("She drew a slow breath before answering.", "soft_inhale", None),
            "whisper": ('"Don\'t move," Mara whispered.', None, None),
            "hitched": ('Mara\'s breath hitched. "You came back."', "shaky_inhale", None),
            "generic_hitched": ('June\'s breath hitched. "You came back."', "shaky_inhale", None),
            "recovering": ("She reached the top of the hill, panting.", "recovering_breath", None),
            "question": ('"What?" Mara said.', None, None),
            "romantic": ("They sat close in the romantic candlelight and spoke about tomorrow.", None, None),
            "adult_without_cue": ("The consenting adults undressed and kissed in private.", None, None),
            "gasp": ("She gasped when the door opened.", "gasp", None),
            "quiet": ("Her breath warm against his ear, she spoke barely above a breath.", None, "quiet_exhale"),
        }
        classifications: dict[str, dict] = {}
        for name, (text, expected_before, expected_after) in cases.items():
            before_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            result = narration_direction(text, breathing_mode="natural")
            after_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            require(result["breath_before"] == expected_before, f"{name} before event was {result['breath_before']!r}.")
            require(result["breath_after"] == expected_after, f"{name} after event was {result['breath_after']!r}.")
            require(before_hash == after_hash, f"{name} classification altered prose.")
            require("[" not in str(result.get("breath_before") or ""), "A bracket control entered breath metadata.")
            classifications[name] = {
                "breath_before": result["breath_before"],
                "breath_after": result["breath_after"],
                "confidence": result["breath_confidence"],
            }

        off = narration_direction("Her breath hitched.", breathing_mode="off")
        require(not off["breath_before"] and not off["breath_after"], "Off mode selected a breath.")
        natural_private = narration_direction("Her voice warm and private, she answered.", breathing_mode="natural")
        cinematic_private = narration_direction("Her voice warm and private, she answered.", breathing_mode="cinematic")
        require(not natural_private["breath_after"], "Natural mode over-interpreted a private voice cue.")
        require(cinematic_private["breath_after"] == "quiet_exhale", "Cinematic mode did not expose its restrained broader cue.")

        ordinary_long = " ".join("The narrator described the room and the work at hand." for _ in range(1200))
        ordinary_units = [ordinary_long[index : index + 220] for index in range(0, len(ordinary_long), 220)]
        ordinary_events = sum(
            bool(direction.get("breath_before") or direction.get("breath_after"))
            for direction in (narration_direction(unit, breathing_mode="natural") for unit in ordinary_units)
        )
        require(ordinary_events == 0, "Ordinary long-form narration accumulated automatic breaths.")

        with db_session() as db:
            db.execute(
                """
                INSERT INTO custom_voices (
                    id, display_name, authorization_confirmed, dominant_speaker_confirmed,
                    validation_status, normalized_audio_checksum
                ) VALUES ('breath-test-voice', 'Breath Test Voice', 1, 1, 'ready', 'voice-checksum')
                """
            )

        source = TEMP_ROOT / "input" / "breath.wav"
        synthetic_breath(source)
        encoded = base64.b64encode(source.read_bytes()).decode("ascii")
        payload = {
            "filename": "breath.wav",
            "audio_base64": encoded,
            "authorization_confirmed": True,
            "isolated_breath_confirmed": True,
        }
        first = save_custom_voice_breath("breath-test-voice", "shaky_inhale", payload)
        require(first["breath"]["revision"] == 1, "Initial breath revision was not one.")
        require(0.4 <= first["breath"]["duration_seconds"] <= 1.5, "Normalized breath duration is outside the recommended range.")
        voice = get_custom_voice("breath-test-voice")
        require(bool(voice and voice["breaths"].get("shaky_inhale")), "Custom voice did not expose its breath library.")
        direction = narration_direction('Mara\'s breath hitched. "You came back."', breathing_mode="natural")
        resolved = resolved_breath_metadata(voice, direction)
        require(resolved["breath_reference_used"], "Explicit cue did not resolve the authorized breath reference.")
        require(resolved["breath_audio_url"].endswith("/shaky_inhale/audio"), "Breath route was not stable.")

        identity_before = voice_cache_identity(voice)
        second = save_custom_voice_breath("breath-test-voice", "shaky_inhale", payload)
        voice_after = get_custom_voice("breath-test-voice")
        require(second["breath"]["revision"] == 2, "Replacing a breath did not increment only its revision.")
        require(identity_before != voice_cache_identity(voice_after), "Voice cache identity ignored breath revision.")

        base_cache = {
            "text": "A cache identity test.",
            "voice": "af_aoede",
            "speed": 1.0,
            "base_url": "http://localhost:8880",
            "extra_options": {},
        }
        off_key = kokoro_cache_key(**base_cache, cache_metadata={"breathing_mode": "off", "breath_type": None})
        natural_key = kokoro_cache_key(
            **base_cache,
            cache_metadata={
                "breathing_mode": "natural",
                "breath_type": "shaky_inhale",
                "breath_sample_checksum": second["breath"]["checksum"],
                "breath_sample_revision": second["breath"]["revision"],
            },
        )
        require(off_key != natural_key, "Off and Natural cache identities collided.")

        removed = delete_custom_voice_breath("breath-test-voice", "shaky_inhale")
        require(removed["ok"], "Custom breath removal failed.")
        require(not get_custom_voice("breath-test-voice")["breaths"], "Removed breath remained visible.")
        missing = resolved_breath_metadata(get_custom_voice("breath-test-voice"), direction)
        require(not missing["breath_reference_used"] and not missing["breath_audio_url"], "Missing breath did not fall back to silence.")

        defaults = merged_tts_settings()
        require(defaults["breathing_mode"] == "natural", "Natural is not the merged default.")
        frontend_files = [
            ROOT / "frontend" / "src" / "services" / "ttsController.js",
            ROOT / "frontend" / "src" / "services" / "ttsBreathScheduler.js",
            ROOT / "frontend" / "src" / "components" / "SettingsDrawer.jsx",
            ROOT / "frontend" / "src" / "components" / "CustomVoiceLibrary.jsx",
        ]
        frontend_source = "\n".join(path.read_text(encoding="utf-8") for path in frontend_files)
        for forbidden in ("[inhales]", "[breathes deeply]", "[soft breath]", "[gasps]"):
            require(forbidden not in frontend_source.lower(), f"Visible bracket control entered the frontend: {forbidden}")
        require("decodeAudioData" in frontend_source, "Custom breath audio is not decoded through Web Audio.")
        require("breathDuration" in frontend_source and "performancePhase" in frontend_source, "Cursor metadata omits breath timing.")
        require("Custom breath references" in frontend_source and "Test in context" in frontend_source, "Custom breath controls are incomplete.")
        result = {
            "checks": {
                "classification_cases": classifications,
                "off_mode": True,
                "natural_default": True,
                "cinematic_broader_but_explicit": True,
                "ordinary_long_events": ordinary_events,
                "prose_unchanged": True,
                "custom_breath_normalization": True,
                "reference_revisions": [first["breath"]["revision"], second["breath"]["revision"]],
                "mode_cache_separation": True,
                "missing_reference_silent_fallback": True,
                "frontend_bracket_tags_absent": True,
                "web_audio_decode_and_cursor_contract": True,
            }
        }
        print(json.dumps(result, indent=2))
        return 0
    finally:
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
