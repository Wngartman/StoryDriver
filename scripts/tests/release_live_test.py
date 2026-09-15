from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import time
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tools" / "release-test-data"
BASE = "http://127.0.0.1:8001"


def api(path, body=None, method=None, timeout=600):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def generate(story, note, mode="continue", target=None):
    started = time.perf_counter()
    request = urllib.request.Request(BASE + f"/sessions/{story}/generate-stream", data=json.dumps({"director_note": note, "mode": mode, "target_scene_id": target}).encode(), headers={"Content-Type": "application/json"})
    events, first_prose, stages = [], None, {}
    with urllib.request.urlopen(request, timeout=600) as response:
        for line in response:
            if not line.strip(): continue
            event = json.loads(line)
            elapsed = time.perf_counter() - started
            if event.get("type") == "error": raise RuntimeError(event)
            if event.get("type") in {"token", "content", "delta"} and first_prose is None: first_prose = elapsed
            if event.get("stage"): stages.setdefault(event["stage"], round(elapsed, 3))
            events.append(event)
    saved = next((event.get("scene") for event in reversed(events) if event.get("type") == "scene"), None)
    if not saved: raise AssertionError(f"No saved scene: {events[-2:]}")
    if target: assert saved["id"] == target, "Version operation changed scene identity"
    record = {"mode": mode, "seconds": round(time.perf_counter() - started, 3), "first_prose": first_prose, "stages": stages, "scene": saved}
    print(json.dumps({key: value for key, value in record.items() if key != "scene"}), flush=True)
    return record


def writing(model):
    settings = api("/settings/model")
    settings.update(provider="llama_cpp", provider_url="http://127.0.0.1:12345/v1", model=model, model_path=model, active_preset_id=None, writing_length_mode="beat", reasoning_mode="off")
    api("/settings/model", settings, "PUT")
    api("/models/library/gguf", {"path": model})
    rows, stories = [], []
    try:
        cases = [
            ("RELEASE TEST - apartment", "Modern apartment. Adult siblings Lena, 31, and Eva, 28, meet their new adult roommate Daniel, 32. It is 6:05 pm. Lena holds one brass key at the kitchen table, Eva is beside the open front door and Daniel has a suitcase. Only introductions and handing the key from Lena to Daniel occur. Stop before anyone leaves the apartment. No time skip."),
            ("RELEASE TEST - tactical", "Medieval, no magic. Captain Hester and scout Rowan stand in the gatehouse. Rowan holds the only map; Hester holds a lantern. They cross to the courtyard, then stop beside the closed stable door to plan. A battle is tomorrow, but no combat or departure may occur now. Keep exact map ownership and positions. No time skip."),
            ("RELEASE TEST - relationships", "Modern apartment, continuous evening. Three adult friends: Asha wants her camera back, Bea wants an apology, Claire wants both to stay for dinner. Bea holds the camera by the sink; Asha stands at the table and Claire blocks neither door. Give all three distinct goals and dialogue. End with a concrete change in their plan, not a cliffhanger."),
        ]
        for title, note in cases:
            story = api("/sessions", {"title": title})["id"]
            stories.append(story)
            opening = generate(story, note)
            rows.append(opening)
            if "apartment" in title:
                rows.append(generate(story, "Continue from that exact moment for only two minutes. Daniel keeps the brass key. Let the three decide which shelf to clear. Nobody leaves."))
                target = opening["scene"]["id"]
                for mode, direction in [("rewrite", "Rewrite this opening with more distinctive dialogue and the same events and ending boundary."), ("revise", "Revise the opening so Lena's nervousness comes through in her gestures. Preserve every event and object handoff."), ("regenerate", "Generate a new version of the same restricted introduction. Do not continue past its end.")]:
                    rows.append(generate(story, direction, mode, target))
                assert len(api(f"/sessions/{story}/scenes")) == 2
        settings["writing_length_mode"] = "chapter"
        api("/settings/model", settings, "PUT")
        long_story = api("/sessions", {"title": "RELEASE TEST - long ship scene"})["id"]
        stories.append(long_story)
        rows.append(generate(long_story, "Write a 1,800-2,200 word science-fiction chapter restricted to the next ten minutes inside a ship's maintenance room. Adult engineer Imani introduces adult apprentice Tomas to the coolant system while captain Soren needs an honest repair estimate. Distinct human dialogue, practical tools, introductions and conflicting goals. A distress call tomorrow is future background only; no call, launch, crisis, time skip, or leaving the room now."))
        return rows
    finally:
        (OUT / "writing-evidence.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        for story in stories:
            api(f"/sessions/{story}?permanent=true", method="DELETE")


def narration(url):
    corpus = [
        "The lift stopped on the third floor. Mara waited for the doors to open, then carried the parcel into the quiet hall.",
        '"I thought you had the key," Eva said. Daniel lifted it from his pocket. "I do. I was waiting for you."',
        "She set the cup down carefully. The news was good, but she needed a moment before she could trust her own voice.",
        "The rope snapped tight. Hester caught the railing, shifted her weight, and pulled Rowan clear of the falling beam.",
        "Aeron checked platform fourteen at 8:45 p.m. The bill was $127.50, not $172.05. Dr. Vale counted thirty-seven sealed boxes.",
    ]
    rows = []
    for index in range(24):
        text = corpus[index % len(corpus)] + f" She marked entry {index + 1} in the ledger."
        started = time.perf_counter()
        req = urllib.request.Request(url + "/v1/audio/speech", data=json.dumps({"input": text, "voice": "af_aoede", "speed": 0.95}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as response: audio = response.read()
        seconds = time.perf_counter() - started
        with wave.open(io.BytesIO(audio)) as wav:
            duration = wav.getnframes() / wav.getframerate()
            assert wav.getnchannels() == 1 and wav.getsampwidth() == 2 and duration > 1
        if index < 5: (OUT / f"kokoro-{['neutral','dialogue','emotional','action','names'][index]}.wav").write_bytes(audio)
        rows.append({"index": index, "seconds": seconds, "duration": duration, "rtf": seconds / duration})
    (OUT / "narration-evidence.json").write_text(json.dumps(rows, indent=2))
    print(json.dumps({"chunks": len(rows), "first_audio": rows[0]["seconds"], "mean_rtf": sum(row["rtf"] for row in rows) / len(rows), "max_rtf": max(row["rtf"] for row in rows)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--narration-url")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.narration_url: narration(args.narration_url)
    if args.model: writing(args.model)
