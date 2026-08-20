from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation.provider_runtime import model_library, provider_registry  # noqa: E402


async def run() -> dict:
    model_path = Path(os.environ["STORYDRIVER_TEST_GGUF"]).resolve()
    if not model_path.is_file():
        raise FileNotFoundError(model_path)

    library_entry = model_library.add_gguf(str(model_path))
    provider = provider_registry.get("llama_cpp", "http://127.0.0.1:12345/v1")
    load_started = perf_counter()
    try:
        loaded = await provider.load_model(
            str(model_path),
            {
                "context_length": 8192,
                "gpu_layers": 99,
                "threads": max((os.cpu_count() or 8) // 2, 4),
                "batch_size": 1024,
                "parallel_slots": 1,
                "flash_attention": True,
                "startup_timeout": 180,
            },
        )
        load_seconds = perf_counter() - load_started
        models = await provider.discover_models()
        model_id = str(models[0].get("id")) if models else model_path.name
        generation_started = perf_counter()
        generated = await provider.generate(
            model=model_id,
            system_prompt="Return only the requested text.",
            user_prompt="Reply with exactly: StoryDriver local provider ready",
            parameters={"temperature": 0, "max_tokens": 24},
            timeout=60,
            inference_backend="openai_compatible",
            reasoning_mode="off",
            fallback_to_openai_compatible=False,
        )
        generation_seconds = perf_counter() - generation_started
        metrics = await provider.get_runtime_metrics()
        return {
            "ok": True,
            "library_entry": library_entry,
            "load_seconds": round(load_seconds, 3),
            "generation_seconds": round(generation_seconds, 3),
            "model_id": model_id,
            "text": str(generated.get("text") or "")[:200],
            "runtime": metrics,
        }
    finally:
        await provider.unload_model()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
