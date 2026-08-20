from __future__ import annotations

import json
from pathlib import Path


SOURCE = Path(
    r"C:\Users\wngar\Documents\ComfyUI\codex_diagnostics\stable_comfyui_recovery\benchmarks\full_lonecat_normal_changed_seed_20260605_1825.json"
)
OUTPUT = Path(r"D:\StoryDriver\backend\data\comfy_workflows\lonecat_zit_nsfw_8_0_1_api.json")


def main() -> int:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    prompt = None
    if isinstance(data.get("history"), dict) and data["history"]:
        prompt = data["history"][next(iter(data["history"]))].get("prompt")
    elif data:
        first = data[next(iter(data))]
        if isinstance(first, dict):
            prompt = first.get("prompt")

    if not isinstance(prompt, list) or len(prompt) < 3 or not isinstance(prompt[2], dict):
        raise RuntimeError(f"No ComfyUI API prompt graph found in {SOURCE}")

    graph = prompt[2]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    print(f"Nodes: {len(graph)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
