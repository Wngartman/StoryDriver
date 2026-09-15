from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import socket
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
        self._loaded_model: str | None = None
        self._flags: set[str] | None = None

    async def health(self) -> dict[str, Any]:
        if self._process is None or self._process.poll() is not None:
            return {"ok": False, "provider": self.id, "runtime_available": self.executable.is_file(), "state": "unloaded"}
        result = await super().health()
        result.update({"runtime_available": self.executable.is_file(), "executable": str(self.executable)})
        return result

    async def ensure_loaded(self, model: str, options: dict[str, Any] | None = None) -> None:
        async with self._lock:
            if self._process and self._process.poll() is None and self._loaded_model == str(Path(model).resolve()):
                return
            await self._load_model(model, options)

    async def load_model(self, model: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            return await self._load_model(model, options)

    async def _load_model(self, model: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        model_path = Path(model).expanduser().resolve()
        if not self.executable.is_file():
            raise RuntimeError(f"Bundled llama.cpp runtime is missing: {self.executable}")
        if not model_path.is_file() or model_path.suffix.lower() != ".gguf":
            raise ValueError("Built-in llama.cpp requires an existing local GGUF file.")
        options = {key: value for key, value in (options or {}).items() if value is not None}
        limits = {"context_length": (2048, 131072), "gpu_layers": (-1, 999), "threads": (1, 256), "batch_size": (32, 8192), "parallel_slots": (1, 8), "startup_timeout": (5, 300)}
        for key, (low, high) in limits.items():
            if key in options and (isinstance(options[key], bool) or not isinstance(options[key], (int, float)) or not low <= options[key] <= high):
                raise ValueError(f"{key} must be between {low} and {high}.")
        try:
            if self._process and self._process.poll() is None:
                await self._unload_model()
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", 12345)) == 0:
                    raise RuntimeError("Port 12345 is already in use. StoryDriver will not take over another model server.")
            flags = await asyncio.to_thread(self._supported_flags)
            command = [str(self.executable), "-m", str(model_path), "--host", "127.0.0.1", "--port", "12345"]
            self._append_option(command, flags, "-c", options.get("context_length", 16384))
            self._append_option(command, flags, "-ngl", options.get("gpu_layers", "auto"))
            self._append_option(command, flags, "-t", options.get("threads"))
            self._append_option(command, flags, "-b", options.get("batch_size"))
            self._append_option(command, flags, "-np", options.get("parallel_slots", 1))
            if isinstance(options.get("flash_attention"), bool) and "--flash-attn" in flags:
                command.extend(["--flash-attn", "on" if options["flash_attention"] else "off"])
            if "--no-webui" in flags:
                command.append("--no-webui")
            if "--reasoning" in flags:
                command.extend(["--reasoning", "off"])
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > 2_000_000:
                os.replace(self.log_path, self.log_path.with_suffix(".previous.log"))
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
            async with httpx.AsyncClient(timeout=1.0, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if self._process.poll() is not None:
                        raise RuntimeError(f"llama.cpp exited with code {self._process.returncode}; see {self.log_path}")
                    try:
                        response = await client.get("http://127.0.0.1:12345/health")
                        if response.status_code == 200 and response.json().get("status") == "ok":
                            self._loaded_model = str(model_path)
                            return {"ok": True, "provider": self.id, "pid": self._process.pid, "model": str(model_path)}
                    except (httpx.HTTPError, ValueError):
                        pass
                    await asyncio.sleep(0.5)
            raise TimeoutError(f"llama.cpp did not become healthy; see {self.log_path}")
        except BaseException:
            await self._unload_model()
            raise

    async def unload_model(self, model: str | None = None) -> dict[str, Any]:
        async with self._lock:
            return await self._unload_model()

    async def _unload_model(self) -> dict[str, Any]:
        self._loaded_model = None
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
        if self._flags is not None:
            return self._flags
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
        candidates = {"-c", "-ngl", "-t", "-b", "-np", "--flash-attn", "--no-webui", "--reasoning"}
        self._flags = {flag for flag in candidates if flag in text}
        return self._flags

    @staticmethod
    def _append_option(command: list[str], flags: set[str], flag: str, value: Any) -> None:
        if value is not None and flag in flags:
            command.extend([flag, str(value)])


class ProviderRegistry:
    def __init__(self) -> None:
        self.llama = LlamaCppProvider()

    def get(self, provider_id: str, endpoint: str | None = None) -> ModelProvider:
        if provider_id == "llama_cpp":
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
