from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from urllib import error, request


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "KOKORO_REALISM_REPORT.md"
CAPABILITY_REPORT_PATH = ROOT / "backend" / "data" / "logs" / "KOKORO_CAPABILITY_REPORT.md"
BACKEND_URL = "http://localhost:8001"
KOKORO_URL = "http://localhost:8880"

SAMPLES = {
    "preview": (
        "The lantern guttered in the cold draft. Elara lowered her voice. "
        "'Wait until the ridge goes dark,' she said. Beyond the barn, the valley held its breath."
    ),
    "dialogue": (
        "'You heard it too,' Mara said. 'Tell me you heard it.' "
        "Jonas did not answer at once. The old stairwell clicked softly behind them."
    ),
    "quiet": (
        "She folded the letter twice, slowly, as if the paper might remember the hand that wrote it. "
        "For a while, the room was only rain and breath."
    ),
    "action": (
        "The door burst inward. Branna ducked under the axe, drove her shoulder into the raider's ribs, "
        "and felt the floorboards shudder beneath them."
    ),
    "invented_names": (
        "Kaelen watched Miri cross the causeway toward Aurelith. Aoede's old song trembled in the rafters."
    ),
    "modern": (
        "Nina muted her phone and stared across the late-night diner. 'Don't text him back,' Caleb said. "
        "Outside, traffic hissed over the wet asphalt."
    ),
    "fantasy": (
        "The queen's outrider knelt beside the broken milestone. Candle smoke clung to her mail, "
        "and the map in her glove had gone dark at the edges."
    ),
}


def get_json(url: str, timeout: float = 10.0):
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict, timeout: float = 90.0):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def safe_get_json(url: str, timeout: float = 10.0):
    try:
        return get_json(url, timeout=timeout), None
    except (OSError, error.URLError, json.JSONDecodeError) as exc:
        return None, str(exc)


def openapi_speech_fields(openapi: dict | None) -> list[str]:
    if not openapi:
        return []
    components = openapi.get("components", {}).get("schemas", {})
    speech = components.get("OpenAISpeechRequest", {})
    properties = speech.get("properties", {}) if isinstance(speech, dict) else {}
    return sorted(properties.keys())


def openapi_mentions(openapi: dict | None, terms: list[str]) -> dict[str, bool]:
    raw = json.dumps(openapi or {}, ensure_ascii=False).lower()
    return {term: term.lower() in raw for term in terms}


