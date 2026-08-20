from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.generation.model_provider import model_client_for_settings  # noqa: E402
from app.generation.provider_runtime import ModelLibrary, provider_registry  # noqa: E402
from app.generation.router import resolve_all_task_model_settings  # noqa: E402
from app.settings.store import load_model_settings  # noqa: E402


def main() -> int:
    settings = load_model_settings(resolve_active_preset=True)
    assert settings.provider == "lm_studio", settings.provider
    assert settings.provider_url == settings.lm_studio_url
    assert model_client_for_settings(settings).provider_id == "lm_studio"

    resolved = resolve_all_task_model_settings()
    for task_type, task in resolved.items():
        assert task.provider in {"llama_cpp", "openai_compatible", "lm_studio"}, task_type
        assert task.provider_url.startswith(("http://localhost", "http://127.0.0.1")), task.provider_url

    external_rejected = False
    try:
        provider_registry.get("openai_compatible", "https://example.invalid/v1")
    except ValueError:
        external_rejected = True
    assert external_rejected, "Non-local provider URL was accepted without an explicit privacy override."

    diagnostics = asyncio.run(provider_registry.diagnostics(settings.provider, settings.provider_url))
    assert diagnostics["health"]["ok"] is True, diagnostics
    assert diagnostics["capabilities"]["stream"] is True

    temp_root = ROOT / "backend" / "data" / "temp" / f"provider-contract-{uuid4()}"
    temp_root.mkdir(parents=True, exist_ok=False)
    try:
        gguf = temp_root / "fixture-q4_k_m.gguf"
        gguf.write_bytes(b"GGUF-contract-fixture")
        library = ModelLibrary(temp_root / "model-library.json")
        added = library.add_gguf(str(gguf))
        assert added["provider"] == "llama_cpp"
        assert added["path"] == str(gguf.resolve())
        payload = json.loads((temp_root / "model-library.json").read_text(encoding="utf-8"))
        assert len(payload["models"]) == 1
        assert library.remove(added["id"]) is True
        assert gguf.exists(), "Removing a library entry deleted the model file."
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    print("model provider abstraction contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
