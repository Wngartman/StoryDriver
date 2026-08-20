from __future__ import annotations

import json
import time
import urllib.request


URL = "http://localhost:1234/v1/chat/completions"
MODEL = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"


def main() -> int:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write a vivid but concise paragraph about a lantern in a rainstorm."}],
        "max_tokens": 220,
        "temperature": 0.4,
        "stream": True,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    first_event = None
    first_visible = None
    visible = []
    reasoning_chars = 0
    with urllib.request.urlopen(req, timeout=120) as response:
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            if first_event is None:
                first_event = time.perf_counter()
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = (event.get("choices") or [{}])[0].get("delta") or {}
            reasoning = delta.get("reasoning_content") or ""
            content = delta.get("content") or ""
            reasoning_chars += len(reasoning)
            if content:
                if first_visible is None:
                    first_visible = time.perf_counter()
                visible.append(content)
    ended = time.perf_counter()
    text = "".join(visible)
    token_estimate = max(1, round(len(text) / 4))
    visible_elapsed = max(0.001, ended - (first_visible or started))
    result = {
        "model": MODEL,
        "first_stream_event_seconds": round((first_event or ended) - started, 3),
        "first_visible_seconds": round((first_visible or ended) - started, 3),
        "wall_seconds": round(ended - started, 3),
        "visible_chars": len(text),
        "visible_tokens_estimate": token_estimate,
        "visible_tokens_per_second_estimate": round(token_estimate / visible_elapsed, 2),
        "hidden_reasoning_chars": reasoning_chars,
        "preview": text[:240],
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
