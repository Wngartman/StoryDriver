from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "TTS_LATENCY_REPORT.md"
BACKEND_URL = "http://localhost:8001"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_json(method: str, path: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{BACKEND_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw or "{}")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} {path}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach StoryDriver backend at {BACKEND_URL}: {error}") from error


def split_chunks(text: str, max_chars: int = 1200) -> list[str]:
    target = max(400, min(6000, int(max_chars or 1200)))
    normalized = (text or "").replace("\r", "").strip()
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+|\n{2,}", normalized) if piece.strip()]
    groups: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(f"{current} {piece}") <= target:
            current = f"{current} {piece}"
        else:
            groups.append(current)
            current = piece
    if current:
        groups.append(current)
    return groups or ([normalized] if normalized else [])


def sample_text(minutes: int) -> str:
    opening = (
        f"This is the {minutes} minute StoryDriver narration latency sample, written to avoid cross-test cache reuse. "
    )
    sentence = (
        "Mara crossed the muddy yard with her cloak tight at her throat, listening to rain tick "
        "against the shuttered farmhouse while the sisters argued softly over the rescue plan. "
    )
    # About 155 spoken words per minute, with roughly 30 words in the sample sentence.
    repeat = max(2, int((minutes * 155) / 30))
    paragraphs = []
    for index in range(repeat):
        paragraphs.append(sentence)
        if index % 7 == 6:
            paragraphs.append("\n\n")
    return f"{opening}{''.join(paragraphs)}".strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def estimate_speech_duration(text: str, speed: float = 1.0) -> float:
    rate = max(80.0, 170.0 * (speed or 1.0))
    return max(1.5, (word_count(text) / rate) * 60.0)


def estimated_transition_gap(chunks: list[str], synth_times: list[float], speed: float, prebuffer: int) -> float:
    if len(chunks) < 2 or len(synth_times) < 2:
        return 0.0
    durations = [estimate_speech_duration(chunk, speed) for chunk in chunks]
    max_gap = 0.0
    for index in range(1, min(len(chunks), len(synth_times))):
        available_playback = sum(durations[max(0, index - max(1, prebuffer)):index])
        max_gap = max(max_gap, max(0.0, synth_times[index] - available_playback))
    return max_gap


def synthesize(text: str, *, voice: str | None, speed: float, label: str, index: int | None, count: int | None) -> tuple[dict, float]:
    started = time.perf_counter()
    response = request_json(
        "POST",
        "/tts/synthesize",
        {
            "text": text,
            "provider": "kokoro",
            "voice": voice,
            "speed": speed,
            "narration_job_id": f"tts-smoke-{label}",
            "chunk_index": index,
            "chunk_count": count,
            "text_hash": f"tts-smoke-{label}",
        },
        timeout=180.0,
    )
    return response, time.perf_counter() - started


def append_report(lines: list[str]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not REPORT_PATH.exists():
        REPORT_PATH.write_text("# TTS Latency Report\n", encoding="utf-8")
    with REPORT_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    global BACKEND_URL
    parser = argparse.ArgumentParser(description="Measure StoryDriver Kokoro TTS chunk latency.")
    parser.add_argument("--backend", default=BACKEND_URL)
    parser.add_argument("--first-chunk-only", action="store_true", help="Skip full cache synthesis for long samples.")
    args = parser.parse_args()
    BACKEND_URL = args.backend.rstrip("/")

    status = request_json("GET", "/tts/status", timeout=10.0)
    kokoro = status.get("kokoro") or {}
    if not kokoro.get("reachable"):
        print("Kokoro is not reachable through StoryDriver /tts/status.")
        append_report(
            [
                f"\n## {utc_now()} - TTS latency smoke test",
                "- result: skipped",
                "- reason: Kokoro is not reachable through StoryDriver",
            ]
        )
        return 2

    voice = status.get("last_voice_used") or None
    speed = 1.0
    chunk_size = int(status.get("tts_chunk_size") or 1200)
    prebuffer_chunks = max(1, min(3, int(status.get("tts_prebuffer_chunks") or 2)))
    cases = [("short", 0), ("3_min", 3), ("9_min", 9), ("12_min", 12)]
    report = [
        f"\n## {utc_now()} - TTS latency smoke test",
        f"- kokoro_base_url: {kokoro.get('base_url')}",
        f"- speech_endpoint: {kokoro.get('speech_endpoint')}",
        f"- device: {kokoro.get('device') or 'device not reported by Kokoro'}",
        f"- chunk_size: {chunk_size}",
        f"- prebuffer_chunks: {prebuffer_chunks}",
    ]

    for label, minutes in cases:
        text = "StoryDriver narration smoke test. The first audio should be ready quickly." if minutes == 0 else sample_text(minutes)
        chunks = split_chunks(text, chunk_size)
        if not chunks:
            continue
        first_response, first_seconds = synthesize(
            chunks[0],
            voice=voice,
            speed=speed,
            label=label,
            index=0 if len(chunks) > 1 else None,
            count=len(chunks) if len(chunks) > 1 else None,
        )
        total_seconds = first_seconds
        synthesized = 1
        synth_times = [first_seconds]
        if not args.first_chunk_only:
            for index, chunk in enumerate(chunks[1:], start=1):
                _, elapsed = synthesize(
                    chunk,
                    voice=voice,
                    speed=speed,
                    label=label,
                    index=index,
                    count=len(chunks),
                )
                total_seconds += elapsed
                synthesized += 1
                synth_times.append(elapsed)
        _, cached_seconds = synthesize(
            chunks[0],
            voice=voice,
            speed=speed,
            label=label,
            index=0 if len(chunks) > 1 else None,
            count=len(chunks) if len(chunks) > 1 else None,
        )
        report.extend(
            [
                f"- {label}_chars: {len(text)}",
                f"- {label}_chunks: {len(chunks)}",
                f"- {label}_estimated_whole_duration_seconds: {sum(estimate_speech_duration(chunk, speed) for chunk in chunks):.1f}",
                f"- {label}_first_chunk_seconds: {first_seconds:.3f}",
                f"- {label}_first_chunk_cached: {first_response.get('cached')}",
                f"- {label}_cached_replay_seconds: {cached_seconds:.3f}",
                f"- {label}_full_cache_seconds: {total_seconds:.3f}",
                f"- {label}_average_chunk_synthesis_seconds: {(sum(synth_times) / len(synth_times)):.3f}",
                f"- {label}_estimated_max_transition_gap_prebuffer_1_seconds: {estimated_transition_gap(chunks, synth_times, speed, 1):.3f}",
                f"- {label}_estimated_max_transition_gap_prebuffer_2_seconds: {estimated_transition_gap(chunks, synth_times, speed, 2):.3f}",
                f"- {label}_estimated_max_transition_gap_prebuffer_3_seconds: {estimated_transition_gap(chunks, synth_times, speed, 3):.3f}",
                f"- {label}_estimated_max_transition_gap_current_prebuffer_seconds: {estimated_transition_gap(chunks, synth_times, speed, prebuffer_chunks):.3f}",
                f"- {label}_chunks_synthesized: {synthesized}",
            ]
        )
        print(f"{label}: first chunk {first_seconds:.2f}s, cached replay {cached_seconds:.2f}s, chunks {len(chunks)}")

    append_report(report)
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