def write_report(lines: list[str]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_capability_report(lines: list[str]) -> None:
    CAPABILITY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CAPABILITY_REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate local Kokoro narration realism samples.")
    parser.add_argument("--backend-url", default=BACKEND_URL)
    parser.add_argument("--kokoro-url", default=KOKORO_URL)
    parser.add_argument("--profile", default="natural_female_narrator")
    parser.add_argument("--voice", default=None)
    args = parser.parse_args()

    voices_payload, voices_error = safe_get_json(f"{args.kokoro_url}/v1/audio/voices")
    openapi_payload, openapi_error = safe_get_json(f"{args.kokoro_url}/openapi.json")
    status_payload, status_error = safe_get_json(f"{args.backend_url}/tts/status")
    storydriver_voices, storydriver_voices_error = safe_get_json(f"{args.backend_url}/tts/voices?provider=kokoro")

    kokoro_voices = []
    if isinstance(voices_payload, dict):
        kokoro_voices = voices_payload.get("voices") or voices_payload.get("data") or []
    elif isinstance(voices_payload, list):
        kokoro_voices = voices_payload
    if not kokoro_voices and isinstance(storydriver_voices, dict):
        kokoro_voices = storydriver_voices.get("voices") or storydriver_voices.get("kokoro") or []
    female_voices = [voice for voice in kokoro_voices if isinstance(voice, str) and len(voice) > 2 and voice[1] == "f"]
    profiles = (storydriver_voices or {}).get("voice_profiles") or (status_payload or {}).get("tts_voice_profiles") or []
    profile = next((item for item in profiles if item.get("id") == args.profile), profiles[0] if profiles else {})
    selected_voice = args.voice or profile.get("voice_id") or "af_heart"
    selected_speed = float(profile.get("speed") or 0.95)
    speech_fields = openapi_speech_fields(openapi_payload)
    mentions = openapi_mentions(
        openapi_payload,
        ["speed", "response_format", "voice", "language", "phoneme", "timestamp", "word", "ssml", "stream"],
    )
    status_kokoro = (status_payload or {}).get("kokoro") or {}
    supported_options = status_kokoro.get("supported_options") or []
    capability_lines = [
        "# Kokoro Capability Report",
        "",
        "Local Kokoro/StoryDriver capability probe. No story content is sent outside the local backend/Kokoro service.",
        "",
        "## Endpoints",
        f"- Kokoro base URL: {args.kokoro_url}",
        f"- StoryDriver backend URL: {args.backend_url}",
        f"- Kokoro voices endpoint: {'ok' if voices_payload else voices_error}",
        f"- Kokoro OpenAPI: {'ok' if openapi_payload else openapi_error}",
        f"- StoryDriver TTS status: {'ok' if status_payload else status_error}",
        "",
        "## Voices",
        f"- Kokoro voice count: {len(kokoro_voices)}",
        f"- Female-coded voices: {', '.join(female_voices) or 'none detected'}",
        f"- Built-in profiles: {', '.join(str(item.get('display_name') or item.get('id') or 'profile') for item in profiles) or 'not loaded'}",
        "",
        "## Speech Request Support",
        f"- OpenAPI speech fields: {', '.join(speech_fields) or 'not detected'}",
        f"- StoryDriver gated optional Kokoro fields: {', '.join(supported_options) or 'none'}",
        f"- Speed field detected: {mentions.get('speed')}",
        f"- Response format field detected: {mentions.get('response_format')}",
        f"- Language field detected: {mentions.get('language')}",
        f"- Phoneme-related field detected: {mentions.get('phoneme')}",
        f"- Timestamp or word timing mentioned: {mentions.get('timestamp') or mentions.get('word')}",
        f"- SSML mentioned: {mentions.get('ssml')}",
        f"- Streaming mentioned: {mentions.get('stream')}",
        "",
        "## StoryDriver Usage",
        "- Current stable synthesis path uses local POST /v1/audio/speech.",
        "- StoryDriver sends stable voice/speed/format fields plus only optional Kokoro fields reported by the local API.",
        "- Pronunciation fixes are local text aliases applied before synthesis; saved scene text is not changed.",
        "- Exact word timestamps are not used unless a future Kokoro/aligner path exposes reliable word timing.",
    ]
    write_capability_report(capability_lines)

    sample_results = []
    for name, text in SAMPLES.items():
        payload = {
            "sample_text": text,
            "voice_profile_id": profile.get("id") or args.profile,
            "voice": selected_voice,
            "speed": selected_speed,
        }
        started = perf_counter()
        try:
            response = post_json(f"{args.backend_url}/tts/preview", payload)
            elapsed = perf_counter() - started
            sample_results.append(
                {
                    "name": name,
                    "ok": True,
                    "elapsed_seconds": round(elapsed, 3),
                    "cached": response.get("cached"),
                    "audio_url": response.get("audio_url"),
                    "cache_key": response.get("cache_key"),
                    "voice": response.get("voice"),
                    "speed": response.get("speed"),
                }
            )
        except (OSError, error.URLError, json.JSONDecodeError) as exc:
            sample_results.append({"name": name, "ok": False, "error": str(exc)})

    cache_probe = None
    if sample_results and sample_results[0].get("ok"):
        started = perf_counter()
        try:
            response = post_json(
                f"{args.backend_url}/tts/preview",
                {
                    "sample_text": SAMPLES["preview"],
                    "voice_profile_id": profile.get("id") or args.profile,
                    "voice": selected_voice,
                    "speed": selected_speed,
                },
            )
            cache_probe = {
                "elapsed_seconds": round(perf_counter() - started, 3),
                "cached": response.get("cached"),
                "audio_url": response.get("audio_url"),
            }
        except (OSError, error.URLError, json.JSONDecodeError) as exc:
            cache_probe = {"error": str(exc)}

    lines = [
        "# Kokoro Realism Report",
        "",
        "Local-only narration realism probe. Story text is not uploaded; samples are generated by the local StoryDriver backend and Kokoro-FastAPI.",
        "",
        "## Capabilities",
        f"- Capability report: {CAPABILITY_REPORT_PATH}",
        f"- Kokoro voices endpoint: {'ok' if voices_payload else voices_error}",
        f"- Kokoro OpenAPI: {'ok' if openapi_payload else openapi_error}",
        f"- StoryDriver TTS status: {'ok' if status_payload else status_error}",
        f"- StoryDriver voices: {'ok' if storydriver_voices else storydriver_voices_error}",
        f"- Speech request fields: {', '.join(speech_fields) or 'not detected'}",
        f"- Kokoro voice count: {len(kokoro_voices)}",
        f"- Female-coded voices: {', '.join(female_voices[:24])}{' ...' if len(female_voices) > 24 else ''}",
        "",
        "## Recommended Profile",
        f"- Profile: {profile.get('display_name') or args.profile}",
        f"- Profile id: {profile.get('id') or args.profile}",
        f"- Voice: {selected_voice}",
        f"- Speed: {selected_speed}",
        f"- Recommended use: {profile.get('recommended_use') or 'general local narration'}",
        "",
        "## Samples",
    ]
    for result in sample_results:
        if result.get("ok"):
            lines.append(
                f"- {result['name']}: {result['audio_url']} voice={result['voice']} speed={result['speed']} "
                f"cached={result['cached']} elapsed={result['elapsed_seconds']}s"
            )
        else:
            lines.append(f"- {result['name']}: failed - {result.get('error')}")
    lines.extend(
        [
            "",
            "## Cache Probe",
            f"- Preview cache replay: {cache_probe}",
            "",
            "## Chunk Gap Note",
            "- Python can time synthesis and cache replay, but real chunk transition gaps are controlled by browser audio playback.",
            "- Use the app mini-player listening check for audible joins during long progressive narration.",
            "",
            "## Known Kokoro Limits",
            "- The stable StoryDriver path uses /v1/audio/speech with voice and speed.",
            "- StoryDriver does not currently use phoneme or timestamp paths for exact word alignment.",
            "- Realism improvements here come from voice selection, speed, prose normalization, punctuation pauses, chunk boundaries, and pronunciation aliases.",
        ]
    )
    write_report(lines)
    print(f"Wrote {REPORT_PATH}")
    for result in sample_results:
        if result.get("audio_url"):
            print(f"{result['name']}: {args.backend_url}{result['audio_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
