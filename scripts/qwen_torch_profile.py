from __future__ import annotations

import json
import os
import time
from pathlib import Path

import torch


ROOT = Path(r"D:\StoryDriver")
MODEL_PATH = ROOT / "tts_engines" / "qwen3_tts" / "models" / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
RESULT = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results" / "qwen_torch_profile.json"
TEXT = "Elena checked the brass latch while quiet rain crossed the apartment window."


def main() -> int:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    torch.set_num_threads(4)
    torch.set_num_interop_threads(4)
    from qwen_tts import Qwen3TTSModel

    model = Qwen3TTSModel.from_pretrained(
        str(MODEL_PATH),
        device_map="cpu",
        dtype=torch.float32,
        attn_implementation="eager",
        local_files_only=True,
    )
    started = time.perf_counter()
    with torch.profiler.profile(
        activities=[torch.profiler.ProfilerActivity.CPU],
        record_shapes=False,
        profile_memory=False,
        with_stack=False,
    ) as profile:
        model.generate_custom_voice(
            text=TEXT,
            language="English",
            speaker="Serena",
            max_new_tokens=192,
        )
    elapsed = time.perf_counter() - started
    events = sorted(profile.key_averages(), key=lambda item: item.self_cpu_time_total, reverse=True)[:30]
    payload = {
        "status": "complete",
        "elapsed_seconds": round(elapsed, 4),
        "torch_version": torch.__version__,
        "threads": 4,
        "interop_threads": 4,
        "events": [
            {
                "key": event.key,
                "calls": event.count,
                "self_cpu_time_total_us": round(event.self_cpu_time_total, 3),
                "cpu_time_total_us": round(event.cpu_time_total, 3),
            }
            for event in events
        ],
        "table": profile.key_averages().table(sort_by="self_cpu_time_total", row_limit=30),
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    pending = RESULT.with_suffix(".json.pending")
    pending.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    pending.replace(RESULT)
    print(payload["table"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
