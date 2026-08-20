from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(r"D:\StoryDriver")
DB_PATH = ROOT / "backend" / "data" / "app.db"
AUDIO_DIR = ROOT / "backend" / "data" / "generated_audio"
ENDPOINT = "http://127.0.0.1:8001/tts/synthesize"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def story_digest(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT id, director_note, generated_text FROM scene_versions ORDER BY id"
    ).fetchall()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def post(payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    nonce = int(time.time() * 1000)
    job_id = f"automatic-narration-direction-test-{nonce}"
    source = f'"Do not touch synthetic parcel {nonce}," Mara whispered.'
    payload: dict[str, object] = {
        "text": source,
        "provider": "kokoro",
        "voice": "af_heart",
        "speed": 1.0,
        "voice_profile_id": "natural_female_narrator",
        "narration_job_id": job_id,
        "chunk_index": 0,
        "chunk_count": 1,
        "paragraph_break_after": True,
    }
    connection = sqlite3.connect(DB_PATH)
    before = story_digest(connection)
    generated_paths: set[Path] = set()
    try:
        first = post(payload)
        second = post(payload)
        require(first.get("narration_style") == "whisper", f"Unexpected style: {first}")
        require(first.get("source_cue") == "whispered", f"Unexpected source cue: {first}")
        require(first.get("pause_after_ms") == 380, f"Paragraph pause was not returned: {first}")
        require(first.get("style_reference") == "normal", "Kokoro selected-reference truth is missing.")
        require(first.get("breathing_mode") == "natural", "Natural breathing mode was not returned.")
        require(not first.get("breath_before") and not first.get("breath_after"), "Ordinary whispered dialogue gained a breath.")
        require(first.get("breath_reference_used") is False, "Kokoro claimed a custom breath reference.")
        require(first.get("cache_key") == second.get("cache_key"), "Identical narration did not reuse its key.")
        require(second.get("cached") is True, "Second narration request did not use cache.")
        require(source == payload["text"], "Request prose changed in memory.")

        row = connection.execute(
            """
            SELECT narration_style, narration_emotion, narration_intensity_bucket,
                   pause_after_ms, source_cue, style_reference, generation_instruction,
                   breathing_mode, breath_before, breath_after, breath_reference_checksum
            FROM narration_chunks WHERE job_id = ? AND chunk_index = 0
            """,
            (job_id,),
        ).fetchone()
        require(row is not None, "Narration-direction metadata was not persisted.")
        require(row[0] == "whisper" and row[4] == "whispered", f"Persisted direction is wrong: {row}")
        require(row[3] == 380 and row[5] == "normal", f"Persisted pause/reference is wrong: {row}")
        require(row[6] == "", "Unsupported generation instruction was persisted as effective.")
        require(row[7] == "natural" and not row[8] and not row[9] and not row[10], "Persisted breath truth is wrong.")

        cache_key = str(first["cache_key"])
        manifest = AUDIO_DIR / f"tts_manifest_{cache_key}.json"
        require(manifest.exists(), "Narration cache manifest is missing.")
        manifest_text = manifest.read_text(encoding="utf-8")
        require(source not in manifest_text, "Full prose leaked into the permanent cache manifest.")
        require('"normalized_tts_text"' not in manifest_text, "Manifest retained full normalized prose.")
        generated_paths.add(manifest)
        if first.get("audio_url"):
            generated_paths.add(AUDIO_DIR / Path(str(first["audio_url"])).name)

        after = story_digest(connection)
        require(before == after, "Saved scene prose changed during narration synthesis.")
        print(
            json.dumps(
                {
                    "status": "pass",
                    "narration_style": first.get("narration_style"),
                    "source_cue": first.get("source_cue"),
                    "pause_after_ms": first.get("pause_after_ms"),
                    "cache_reused": second.get("cached"),
                    "scene_prose_unchanged": before == after,
                    "metadata_persisted": True,
                    "full_prose_absent_from_manifest": True,
                },
                indent=2,
            )
        )
        return 0
    finally:
        connection.execute("DELETE FROM narration_chunks WHERE job_id = ?", (job_id,))
        connection.execute("DELETE FROM narration_jobs WHERE id = ?", (job_id,))
        connection.commit()
        connection.close()
        for path in generated_paths:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
