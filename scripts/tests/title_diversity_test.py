from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
BACKEND = "http://localhost:8001"
DB_PATH = ROOT / "backend" / "data" / "app.db"
METRICS = ROOT / "backend" / "data" / "logs" / "TITLE_DIVERSITY_LATEST.json"
sys.path.insert(0, str(ROOT / "backend"))

from app.services.auto_title import (
    deterministic_title_fallback,
    generated_title_is_usable,
    title_lexical_similarity,
    title_structure,
    title_words,
)


GENRES = ["contemporary", "romance", "medieval", "science fiction", "horror", "mystery", "erotic drama", "comedy", "survival"]
EVENTS = ["letter", "key", "compass", "promise", "bridge", "apartment", "station", "ship", "garden", "trial", "door", "map", "ring", "storm", "heist", "wedding", "signal", "forest", "ledger"]
NAMES = [
    "Mara", "June", "Elian", "Sabine", "Rhea", "Imani", "Noor", "Elena", "Tomas", "Lysa",
    "Dara", "Micah", "Oren", "Vera", "Celia", "Ronan", "Asha", "Felix", "Nadia", "Quinn",
    "Iris", "Caleb", "Mina", "Theo", "Priya", "Jonah", "Ada", "Soren", "Mae", "Dante",
    "Leah", "Niko", "Faye", "Arden", "Kira", "Bram", "Sana", "Hollis", "Talia", "Emmett",
    "Ruth", "Cass", "Zara", "Milo", "Eden", "Pavel", "Nora", "Gideon", "Anya", "Reese",
]


def request(path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 120) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BACKEND + path, method=method, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def delete_story(session_id: str) -> dict[str, Any]:
    result = request(f"/sessions/{session_id}?permanent=true", "DELETE") or {}
    job_id = result.get("job_id") or result.get("id")
    if not job_id:
        return result
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = request(f"/sessions/delete-jobs/{job_id}") or {}
        if job.get("status") in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.5)
    return {"status": "timeout"}


def seed_scene(session_id: str, director_note: str, scene_text: str) -> None:
    scene_id, version_id = str(uuid4()), str(uuid4())
    with sqlite3.connect(DB_PATH) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute(
            "INSERT INTO scenes (id, session_id, director_note, generated_text, mode) VALUES (?, ?, ?, ?, 'continue')",
            (scene_id, session_id, director_note, scene_text),
        )
        db.execute(
            """
            INSERT INTO scene_versions (id, scene_id, session_id, director_note, generated_text, mode, version_index)
            VALUES (?, ?, ?, ?, ?, 'continue', 1)
            """,
            (version_id, scene_id, session_id, director_note, scene_text),
        )
        db.commit()


def static_cases() -> dict[str, Any]:
    titles: list[str] = []
    recent: list[str] = []
    cases: list[dict[str, str]] = []
    for index in range(50):
        genre = GENRES[index % len(GENRES)]
        name = NAMES[index]
        event = EVENTS[index % len(EVENTS)]
        note = f"{genre.title()} story about {name}, whose choice involving a {event} changes a close relationship."
        scene = f"{name} studies the {event} at the center of the room and makes a specific choice before the other person answers."
        title = deterministic_title_fallback(note, scene, character_names=[name], recent_titles=recent[-12:])
        if not generated_title_is_usable(title, recent[-12:]):
            raise AssertionError(f"fallback title was unusable at case {index}: {title}")
        titles.append(title)
        cases.append({"genre": genre, "name": name, "event": event, "title": title})
        recent.append(title)
    first_words = [title_words(title)[0] for title in titles]
    structures = Counter(title_structure(title) for title in titles)
    max_similarity = max(
        title_lexical_similarity(first, second)
        for index, first in enumerate(titles)
        for second in titles[index + 1 :]
    )
    return {
        "count": len(titles),
        "unique_count": len(set(title.lower() for title in titles)),
        "repeated_first_words": {word: count for word, count in Counter(first_words).items() if count > 1},
        "structures": dict(structures),
        "max_pair_similarity": round(max_similarity, 3),
        "cases": cases,
    }


def live_cases(count: int) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    created: list[str] = []
    for index in range(count):
        genre = GENRES[index % len(GENRES)]
        name = NAMES[40 + index]
        event = EVENTS[(index * 3) % len(EVENTS)]
        director_note = f"Open a {genre} story with {name} confronting the practical consequences of a {event}."
        scene_text = (
            f"{name} set the {event} on the chipped table beneath the kitchen light. The choice was concrete: "
            f"tell the truth now, or let the person in the next room act on a lie. {name} crossed to the closed door "
            "but stopped with one hand on the latch, hearing the other person's careful breathing beyond it."
        )
        session = request("/sessions", "POST", {"title": "New Story"})
        session_id = session["id"]
        created.append(session_id)
        seed_scene(session_id, director_note, scene_text)
        titled = request(
            f"/sessions/{session_id}/auto-title",
            "POST",
            {"scene_text": scene_text, "director_note": director_note},
            timeout=180,
        )
        title = str(titled.get("title") or "")
        results.append(
            {
                "genre": genre,
                "title": title,
                "status": titled.get("auto_title_status"),
                "usable": generated_title_is_usable(title, [item["title"] for item in results]),
                "specific_signal": name.lower() in title.lower() or event.lower() in title.lower() or len(title_words(title)) >= 2,
            }
        )
    return results, created


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", type=int, default=0, choices=range(0, 10))
    args = parser.parse_args()
    metrics: dict[str, Any] = {"static": static_cases(), "live": [], "errors": [], "deletions": []}
    created: list[str] = []
    try:
        if args.live:
            metrics["live"], created = live_cases(args.live)
        static = metrics["static"]
        if static["unique_count"] != 50:
            metrics["errors"].append(f"only {static['unique_count']} of 50 deterministic titles were unique")
        if static["repeated_first_words"]:
            metrics["errors"].append(f"repeated deterministic first words: {static['repeated_first_words']}")
        live_titles = [item["title"].lower() for item in metrics["live"]]
        if len(live_titles) != len(set(live_titles)):
            metrics["errors"].append("live model title calls repeated a title")
        for item in metrics["live"]:
            if item["status"] != "generated" or not item["usable"] or not item["specific_signal"]:
                metrics["errors"].append(f"live title failed: {item}")
    finally:
        metrics["deletions"] = [delete_story(session_id) for session_id in reversed(created)]
        metrics["passed"] = not metrics["errors"] and all(item.get("status") == "completed" for item in metrics["deletions"])
        METRICS.parent.mkdir(parents=True, exist_ok=True)
        METRICS.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    print(f"PASS: {metrics['static']['unique_count']}/50 unique deterministic automatic titles; live={len(metrics['live'])}." if metrics["passed"] else "FAIL")
    for error in metrics["errors"]:
        print(f"  {error}")
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
