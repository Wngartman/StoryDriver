from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from app.config import BASE_DIR, QWEN_TTS_BASE_URL


ROOT = BASE_DIR
QWEN_ROOT = Path(os.getenv("STORYDRIVER_QWEN_ROOT", str(ROOT / "tts_engines" / "qwen3_tts"))).resolve()
QWEN_PYTHON = QWEN_ROOT / "venv" / "Scripts" / "python.exe"
QWEN_SERVICE = QWEN_ROOT / "service"
QWEN_LOG_DIR = QWEN_ROOT / "logs"
_START_LOCK = asyncio.Lock()
_QWEN_PROCESS: subprocess.Popen[bytes] | None = None


class QwenTTSClient:
    """Client and local lifecycle owner for the isolated Premium Qwen worker."""

    def __init__(self, base_url: str = QWEN_TTS_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _raise_worker_error(response: httpx.Response, operation: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            try:
                payload = response.json()
                detail = payload.get("detail") if isinstance(payload, dict) else None
            except ValueError:
                detail = None
            message = str(detail or response.text or error).strip()
            raise RuntimeError(f"Qwen {operation} failed: {message[:500]}") from error

    async def health(self, timeout: float = 0.6) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
                return {"reachable": True, **response.json(), "base_url": self.base_url}
        except (httpx.HTTPError, ValueError) as error:
            return {
                "reachable": False,
                "installed": True,
                "loaded": False,
                "base_url": self.base_url,
                "error": str(error),
            }

    async def voices(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{self.base_url}/voices")
            self._raise_worker_error(response, "voice list")
            return response.json()

    async def ensure_running(self, timeout: float = 30.0) -> dict[str, Any]:
        global _QWEN_PROCESS
        started = perf_counter()
        probe_started = perf_counter()
        status = await self.health(timeout=0.35)
        initial_probe_seconds = perf_counter() - probe_started
        if status.get("reachable"):
            return {
                **status,
                "startup_timings": {
                    "initial_health_probe_seconds": round(initial_probe_seconds, 4),
                    "worker_spawn_seconds": 0.0,
                    "health_ready_seconds": 0.0,
                    "ensure_running_seconds": round(perf_counter() - started, 4),
                    "worker_was_warm": True,
                },
            }
        async with _START_LOCK:
            status = await self.health(timeout=0.35)
            if status.get("reachable"):
                return {
                    **status,
                    "startup_timings": {
                        "initial_health_probe_seconds": round(initial_probe_seconds, 4),
                        "worker_spawn_seconds": 0.0,
                        "health_ready_seconds": 0.0,
                        "ensure_running_seconds": round(perf_counter() - started, 4),
                        "worker_was_warm": True,
                    },
                }
            if not QWEN_PYTHON.exists():
                raise RuntimeError(f"Qwen Python environment is missing: {QWEN_PYTHON}")
            if _QWEN_PROCESS is None or _QWEN_PROCESS.poll() is not None:
                spawn_started = perf_counter()
                QWEN_LOG_DIR.mkdir(parents=True, exist_ok=True)
                environment = os.environ.copy()
                environment.update(
                    {
                        "TMP": str(ROOT / "backend" / "data" / "temp"),
                        "TEMP": str(ROOT / "backend" / "data" / "temp"),
                        "PIP_CACHE_DIR": str(ROOT / "backend" / "data" / "temp" / "pip-cache"),
                        "HF_HOME": str(ROOT / "tts_engines" / "cache" / "huggingface"),
                        "HUGGINGFACE_HUB_CACHE": str(ROOT / "tts_engines" / "cache" / "huggingface" / "hub"),
                        "TRANSFORMERS_CACHE": str(ROOT / "tts_engines" / "cache" / "transformers"),
                        "TORCH_HOME": str(ROOT / "tts_engines" / "cache" / "torch"),
                        "XDG_CACHE_HOME": str(ROOT / "tts_engines" / "cache"),
                        "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1",
                    }
                )
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
                with (QWEN_LOG_DIR / "service_stdout.log").open("ab") as stdout, (
                    QWEN_LOG_DIR / "service_stderr.log"
                ).open("ab") as stderr:
                    _QWEN_PROCESS = subprocess.Popen(
                        [
                            str(QWEN_PYTHON),
                            "-m",
                            "uvicorn",
                            "app:app",
                            "--app-dir",
                            str(QWEN_SERVICE),
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "8891",
                        ],
                        cwd=str(QWEN_SERVICE),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        creationflags=creation_flags,
                    )
                worker_spawn_seconds = perf_counter() - spawn_started
            else:
                worker_spawn_seconds = 0.0
            ready_started = perf_counter()
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if _QWEN_PROCESS is not None and _QWEN_PROCESS.poll() is not None:
                    raise RuntimeError(f"Qwen worker exited with code {_QWEN_PROCESS.returncode}.")
                await asyncio.sleep(0.25)
                status = await self.health(timeout=0.5)
                if status.get("reachable"):
                    return {
                        **status,
                        "startup_timings": {
                            "initial_health_probe_seconds": round(initial_probe_seconds, 4),
                            "worker_spawn_seconds": round(worker_spawn_seconds, 4),
                            "health_ready_seconds": round(perf_counter() - ready_started, 4),
                            "ensure_running_seconds": round(perf_counter() - started, 4),
                            "worker_was_warm": False,
                        },
                    }
            raise RuntimeError("Qwen worker did not become ready within 30 seconds.")

    async def ensure_loaded(self, model_kind: str = "custom_voice") -> dict[str, Any]:
        status = await self.ensure_running()
        if status.get("loaded") and status.get("loaded_model_kind") == model_kind:
            return status
        load_started = perf_counter()
        async with httpx.AsyncClient(timeout=150.0) as client:
            response = await client.post(
                f"{self.base_url}/load",
                json={"threads": 4, "interop_threads": 4, "model_kind": model_kind},
            )
            self._raise_worker_error(response, "model load")
            return {
                **response.json(),
                "startup_timings": {
                    **(status.get("startup_timings") or {}),
                    "model_load_seconds": round(perf_counter() - load_started, 4),
                },
            }

    async def synthesize_batch(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        if not requests or len(requests) > 4:
            raise ValueError("Qwen batch size must be between one and four.")
        total_started = perf_counter()
        running = await self.ensure_running()
        timings = dict(running.get("startup_timings") or {})
        async with httpx.AsyncClient(timeout=620.0) as client:
            request_started = perf_counter()
            response = await client.post(f"{self.base_url}/synthesize-batch", json={"requests": requests})
            timings["first_batch_request_seconds"] = round(perf_counter() - request_started, 4)
            if response.status_code == 503 and "not loaded" in response.text.lower():
                model_kind = "base" if any(request.get("custom_voice_id") for request in requests) else "custom_voice"
                loaded = await self.ensure_loaded(model_kind)
                for key, value in (loaded.get("startup_timings") or {}).items():
                    if key == "model_load_seconds" or key not in timings:
                        timings[key] = value
                retry_started = perf_counter()
                response = await client.post(f"{self.base_url}/synthesize-batch", json={"requests": requests})
                timings["post_load_batch_request_seconds"] = round(perf_counter() - retry_started, 4)
            self._raise_worker_error(response, "batch synthesis")
            result = response.json()
            result["startup_timings"] = {
                **timings,
                "client_total_seconds": round(perf_counter() - total_started, 4),
            }
            return result

    async def build_voice_prompts(
        self,
        *,
        voice_id: str,
        revision: int,
        prompts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        await self.ensure_loaded("base")
        async with httpx.AsyncClient(timeout=260.0) as client:
            response = await client.post(
                f"{self.base_url}/build-voice-prompts",
                json={"voice_id": voice_id, "revision": revision, "prompts": prompts},
            )
            self._raise_worker_error(response, "voice prompt build")
            return response.json()

    async def cancel(self, request_id: str | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.post(f"{self.base_url}/cancel", json={"request_id": request_id})
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            return {"ok": False, "cancelled": False, "reachable": False, "error": str(error)}

    async def unload(self, timeout: float = 8.0) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/unload")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as error:
            return {"ok": False, "unloaded": False, "reachable": False, "error": str(error)}

    async def shutdown(self) -> dict[str, Any]:
        global _QWEN_PROCESS
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.post(f"{self.base_url}/shutdown")
                response.raise_for_status()
                result = response.json()
            process = _QWEN_PROCESS
            if process is not None:
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                if process.poll() is not None:
                    _QWEN_PROCESS = None
            return result
        except (httpx.HTTPError, ValueError) as error:
            return {"ok": False, "shutdown_scheduled": False, "reachable": False, "error": str(error)}


async def ensure_qwen_unloaded_before_writing() -> dict[str, Any]:
    """Protect Gemma resources without starting or otherwise touching LM Studio."""
    client = QwenTTSClient()
    status = await client.health(timeout=0.35)
    if not status.get("reachable"):
        return {"checked": True, "unloaded": False, "reason": "qwen_not_loaded"}
    retained_rss = int(status.get("rss_bytes") or 0)
    if not status.get("loaded") and retained_rss < 800_000_000:
        return {"checked": True, "unloaded": False, "reason": "qwen_not_loaded"}
    await client.cancel(status.get("active_request_id"))
    result = await client.shutdown()
    return {"checked": True, "worker_shutdown": True, "pre_shutdown_rss_bytes": retained_rss, **result}
