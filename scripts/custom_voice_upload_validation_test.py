from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
import wave
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(r"D:\StoryDriver")
API = "http://127.0.0.1:8001"
QWEN_PYTHON = ROOT / "tts_engines" / "qwen3_tts" / "venv" / "Scripts" / "python.exe"
SAMPLE = ROOT / "tts_engines" / "samples" / "qwen_customvoice_neutral.wav"
SAMPLE_TEXT = "Elena opened the notebook and read the first careful line to the room."
TEMP = ROOT / "backend" / "data" / "temp" / "custom_voice_upload_validation"


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


def create_voice(label: str, source: Path, transcript: str) -> str:
    result = request_json(
        "/tts/custom-voices",
        {
            "display_name": f"Upload Validation {label} {uuid4().hex[:8]}",
            "language": "English",
            "authorization_confirmed": True,
            "dominant_speaker_confirmed": True,
            "user_notes": "Disposable synthetic local upload format test.",
            "references": {
                "normal": {
                    "filename": source.name,
                    "audio_base64": base64.b64encode(source.read_bytes()).decode("ascii"),
                    "transcript": transcript,
                }
            },
            "build_prompt": False,
        },
    )
    voice = result["voice"]
    normalized = Path(voice["references"]["normal"]["path"])
    with wave.open(str(normalized), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 24000
        assert handle.getsampwidth() == 2
    return str(voice["id"])


def main() -> int:
    TEMP.mkdir(parents=True, exist_ok=True)
    flac = TEMP / "synthetic_reference.flac"
    mp3_path: Path | None = None
    mp3_manifest: Path | None = None
    voice_ids: list[str] = []
    try:
        subprocess.run(
            [
                str(QWEN_PYTHON),
                "-c",
                "import soundfile as sf,sys; a,s=sf.read(sys.argv[1]); sf.write(sys.argv[2],a,s,format='FLAC')",
                str(SAMPLE),
                str(flac),
            ],
            check=True,
            cwd=ROOT,
        )
        unique_text = f"This is a synthetic local MP3 upload validation recording. Marker {uuid4().hex[:8]}."
        mp3_response = request_json(
            "/tts/synthesize",
            {
                "text": unique_text,
                "voice": "af_heart",
                "speed": 1.0,
                "provider": "kokoro",
                "voice_profile_id": "natural_female_narrator",
                "follow_mode": "off",
            },
        )
        mp3_path = ROOT / "backend" / "data" / "generated_audio" / Path(mp3_response["audio_url"]).name
        if mp3_response.get("cache_key"):
            mp3_manifest = mp3_path.parent / f"tts_manifest_{mp3_response['cache_key']}.json"
        for label, source, transcript in (
            ("WAV", SAMPLE, SAMPLE_TEXT),
            ("FLAC", flac, SAMPLE_TEXT),
            ("MP3", mp3_path, unique_text),
        ):
            voice_ids.append(create_voice(label, source, transcript))
        print("PASS: WAV, FLAC, and MP3 normalized locally to mono 24 kHz PCM16 WAV.")
        return 0
    finally:
        for voice_id in voice_ids:
            try:
                request_json(f"/tts/custom-voices/{voice_id}?remove_generated_audio=true", method="DELETE")
            except Exception:
                pass
        for path in (mp3_path, mp3_manifest):
            if path:
                path.unlink(missing_ok=True)
        shutil.rmtree(TEMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
