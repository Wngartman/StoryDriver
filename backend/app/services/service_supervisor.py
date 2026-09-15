from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import BASE_DIR, DATA_DIR, settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enabled(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def hidden_process_flags() -> tuple[int, subprocess.STARTUPINFO | None]:
    if os.name != "nt":
        return 0, None
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0), startup


def port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


@dataclass
class ManagedServiceState:
    name: str
    status: str = "stopped"
    reachable: bool = False
    owned: bool = False
    pid: int | None = None
    endpoint: str = ""
    started_at: str | None = None
    ready_at: str | None = None
    last_checked_at: str | None = None
    restart_count: int = 0
    error: str = ""
    log_path: str = ""


class StoryDriverServiceSupervisor:
    """Owns optional local workers started by this backend process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._states = {
            "kokoro": ManagedServiceState(
                name="kokoro",
                endpoint=settings.kokoro_base_url,
                log_path=str(DATA_DIR / "logs" / "kokoro-supervisor.log"),
            )
        }
        self._monitor: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start_background(self) -> None:
        if not enabled(settings.auto_start_kokoro):
            return
        with self._lock:
            if self._monitor and self._monitor.is_alive():
                return
            self._stop_event.clear()
            self._monitor = threading.Thread(target=self._monitor_loop, name="storydriver-service-supervisor", daemon=True)
            self._monitor.start()

    def _monitor_loop(self) -> None:
        self.ensure_kokoro_started()
        while not self._stop_event.wait(2.0):
            state = self._refresh_kokoro()
            process = self._processes.get("kokoro")
            if process and process.poll() is not None and state.owned and state.restart_count < 2:
                state.restart_count += 1
                state.error = f"Kokoro exited with code {process.returncode}; bounded restart {state.restart_count}/2."
                self._processes.pop("kokoro", None)
                time.sleep(min(2 * state.restart_count, 4))
                self.ensure_kokoro_started(restart=True)

    def ensure_kokoro_started(self, restart: bool = False) -> dict[str, Any]:
        with self._lock:
            state = self._states["kokoro"]
            if self._kokoro_reachable():
                state.status = "ready"
                state.reachable = True
                if not state.owned:
                    state.status = "external"
                state.ready_at = state.ready_at or utc_now()
                state.last_checked_at = utc_now()
                state.error = ""
                return asdict(state)
            process = self._processes.get("kokoro")
            if process and process.poll() is None:
                state.status = "starting"
                state.last_checked_at = utc_now()
                return asdict(state)

            command, working_dir, environment = self._kokoro_command()
            if not command:
                state.status = "error"
                state.error = "Kokoro's local Python environment is not configured or is missing."
                state.last_checked_at = utc_now()
                return asdict(state)

            parsed = urlparse(settings.kokoro_base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8880
            if port_is_open(host, port):
                state.status = "error"
                state.error = f"Port {port} is occupied by a process that is not a healthy Kokoro service."
                state.last_checked_at = utc_now()
                return asdict(state)

            log_path = Path(state.log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            creationflags, startupinfo = hidden_process_flags()
            with log_path.open("ab", buffering=0) as log:
                try:
                    process = subprocess.Popen(
                        command, cwd=str(working_dir), env=environment,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=creationflags, startupinfo=startupinfo,
                    )
                except OSError as error:
                    state.status = "error"
                    state.error = f"Kokoro could not start: {error}"
                    return asdict(state)
            self._processes["kokoro"] = process
            state.status = "starting"
            state.reachable = False
            state.owned = True
            state.pid = process.pid
            state.started_at = utc_now()
            state.ready_at = None
            state.last_checked_at = utc_now()
            state.error = "" if not restart else state.error
            return asdict(state)

    def _kokoro_command(self) -> tuple[list[str], Path, dict[str, str]]:
        bundled = BASE_DIR / "runtimes" / "kokoro" / "StoryDriverNarration.exe"
        if bundled.is_file() and not settings.kokoro_working_dir and not settings.kokoro_python_exe:
            port = urlparse(settings.kokoro_base_url).port or 8880
            return [str(bundled), "--port", str(port)], bundled.parent, os.environ.copy()
        working_dir = Path(settings.kokoro_working_dir or str(BASE_DIR / "runtimes" / "Kokoro-FastAPI")).resolve()
        python_candidates = [
            Path(settings.kokoro_python_exe).resolve() if settings.kokoro_python_exe else None,
            working_dir / ".venv_storydriver_py312" / "Scripts" / "python.exe",
            working_dir / ".venv" / "Scripts" / "python.exe",
        ]
        python = next((candidate for candidate in python_candidates if candidate and candidate.is_file()), None)
        if not working_dir.is_dir() or python is None:
            return [], working_dir, {}
        parsed = urlparse(settings.kokoro_base_url)
        port = parsed.port or 8880
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONHOME": "",
                "PYTHONUTF8": "1",
                "PROJECT_ROOT": str(working_dir),
                "USE_GPU": "false",
                "USE_ONNX": "false",
                "PYTHONPATH": f"{working_dir};{working_dir / 'api'}",
                "MODEL_DIR": "src/models",
                "VOICES_DIR": "src/voices/v1_0",
                "WEB_PLAYER_PATH": str(working_dir / "web"),
                "API_LOG_LEVEL": "INFO",
                "DEVICE": "cpu",
                "TMP": str(DATA_DIR / "temp"),
                "TEMP": str(DATA_DIR / "temp"),
            }
        )
        for candidate in (
            Path(r"C:\Program Files (x86)\eSpeak NG\libespeak-ng.dll"),
            Path(r"C:\Program Files\eSpeak NG\libespeak-ng.dll"),
        ):
            if candidate.is_file():
                environment["PHONEMIZER_ESPEAK_LIBRARY"] = str(candidate)
                break
        return [
            str(python),
            "-m",
            "uvicorn",
            "api.src.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ], working_dir, environment

    def _kokoro_reachable(self) -> bool:
        try:
            with httpx.Client(timeout=0.6, trust_env=False) as client:
                response = client.get(f"{settings.kokoro_base_url.rstrip('/')}/health")
                payload = response.json() if response.status_code == 200 else {}
                return response.status_code == 200 and isinstance(payload, dict) and payload.get("status") in {"healthy", "ok"}
        except (httpx.HTTPError, ValueError):
            return False

    def _refresh_kokoro(self) -> ManagedServiceState:
        with self._lock:
            state = self._states["kokoro"]
            state.last_checked_at = utc_now()
            state.reachable = self._kokoro_reachable()
            if state.reachable:
                state.status = "ready" if state.owned else "external"
                state.ready_at = state.ready_at or utc_now()
                state.error = ""
            else:
                process = self._processes.get("kokoro")
                if process and process.poll() is None:
                    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(state.started_at)).total_seconds() if state.started_at else 0
                    state.status = "error" if elapsed > 120 else "starting"
                    if elapsed > 120:
                        state.error = "Kokoro has not become ready after 120 seconds. Check its paths and log, then restart narration."
                elif state.status != "error":
                    state.status = "stopped"
                    state.pid = None
            return state

    def status(self) -> dict[str, Any]:
        state = self._refresh_kokoro()
        return {
            "desktop_mode": enabled(settings.desktop_mode),
            "auto_start_kokoro": enabled(settings.auto_start_kokoro),
            "services": {"kokoro": asdict(state)},
        }

    def stop(self, name: str) -> dict[str, Any]:
        if name != "kokoro":
            raise ValueError(f"Unknown supervised service: {name}")
        with self._lock:
            state = self._states[name]
            process = self._processes.get(name)
            if not state.owned or process is None:
                return {"ok": True, "stopped": False, "external_preserved": state.reachable}
            if process.poll() is None:
                if os.name == "nt":
                    flags, startup = hidden_process_flags()
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10, creationflags=flags, startupinfo=startup, check=False)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self._processes.pop(name, None)
            state.status = "stopped"
            state.reachable = False
            state.owned = False
            state.pid = None
            return {"ok": True, "stopped": True, "exit_code": process.returncode}

    def shutdown(self) -> None:
        self._stop_event.set()
        try:
            self.stop("kokoro")
        except Exception:
            pass


service_supervisor = StoryDriverServiceSupervisor()
