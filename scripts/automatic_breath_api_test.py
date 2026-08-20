from __future__ import annotations

import base64
import json
import shutil
import struct
import time
import wave
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(r"D:\StoryDriver")
API = "http://127.0.0.1:8001"
TEMP = ROOT / "backend" / "data" / "temp" / "automatic_breath_api_test"
VOICE_SAMPLE = ROOT / "tts_engines" / "samples" / "qwen_customvoice_neutral.wav"
VOICE_TRANSCRIPT = "Elena opened the notebook and read the first careful line to the room."


def request_json(path: str, payload: dict | None = None, method: str | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{API}{path}",
        data=body,
        method=method or ("POST" if body else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def make_breath(path: Path) -> None:
    rate = 24_000
    frames = int(0.68 * rate)
    state = 0x2468ACE
    values: list[int] = []
    for index in range(frames):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        noise = ((state / 0xFFFFFFFF) * 2.0) - 1.0
        envelope = min(1.0, index / (rate * 0.08), (frames - index) / (rate * 0.18))
        values.append(int(noise * max(0.0, envelope) * 2800))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(struct.pack(f"<{len(values)}h", *values))


def main() -> int:
    TEMP.mkdir(parents=True, exist_ok=True)
    voice_id: str | None = None
    try:
        created = request_json(
            "/tts/custom-voices",
            {
                "display_name": f"Breath API Test {int(time.time() * 1000)}",
                "language": "English",
                "authorization_confirmed": True,
                "dominant_speaker_confirmed": True,
                "user_notes": "Disposable synthetic local breath API test.",
                "references": {
                    "normal": {
                        "filename": VOICE_SAMPLE.name,
                        "audio_base64": base64.b64encode(VOICE_SAMPLE.read_bytes()).decode("ascii"),
                        "transcript": VOICE_TRANSCRIPT,
                    }
                },
                "build_prompt": False,
            },
        )
        voice_id = created["voice"]["id"]
        breath_path = TEMP / "shaky.wav"
        make_breath(breath_path)
        payload = {
            "filename": breath_path.name,
            "audio_base64": base64.b64encode(breath_path.read_bytes()).decode("ascii"),
            "authorization_confirmed": True,
            "isolated_breath_confirmed": True,
        }
        first = request_json(f"/tts/custom-voices/{voice_id}/breaths/shaky_inhale", payload, "PUT")
        second = request_json(f"/tts/custom-voices/{voice_id}/breaths/shaky_inhale", payload, "PUT")
        breath = second["breath"]
        if first["breath"]["revision"] != 1 or breath["revision"] != 2:
            raise AssertionError("Breath replace revisions are wrong.")
        if not breath["authorization_confirmed"] or not breath["isolated_breath_confirmed"]:
            raise AssertionError("Authorization metadata was not retained.")
        audio_request = Request(f"{API}{breath['audio_url']}")
        with urlopen(audio_request, timeout=30) as response:
            audio_bytes = response.read()
            if response.headers.get_content_type() not in {"audio/wav", "audio/x-wav"}:
                raise AssertionError("Breath preview did not return WAV audio.")
        preview = TEMP / "preview.wav"
        preview.write_bytes(audio_bytes)
        with wave.open(str(preview), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getframerate() != 24_000 or handle.getsampwidth() != 2:
                raise AssertionError("Breath preview is not mono 24 kHz PCM16.")
        listed = request_json("/tts/custom-voices")
        selected = next(voice for voice in listed["voices"] if voice["id"] == voice_id)
        if selected["breaths"]["shaky_inhale"]["checksum"] != breath["checksum"]:
            raise AssertionError("Custom voice list lost breath metadata.")
        removed = request_json(f"/tts/custom-voices/{voice_id}/breaths/shaky_inhale", method="DELETE")
        if not removed["ok"]:
            raise AssertionError("Breath removal failed.")
        print(
            json.dumps(
                {
                    "status": "pass",
                    "revision_sequence": [1, 2],
                    "duration_seconds": breath["duration_seconds"],
                    "preview_bytes": len(audio_bytes),
                    "cache_invalidated": second["cache_invalidated"],
                    "removed": True,
                },
                indent=2,
            )
        )
        return 0
    finally:
        if voice_id:
            try:
                request_json(f"/tts/custom-voices/{voice_id}?remove_generated_audio=true", method="DELETE")
            except Exception:
                pass
        shutil.rmtree(TEMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
