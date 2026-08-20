from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation.provider_runtime import provider_registry  # noqa: E402


class LocalProviderFixture(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/models":
            self.respond({"object": "list", "data": [{"id": "fixture-local-model", "object": "model"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        if request.get("model") != "fixture-local-model":
            self.send_error(400)
            return
        self.respond(
            {
                "id": "fixture-completion",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Local provider contract passed."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            }
        )

    def respond(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def exercise(port: int) -> dict:
    provider = provider_registry.get("openai_compatible", f"http://127.0.0.1:{port}/v1")
    health = await provider.health()
    models = await provider.discover_models()
    generated = await provider.generate(
        model="fixture-local-model",
        system_prompt="Return the requested text.",
        user_prompt="Run the local provider contract.",
        parameters={"temperature": 0, "max_tokens": 16},
        timeout=10,
        inference_backend="openai_compatible",
        reasoning_mode="off",
        fallback_to_openai_compatible=False,
    )
    capabilities = await provider.get_capabilities()
    assert health["ok"] is True
    assert [item["id"] for item in models] == ["fixture-local-model"]
    assert generated["text"] == "Local provider contract passed."
    assert capabilities["load"] is False and capabilities["stream"] is True
    return {"health": health, "models": models, "generated": generated["text"], "capabilities": capabilities}


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalProviderFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(exercise(server.server_port))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
