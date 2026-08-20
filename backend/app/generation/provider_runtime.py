from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, AsyncIterator

import httpx

from app.config import BASE_DIR, DATA_DIR
from app.diagnostics.privacy import validate_local_service_url
from app.generation.model_provider import LMStudioClient, LMStudioError
from app.services.lmstudio_resource_client import LMStudioResourceClient


PROVIDER_IDS = ("llama_cpp", "openai_compatible", "lm_studio")
LLAMA_DEFAULT_URL = "http://127.0.0.1:12345/v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hidden_process_flags() -> tuple[int, subprocess.STARTUPINFO | None]:
    if os.name != "nt":
        return 0, None
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0), startup


@dataclass
class ModelLibraryEntry:
    id: str
    name: str
    provider: str
    path: str = ""
    endpoint: str = ""
    architecture: str = ""
    quantization: str = ""
    size_bytes: int = 0
    context_length: int | None = None
    compatibility: str = "unknown"
    added_at: str = ""


class ModelLibrary:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (DATA_DIR / "config" / "model-library.json")
        self._lock = threading.RLock()

    def _read(self) -> list[ModelLibraryEntry]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = payload.get("models", []) if isinstance(payload, dict) else []
        result: list[ModelLibraryEntry] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                result.append(ModelLibraryEntry(**item))
            except TypeError:
                continue
        return result

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(item) for item in self._read()]

    def add_gguf(self, path: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file() or resolved.suffix.lower() != ".gguf":
            raise ValueError("Select an existing local .gguf model file.")
        entry = ModelLibraryEntry(
            id=f"gguf:{resolved.as_posix().lower()}",
            name=resolved.stem,
            provider="llama_cpp",
            path=str(resolved),
            size_bytes=resolved.stat().st_size,
            compatibility="gguf",
            added_at=utc_now(),
        )
        with self._lock:
            entries = self._read()
            entries = [item for item in entries if item.id != entry.id]
            entries.append(entry)
            self._write(entries)
        return asdict(entry)

    def scan(self, directory: str) -> list[dict[str, Any]]:
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Select an existing local model directory.")
        added: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*.gguf")):
            added.append(self.add_gguf(str(path)))
        return added

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            entries = self._read()
            kept = [item for item in entries if item.id != entry_id]
            if len(kept) == len(entries):
                return False
            self._write(kept)
        return True

    def _write(self, entries: list[ModelLibraryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "models": [asdict(item) for item in entries]}, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class ModelProvider(ABC):
    id: str
    display_name: str

    def __init__(self, endpoint: str) -> None:
        self.endpoint = validate_local_service_url(endpoint, f"{self.display_name} URL").rstrip("/")

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...

    @abstractmethod
    async def discover_models(self) -> list[dict[str, Any]]: ...

    async def get_model_info(self, model: str) -> dict[str, Any]:
        models = await self.discover_models()
        return next((item for item in models if str(item.get("id")) == model), {"id": model})

    async def load_model(self, model: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": False, "supported": False, "provider": self.id, "model": model}

    async def unload_model(self, model: str | None = None) -> dict[str, Any]:
        return {"ok": False, "supported": False, "provider": self.id, "model": model}

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        client = LMStudioClient(self.endpoint, provider_id=self.id)
        return await client.generate_scene_routed(**kwargs)

    async def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        client = LMStudioClient(self.endpoint, provider_id=self.id)
        async for event in client.stream_scene_events_routed(**kwargs):
            yield event

    async def cancel(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.id, "transport": "client_disconnect"}

    @abstractmethod
    async def get_capabilities(self) -> dict[str, Any]: ...

    async def get_runtime_metrics(self) -> dict[str, Any]:
        return {"provider": self.id, "endpoint": self.endpoint}


class OpenAICompatibleProvider(ModelProvider):
    id = "openai_compatible"
    display_name = "Local OpenAI-compatible"

    async def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            models = await self.discover_models()
            return {
                "ok": True,
                "provider": self.id,
                "endpoint": self.endpoint,
                "model_count": len(models),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except Exception as exc:
            return {"ok": False, "provider": self.id, "endpoint": self.endpoint, "error": str(exc)}

    async def discover_models(self) -> list[dict[str, Any]]:
        return await LMStudioClient(self.endpoint, provider_id=self.id).list_models()

    async def get_capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.id,
            "stream": True,
            "model_discovery": True,
            "load": False,
            "unload": False,
            "cancel": "client_disconnect",
            "runtime_controls": [],
        }


class LMStudioProvider(OpenAICompatibleProvider):
    id = "lm_studio"
    display_name = "LM Studio"

    async def discover_models(self) -> list[dict[str, Any]]:
        return await LMStudioClient(self.endpoint, provider_id=self.id).list_models()

    async def load_model(self, model: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return await LMStudioResourceClient().load_lmstudio_model(model, options)

    async def unload_model(self, model: str | None = None) -> dict[str, Any]:
        if not model:
            return {"ok": False, "supported": True, "error": "LM Studio unload requires an instance ID."}
        return await LMStudioResourceClient().unload_lmstudio_instance(model)

    async def get_capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.id,
            "stream": True,
            "model_discovery": True,
            "load": True,
            "unload": True,
            "cancel": "client_disconnect",
            "native_rest": True,
            "runtime_controls": ["context_length", "gpu_offload"],
        }


class LlamaCppProvider(OpenAICompatibleProvider):
    id = "llama_cpp"
    display_name = "Built-in llama.cpp"

    def __init__(self, endpoint: str = LLAMA_DEFAULT_URL) -> None:
        super().__init__(endpoint)
        self.executable = Path(
            os.getenv("STORYDRIVER_LLAMA_SERVER", str(BASE_DIR / "runtimes" / "llama.cpp" / "llama-server.exe"))
        ).resolve()
        self.log_path = DATA_DIR / "logs" / "llama-cpp-runtime.log"
        self.state_path = DATA_DIR / "runtime" / "llama-cpp-process.json"
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = asyncio.Lock()

    async def health(self) -> dict[str, Any]:
        result = await super().health()
        result.update({"runtime_available": self.executable.is_file(), "executable": str(self.executable)})
        return result

    async def load_model(self, model: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        model_path = Path(model).expanduser().resolve()
        if not self.executable.is_file():
            raise RuntimeError(f"Bundled llama.cpp runtime is missing: {self.executable}")
        if not model_path.is_file() or model_path.suffix.lower() != ".gguf":
            raise ValueError("Built-in llama.cpp requires an existing local GGUF file.")
        options = options or {}
        async with self._lock:
            if self._process and self._process.poll() is None:
                await self.unload_model()
            flags = await asyncio.to_thread(self._supported_flags)
            command = [str(self.executable), "-m", str(model_path), "--host", "127.0.0.1", "--port", "12345"]
            self._append_option(command, flags, "-c", options.get("context_length"))
            self._append_option(command, flags, "-ngl", options.get("gpu_layers"))
            self._append_option(command, flags, "-t", options.get("threads"))
            self._append_option(command, flags, "-b", options.get("batch_size"))
            self._append_option(command, flags, "-np", options.get("parallel_slots"))
            if options.get("flash_attention") is True and "--flash-attn" in flags:
                command.extend(["--flash-attn", "on"])
            if "--no-webui" in flags:
                command.append("--no-webui")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            log = self.log_path.open("ab", buffering=0)
            creationflags, startupinfo = hidden_process_flags()
            self._process = subprocess.Popen(
                command,
                cwd=str(self.executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            log.close()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"pid": self._process.pid, "model": str(model_path), "started_at": utc_now()}),
                encoding="utf-8",
            )
        deadline = time.monotonic() + float(options.get("startup_timeout", 120))
        while time.monotonic() < deadline:
            status = await super().health()
            if status.get("ok"):
                return {"ok": True, "provider": self.id, "pid": self._process.pid, "model": str(model_path)}
            if self._process.poll() is not None:
                raise RuntimeError(f"llama.cpp exited with code {self._process.returncode}; see {self.log_path}")
            await asyncio.sleep(0.5)
        raise TimeoutError(f"llama.cpp did not become healthy; see {self.log_path}")

    async def unload_model(self, model: str | None = None) -> dict[str, Any]:
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            self.state_path.unlink(missing_ok=True)
            return {"ok": True, "provider": self.id, "already_stopped": True}
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 8)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, 5)
        code = process.returncode
        self._process = None
        self.state_path.unlink(missing_ok=True)
        return {"ok": True, "provider": self.id, "exit_code": code}

    async def get_capabilities(self) -> dict[str, Any]:
        flags = await asyncio.to_thread(self._supported_flags) if self.executable.is_file() else set()
        return {
            "provider": self.id,
            "stream": True,
            "model_discovery": True,
            "load": True,
            "unload": True,
            "cancel": "client_disconnect",
            "runtime_available": self.executable.is_file(),
            "runtime_controls": [
                name
                for name, flag in (
                    ("context_length", "-c"),
                    ("gpu_layers", "-ngl"),
                    ("threads", "-t"),
                    ("batch_size", "-b"),
                    ("parallel_slots", "-np"),
                    ("flash_attention", "--flash-attn"),
                )
                if flag in flags
            ],
        }

    async def get_runtime_metrics(self) -> dict[str, Any]:
        process = self._process
        return {
            "provider": self.id,
            "endpoint": self.endpoint,
            "runtime_available": self.executable.is_file(),
            "running": bool(process and process.poll() is None),
            "pid": process.pid if process and process.poll() is None else None,
            "log_path": str(self.log_path),
        }

    def _supported_flags(self) -> set[str]:
        if not self.executable.is_file():
            return set()
        creationflags, startupinfo = hidden_process_flags()
        try:
            result = subprocess.run(
                [str(self.executable), "--help"],
                cwd=str(self.executable.parent),
                capture_output=True,
                timeout=15,
                check=False,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except (OSError, subprocess.SubprocessError):
            return set()
        text = (result.stdout + result.stderr).decode("utf-8", errors="ignore")
        candidates = {"-c", "-ngl", "-t", "-b", "-np", "--flash-attn", "--no-webui"}
        return {flag for flag in candidates if flag in text}

    @staticmethod
    def _append_option(command: list[str], flags: set[str], flag: str, value: Any) -> None:
        if value is not None and flag in flags:
            command.extend([flag, str(value)])


class ProviderRegistry:
    def __init__(self) -> None:
        self.llama = LlamaCppProvider()

    def get(self, provider_id: str, endpoint: str | None = None) -> ModelProvider:
        if provider_id == "llama_cpp":
            if endpoint and endpoint.rstrip("/") != self.llama.endpoint:
                self.llama.endpoint = validate_local_service_url(endpoint, "Built-in llama.cpp URL").rstrip("/")
            return self.llama
        if provider_id == "openai_compatible":
            return OpenAICompatibleProvider(endpoint or "http://127.0.0.1:1234/v1")
        if provider_id == "lm_studio":
            return LMStudioProvider(endpoint or "http://127.0.0.1:1234/v1")
        raise ValueError(f"Unknown model provider: {provider_id}")

    async def diagnostics(self, provider_id: str, endpoint: str) -> dict[str, Any]:
        provider = self.get(provider_id, endpoint)
        health, capabilities, metrics = await asyncio.gather(
            provider.health(), provider.get_capabilities(), provider.get_runtime_metrics()
        )
        return {"health": health, "capabilities": capabilities, "metrics": metrics}


model_library = ModelLibrary()
provider_registry = ProviderRegistry()
