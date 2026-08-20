from __future__ import annotations

import csv
import ipaddress
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import webbrowser
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = BACKEND_DIR / "data"

DEFAULTS = {
    "OPEN_BROWSER_AFTER_START": "true",
    "OPEN_BROWSER_ALWAYS": "false",
    "STORYDRIVER_FRONTEND_URL": "http://localhost:5173",
    "STORYDRIVER_BACKEND_URL": "http://localhost:8001",
    "LM_STUDIO_OPENAI_BASE_URL": "http://localhost:1234/v1",
    "LM_STUDIO_BASE_URL": "http://localhost:1234/v1",
    "LM_STUDIO_REST_BASE_URL": "http://localhost:1234/api/v1",
    "LM_STUDIO_API_TOKEN": "",
    "LM_STUDIO_START_COMMAND": "",
    "LM_STUDIO_WORKING_DIR": "",
    "LM_STUDIO_WAIT_SECONDS": "45",
    "KOKORO_BASE_URL": "http://localhost:8880",
    "KOKORO_SPEECH_ENDPOINT": "/v1/audio/speech",
    "KOKORO_START_COMMAND": "",
    "KOKORO_WORKING_DIR": "",
    "KOKORO_PYTHON_EXE": "",
    "KOKORO_WAIT_SECONDS": "45",
    "COMFYUI_BASE_URL": "http://localhost:8188",
    "COMFYUI_START_COMMAND": "",
    "COMFYUI_WORKING_DIR": "",
    "COMFYUI_WAIT_SECONDS": "120",
    "COMFYUI_LAUNCH_ARGS": "",
    "COMFYUI_EXPECTED_BACKEND": "unknown",
    "COMFYUI_PERF_NOTES": "",
    "RESTART_EXISTING_SERVICES": "false",
    "STORYDRIVER_NODE_EXE": "",
    "STORYDRIVER_PYTHONHOME": "",
    "STORYDRIVER_FRONTEND_MODE": "preview",
}

CONFIG = dict(DEFAULTS)
BACKEND_URL = DEFAULTS["STORYDRIVER_BACKEND_URL"]
FRONTEND_URL = DEFAULTS["STORYDRIVER_FRONTEND_URL"]
IMAGE_MODE_LABELS = {
    "enabled": "Enabled",
    "paused": "Paused / Coming Soon",
    "manual": "Manual / Advanced",
}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in CONFIG:
            CONFIG[key] = value.strip().strip('"')


def load_config() -> None:
    global BACKEND_URL, FRONTEND_URL
    load_env_file(ROOT / ".env")
    load_env_file(BACKEND_DIR / ".env")
    load_env_file(ROOT / "scripts" / "storydriver.local.env")
    for key in CONFIG:
        value = os.environ.get(key)
        if value:
            CONFIG[key] = value

    if (
        CONFIG["LM_STUDIO_OPENAI_BASE_URL"] == DEFAULTS["LM_STUDIO_OPENAI_BASE_URL"]
        and CONFIG["LM_STUDIO_BASE_URL"] != DEFAULTS["LM_STUDIO_BASE_URL"]
    ):
        CONFIG["LM_STUDIO_OPENAI_BASE_URL"] = CONFIG["LM_STUDIO_BASE_URL"]
    else:
        CONFIG["LM_STUDIO_BASE_URL"] = CONFIG["LM_STUDIO_OPENAI_BASE_URL"]

    BACKEND_URL = CONFIG["STORYDRIVER_BACKEND_URL"].rstrip("/")
    FRONTEND_URL = CONFIG["STORYDRIVER_FRONTEND_URL"].rstrip("/")


def is_true(value: str) -> bool:
    return str(value).lower() in {"1", "true", "yes", "y"}


def int_config(key: str, fallback: int) -> int:
    try:
        return max(0, int(CONFIG.get(key, fallback)))
    except (TypeError, ValueError):
        return fallback


def image_generation_mode() -> str:
    db_path = DATA_DIR / "app.db"
    if not db_path.exists():
        return "paused"
    try:
        with sqlite3.connect(str(db_path), timeout=2) as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                ("image_settings",),
            ).fetchone()
    except Exception:
        return "paused"
    if not row:
        return "paused"
    try:
        data = json.loads(row[0] or "{}")
    except (TypeError, json.JSONDecodeError):
        return "paused"
    mode = str(data.get("image_generation_mode") or "paused").strip().lower()
    return mode if mode in IMAGE_MODE_LABELS else "paused"


def image_generation_paused() -> bool:
    return image_generation_mode() == "paused"


def port_from_url(url: str, fallback: int) -> int:
    try:
        parsed = urlparse(url)
        return parsed.port or fallback
    except ValueError:
        return fallback


def lan_ipv4_candidates() -> list[str]:
    candidates: list[str] = []

    def add_candidate(value: str | None) -> None:
        if not value:
            return
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError:
            return
        if address.version != 4 or address.is_loopback or address.is_link_local:
            return
        if address.is_private and str(address) not in candidates:
            candidates.append(str(address))

    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            add_candidate(item[4][0])
    except Exception:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            add_candidate(sock.getsockname()[0])
    except Exception:
        pass

    try:
        result = run_capture(["ipconfig"], timeout=8)
        for line in result.stdout.splitlines():
            if "IPv4" in line and ":" in line:
                add_candidate(line.split(":", 1)[1].strip())
    except Exception:
        pass

    return candidates


def url_with_host(url: str, host: str, fallback_port: int) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "http"
    port = parsed.port or fallback_port
    return f"{scheme}://{host}:{port}"


def print_lan_access_summary() -> None:
    front_port = port_from_url(FRONTEND_URL, 5173)
    back_port = port_from_url(BACKEND_URL, 8001)
    candidates = lan_ipv4_candidates()
    print("\nStoryDriver access URLs:")
    print(f"  Desktop app:    {FRONTEND_URL}")
    if candidates:
        print(f"  Phone/LAN app:  http://{candidates[0]}:{front_port}")
        print(f"  Backend health: http://{candidates[0]}:{back_port}/health")
        if len(candidates) > 1:
            print(f"  Other LAN IPs:  {', '.join(candidates[1:])}")
    else:
        print("  Phone/LAN app:  No private IPv4 address detected. Run scripts\\diagnose_lan_access.bat.")
        print(f"  Backend health: {url_with_host(BACKEND_URL, 'localhost', back_port)}/health")
    print(f"  Use the {port_from_url(FRONTEND_URL, 5173)} URL for the app. Port {port_from_url(BACKEND_URL, 8001)} is the backend API only.")
    print("  Keep this on your local Wi-Fi/LAN; do not port-forward these services.")


def http_status(detail: str) -> int | None:
    if not detail.startswith("HTTP "):
        return None
    try:
        return int(detail.split()[1])
    except (IndexError, ValueError):
        return None


def raw_http_get(url: str, timeout: float = 4.0, headers: dict[str, str] | None = None) -> tuple[bool, str, str]:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            header_lines = [
                f"GET {path} HTTP/1.1",
                f"Host: {host}",
                "Connection: close",
            ]
            for key, value in (headers or {}).items():
                if value:
                    header_lines.append(f"{key}: {value}")
            request = "\r\n".join(header_lines) + "\r\n\r\n"
            sock.sendall(request.encode("ascii", errors="ignore"))
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        data = b"".join(chunks).decode("utf-8", errors="replace")
        header, _, body = data.partition("\r\n\r\n")
        status_line = header.splitlines()[0] if header else ""
        parts = status_line.split()
        status_code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        ok = 200 <= status_code < 500
        return ok, f"HTTP {status_code}" if status_code else "No HTTP status", body
    except Exception as error:
        return False, str(error), ""


def endpoint_2xx(detail: str) -> bool:
    status = http_status(detail)
    return status is not None and 200 <= status < 300


def run_capture(args: list[str], timeout: int = 8, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )


def listeners_for_port(port: int) -> list[dict]:
    try:
        result = run_capture(["netstat", "-ano", "-p", "tcp"], timeout=10)
    except Exception:
        return []
    listeners: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local_address, state, pid = parts[1], parts[3].upper(), parts[4]
        if state != "LISTENING" or not local_address.endswith(f":{port}"):
            continue
        listeners.append(
            {
                "port": port,
                "address": local_address.rsplit(":", 1)[0],
                "pid": pid,
                "name": process_name(pid),
                "command": command_line(pid),
            }
        )
    return listeners


def process_name(pid: str) -> str:
    try:
        result = run_capture(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=5)
    except Exception:
        return "unknown"
    line = result.stdout.strip().splitlines()
    if not line or "INFO:" in line[0]:
        return "unknown"
    return line[0].split('","')[0].strip('"') or "unknown"


def command_line(pid: str) -> str:
    if not str(pid).isdigit():
        return ""
    try:
        result = run_capture(
            ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
            timeout=5,
        )
    except Exception:
        result = None
    for line in (result.stdout if result else "").splitlines():
        if line.startswith("CommandLine="):
            return line.split("=", 1)[1].strip()
    try:
        fallback = run_capture(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            timeout=8,
        )
        return fallback.stdout.strip()
    except Exception:
        pass
    return ""


def configured_processes(command: str, working_dir: str) -> list[dict]:
    command_hints: list[str] = []
    working_dir_hint = working_dir.lower() if working_dir.strip() else ""
    command_head = command.strip().split(maxsplit=1)[0].strip('"')
    command_name = Path(command_head).name.lower()
    if command_name and command_name not in {"cmd.exe", "python.exe", "python", "py", "uv", "uv.exe"}:
        command_hints.append(command_name)
        if "\\" in command_head or "/" in command_head:
            command_hints.append(command_head.lower())
    if not command_hints and not working_dir_hint:
        return []
    try:
        output = run_capture(
            ["wmic", "process", "get", "ProcessId,Name,ExecutablePath,CommandLine", "/format:csv"],
            timeout=8,
        ).stdout
    except Exception:
        return []
    matches: list[dict] = []
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return matches
    reader = csv.DictReader(StringIO("\n".join(lines)))
    for row in reader:
        text = " ".join(str(row.get(key, "")) for key in ("CommandLine", "ExecutablePath", "Name")).lower()
        if command_hints:
            if any(hint and hint in text for hint in command_hints):
                matches.append(row)
            continue
        if working_dir_hint and working_dir_hint in text:
            matches.append(row)
    return matches


def format_process_matches(items: list[dict]) -> str:
    if not items:
        return "none"
    parts = []
    for item in items[:4]:
        parts.append(f"{item.get('ProcessId', '?')} ({item.get('Name', 'unknown')})")
    if len(items) > 4:
        parts.append(f"+{len(items) - 4} more")
    return ", ".join(parts)


def stop_configured_processes(items: list[dict], label: str) -> None:
    for item in items:
        pid = str(item.get("ProcessId") or "").strip()
        if not pid.isdigit():
            continue
        name = str(item.get("Name") or "process")
        print(f"Stopping stale {label} launcher PID {pid} ({name}).")
        subprocess.run(
            ["taskkill", "/PID", pid, "/F"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def format_listeners(items: list[dict]) -> str:
    if not items:
        return "no listener"
    return ", ".join(f"{item['address']}:{item['pid']} ({item['name']})" for item in items)


def looks_like_comfyui_listener(listener: dict) -> bool:
    haystack = " ".join(
        [
            str(listener.get("name") or ""),
            str(listener.get("command") or ""),
        ]
    ).lower()
    return "comfyui" in haystack or "comfy desktop" in haystack or "comfyui\\main.py" in haystack or "comfyui/main.py" in haystack


def auth_headers() -> dict[str, str]:
    token = CONFIG.get("LM_STUDIO_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def check_any_url(base: str, paths: list[str], require_2xx: bool = False, timeout: float = 4.0) -> tuple[bool, str]:
    last_detail = "No response."
    for path in paths:
        _, detail, _ = raw_http_get(f"{base}{path}", timeout=timeout)
        if require_2xx:
            if endpoint_2xx(detail):
                return True, detail
        elif http_status(detail) is not None:
            return True, detail
        last_detail = detail
        if http_status(detail) is None:
            return False, last_detail
    return False, last_detail


def loopback_base_variants(base: str) -> list[str]:
    base = base.rstrip("/")
    variants = [base]
    try:
        parsed = urlparse(base)
    except ValueError:
        return variants
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return variants
    scheme = parsed.scheme or "http"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    for host in ("127.0.0.1", "localhost"):
        candidate = f"{scheme}://{host}{port}{path}"
        if candidate not in variants:
            variants.append(candidate)
    return variants


def check_any_url_variants(bases: list[str], paths: list[str], timeout: float = 4.0) -> tuple[bool, str]:
    last_detail = "No response."
    for base in bases:
        ok, detail = check_any_url(base.rstrip("/"), paths, timeout=timeout)
        if ok:
            return True, f"{detail} via {base.rstrip('/')}"
        last_detail = f"{detail} via {base.rstrip('/')}"
    return False, last_detail


def comfyui_bases() -> list[str]:
    return loopback_base_variants(CONFIG["COMFYUI_BASE_URL"].rstrip("/"))


def service_state(kind: str) -> dict:
    listeners = None
    if kind == "backend":
        port = port_from_url(BACKEND_URL, 8001)
        _, detail, body = raw_http_get(f"{BACKEND_URL}/health")
        ok = endpoint_2xx(detail) and "StoryDriver" in body
    elif kind == "frontend":
        port = port_from_url(FRONTEND_URL, 5173)
        _, detail, body = raw_http_get(FRONTEND_URL)
        ok = endpoint_2xx(detail) and "StoryDriver" in body
    elif kind in {"lmstudio", "lmstudio_openai"}:
        port = port_from_url(CONFIG["LM_STUDIO_OPENAI_BASE_URL"], 1234)
        base = CONFIG["LM_STUDIO_OPENAI_BASE_URL"].rstrip("/")
        _, detail, _ = raw_http_get(f"{base}/models")
        ok = endpoint_2xx(detail)
    elif kind == "lmstudio_rest":
        port = port_from_url(CONFIG["LM_STUDIO_REST_BASE_URL"], 1234)
        base = CONFIG["LM_STUDIO_REST_BASE_URL"].rstrip("/")
        _, detail, _ = raw_http_get(f"{base}/models", headers=auth_headers())
        status = http_status(detail)
        if status in {401, 403}:
            detail = f"{detail}. Set LM_STUDIO_API_TOKEN if this endpoint requires auth."
        ok = endpoint_2xx(detail)
    elif kind == "kokoro":
        port = port_from_url(CONFIG["KOKORO_BASE_URL"], 8880)
        ok, detail = check_any_url(CONFIG["KOKORO_BASE_URL"].rstrip("/"), ["", "/docs", "/health"])
    else:
        port = port_from_url(CONFIG["COMFYUI_BASE_URL"], 8188)
        listeners = listeners_for_port(port)
        if listeners:
            ok, detail = check_any_url_variants(comfyui_bases(), ["/system_stats", "/queue", ""], timeout=1.0)
        else:
            ok, detail = False, "No listener on the configured ComfyUI port."

    if listeners is None:
        listeners = listeners_for_port(port)
    status = "OK" if ok else "Port occupied but unexpected" if listeners else "Offline"
    return {"kind": kind, "port": port, "ok": ok, "status": status, "detail": detail, "listeners": listeners}


def endpoint_reachable(kind: str) -> bool:
    if kind == "backend":
        _, detail, body = raw_http_get(f"{BACKEND_URL}/health", timeout=1.0)
        return endpoint_2xx(detail) and "StoryDriver" in body
    if kind == "frontend":
        _, detail, body = raw_http_get(FRONTEND_URL, timeout=1.0)
        return endpoint_2xx(detail) and "StoryDriver" in body
    if kind in {"lmstudio", "lmstudio_openai"}:
        base = CONFIG["LM_STUDIO_OPENAI_BASE_URL"].rstrip("/")
        _, detail, _ = raw_http_get(f"{base}/models", timeout=1.0)
        return endpoint_2xx(detail)
    if kind == "lmstudio_rest":
        base = CONFIG["LM_STUDIO_REST_BASE_URL"].rstrip("/")
        _, detail, _ = raw_http_get(f"{base}/models", timeout=1.0, headers=auth_headers())
        return endpoint_2xx(detail)
    if kind == "kokoro":
        base = CONFIG["KOKORO_BASE_URL"].rstrip("/")
        for path in ("/docs", "/health"):
            _, detail, _ = raw_http_get(f"{base}{path}", timeout=0.75)
            if http_status(detail) is not None:
                return True
        return False
    ok, _ = check_any_url_variants(comfyui_bases(), ["/system_stats", "/queue", ""], timeout=2.0)
    return ok


def show_state(name: str, state: dict, url: str) -> None:
    print(f"[{state['status']}] {name:<22} {url}")
    print(f"      PIDs: {format_listeners(state['listeners'])}")
    if state["detail"] and not state["ok"]:
        print(f"      Detail: {state['detail']}")
    next_steps = {
        "backend": "Run scripts\\start_backend.bat.",
        "frontend": "Run scripts\\start_frontend.bat.",
        "lmstudio": "Start LM Studio Server or configure LM_STUDIO_START_COMMAND.",
        "lmstudio_openai": "Start LM Studio Server or configure LM_STUDIO_START_COMMAND.",
        "lmstudio_rest": (
            "REST model management is optional while images are paused; writing uses the OpenAI endpoint."
            if image_generation_paused()
            else "Balanced and Manual still work; Auto Image Priority needs the LM Studio REST API for unload/reload."
        ),
        "kokoro": "Kokoro offline. Browser fallback may sound robotic if enabled.",
        "comfyui": "Open ComfyUI Desktop and wait for the backend, then refresh. StoryDriver does not force-launch ComfyUI Desktop by default.",
    }
    if state["ok"]:
        print("      Next: Ready.")
    else:
        print(f"      Next: {next_steps.get(state['kind'], 'Check the service configuration.')}")


def show_comfyui_state_for_mode() -> None:
    mode = image_generation_mode()
    state = service_state("comfyui")
    if mode == "paused":
        print(f"[Paused] {'ComfyUI':<22} {CONFIG['COMFYUI_BASE_URL']}")
        print(f"      PIDs: {format_listeners(state['listeners'])}")
        print("      Next: Image generation paused. Existing images remain available.")
        return
    show_state("ComfyUI", state, CONFIG["COMFYUI_BASE_URL"])


def is_owned(listener: dict, kind: str) -> bool:
    command = listener.get("command", "").lower()
    root = str(ROOT).lower()
    if not command:
        return False
    if kind == "backend":
        if "app.main:app" in command:
            _, _, body = raw_http_get(f"{BACKEND_URL}/health", timeout=2.0)
            return "StoryDriver" in body
        return root in command and ("uvicorn" in command or "app.main:app" in command)
    if kind == "frontend":
        vite_path = str(FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js").lower()
        return (
            root in command and ("vite" in command or "node" in command or "npm" in command)
        ) or vite_path in command
    if kind == "kokoro":
        working_dir = CONFIG["KOKORO_WORKING_DIR"].lower()
        configured_python = CONFIG.get("KOKORO_PYTHON_EXE", "").lower()
        kokoro_port = str(port_from_url(CONFIG["KOKORO_BASE_URL"], 8880))
        return bool(
            (working_dir and working_dir in command)
            or (configured_python and configured_python in command and f"--port {kokoro_port}" in command)
            or ("api.src.main:app" in command and f"--port {kokoro_port}" in command)
        )
    return False


def stop_owned(kind: str, name: str, port: int) -> None:
    listeners = listeners_for_port(port)
    if not listeners:
        print(f"{name} is not running on port {port}.")
        return
    for listener in listeners:
        if not is_owned(listener, kind):
            print(f"Leaving PID {listener['pid']} ({listener['name']}) alone; it is not clearly {name}.")
            if listener.get("command"):
                print(f"      Command: {listener['command']}")
            continue
        print(f"Stopping {name} PID {listener['pid']} ({listener['name']}).")
        subprocess.run(
            ["taskkill", "/PID", str(listener["pid"]), "/F"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def start_detached(args, cwd: Path, env: dict[str, str] | None = None, shell: bool = False) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        shell=shell,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def start_command_window(command: str, working_dir: Path, title: str) -> None:
    if os.name == "nt":
        import ctypes

        parameters = f'/k title "{title}" && {command}'
        result = ctypes.windll.shell32.ShellExecuteW(None, "open", "cmd.exe", parameters, str(working_dir), 1)
        if result <= 32:
            raise RuntimeError(f"Windows ShellExecute failed with code {result}")
    else:
        subprocess.Popen(
            command,
            cwd=str(working_dir),
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )


def working_dir_from_config(key: str, service_name: str) -> Path:
    configured = CONFIG.get(key, "").strip()
    if not configured:
        return ROOT
    path = Path(configured)
    if path.exists():
        return path
    print(f"{service_name} working directory does not exist: {path}")
    print("Starting from D:\\StoryDriver instead.")
    return ROOT


def wait_for_service(kind: str, wait_seconds: int, label: str) -> bool:
    if wait_seconds <= 0:
        return endpoint_reachable(kind)
    print(f"Waiting up to {wait_seconds} seconds for {label} ...")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        if endpoint_reachable(kind):
            print(f"{label} is reachable.")
            return True
    return False


def comfyui_backend_preflight(command: str, working_dir: Path) -> tuple[bool, str]:
    if "run_comfyui_desktop_backend_8188.bat" not in command.lower():
        return True, ""
    python = working_dir / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        return (
            False,
            f"Configured ComfyUI backend-only launcher cannot run because {python} is missing.",
        )
    missing: list[str] = []
    for module in ("sqlalchemy", "torch"):
        try:
            result = subprocess.run(
                [str(python), "-c", f"import {module}"],
                cwd=str(working_dir),
                capture_output=True,
                text=True,
                timeout=20,
                errors="replace",
            )
            if result.returncode != 0:
                missing.append(module)
        except Exception:
            missing.append(module)
    if missing:
        return (
            False,
            "Configured ComfyUI backend-only launcher cannot run because "
            f"{python} cannot import: {', '.join(missing)}. "
            "Use ComfyUI Desktop repair/update or a proven backend command before enabling auto-start.",
        )
    return True, ""


def candidate_python_homes() -> list[Path]:
    candidates: list[Path] = []
    if CONFIG["STORYDRIVER_PYTHONHOME"]:
        candidates.append(Path(CONFIG["STORYDRIVER_PYTHONHOME"]))
    vendor_root = Path.home() / ".lmstudio" / "extensions" / "backends" / "vendor"
    if vendor_root.exists():
        candidates.extend(sorted((path.parent for path in vendor_root.glob("**/python.exe")), reverse=True))
    return candidates


def backend_python_env() -> tuple[Path | None, dict[str, str] | None, str]:
    python = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        return None, None, "Backend venv is missing. Run dependency setup before starting the backend."
    base_env = os.environ.copy()
    base_env.update(CONFIG)
    code = "import html.entities, ssl, fastapi, uvicorn; print('ok')"
    if test_python(python, code, base_env):
        return python, base_env, "project venv"
    for home in candidate_python_homes():
        env = base_env.copy()
        env["PYTHONHOME"] = str(home)
        if test_python(python, code, env):
            return python, env, f"project venv with PYTHONHOME={home}"
    return python, base_env, "project venv has missing Python runtime files"


def test_python(python: Path, code: str, env: dict[str, str]) -> bool:
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def find_node() -> Path | None:
    candidates: list[Path] = []
    if CONFIG["STORYDRIVER_NODE_EXE"]:
        candidates.append(Path(CONFIG["STORYDRIVER_NODE_EXE"]))
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.append(Path(program_files) / "nodejs" / "node.exe")
    which_node = shutil.which("node")
    if which_node:
        candidates.append(Path(which_node))
    codex_bin = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    if codex_bin.exists():
        candidates.extend(sorted(codex_bin.glob("*\\node.exe"), key=lambda path: path.stat().st_mtime, reverse=True))
    codex_runtime_node = (
        Path(os.environ.get("USERPROFILE", ""))
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "bin"
        / "node.exe"
    )
    candidates.append(codex_runtime_node)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen or not candidate.exists():
            continue
        seen.add(key)
        try:
            result = subprocess.run([str(candidate), "-v"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return candidate
        except Exception:
            continue
    return None


def start_backend() -> bool:
    state = service_state("backend")
    if state["ok"]:
        print("Backend already running.")
        return False
    if state["listeners"]:
        print(f"Port {state['port']} is occupied but does not look like StoryDriver.")
        print(f"PIDs: {format_listeners(state['listeners'])}")
        if any(looks_like_comfyui_listener(listener) for listener in state["listeners"]):
            print(f"That listener looks like ComfyUI Desktop. StoryDriver keeps backend port {state['port']}.")
            print("Next: close/restart ComfyUI Desktop after setting its launch args to port 8188, then relaunch StoryDriver.exe.")
        return False
    python, env, note = backend_python_env()
    if not python or not env:
        print(note)
        return False
    if "missing Python runtime files" in note:
        print("Backend cannot start: project venv Python runtime is incomplete.")
        print("Next: repair Python 3.11 or set STORYDRIVER_PYTHONHOME to a complete Python 3.11 runtime.")
        return False
    port = port_from_url(BACKEND_URL, 8001)
    print(f"Starting backend ({note}) on {BACKEND_URL} ...")
    start_detached([str(python), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port)], BACKEND_DIR, env)
    return True


def start_frontend() -> bool:
    state = service_state("frontend")
    if state["ok"]:
        print("Frontend already running.")
        return False
    if state["listeners"]:
        print(f"Port {state['port']} is occupied but does not look like StoryDriver/Vite.")
        print(f"PIDs: {format_listeners(state['listeners'])}")
        return False
    node = find_node()
    vite = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not node:
        print("No working node.exe was found. Install Node.js or set STORYDRIVER_NODE_EXE.")
        return False
    if not vite.exists():
        print("Frontend dependencies are missing. Run npm install in D:\\StoryDriver\\frontend.")
        return False
    port = port_from_url(FRONTEND_URL, 5173)
    mode = CONFIG.get("STORYDRIVER_FRONTEND_MODE", "preview").strip().lower()
    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if mode != "dev" and dist_index.exists():
        print(f"Starting frontend preview with {node} on {FRONTEND_URL} ...")
        start_detached([str(node), str(vite), "preview", "--host", "0.0.0.0", "--port", str(port)], FRONTEND_DIR)
    else:
        if mode != "dev":
            print("Frontend dist build is missing; falling back to Vite dev server. Run npm.cmd run build for stable LAN/mobile preview.")
        print(f"Starting frontend dev server with {node} on {FRONTEND_URL} ...")
        start_detached([str(node), str(vite), "--host", "0.0.0.0", "--port", str(port)], FRONTEND_DIR)
    return True


def start_kokoro() -> bool:
    state = service_state("kokoro")
    if state["ok"]:
        print("Kokoro is already reachable.")
        return False
    command = CONFIG["KOKORO_START_COMMAND"].strip()
    if not command:
        print("Kokoro is not reachable and KOKORO_START_COMMAND is not configured.")
        print("Kokoro offline. Browser fallback may sound robotic if enabled.")
        return False
    if state["listeners"]:
        print(f"Kokoro port {state['port']} is occupied but did not respond as expected.")
        print(f"PIDs: {format_listeners(state['listeners'])}")
        return False
    working_dir = working_dir_from_config("KOKORO_WORKING_DIR", "Kokoro")
    running = configured_processes(command, str(working_dir))
    if running:
        print("A Kokoro launcher window exists, but the service is not reachable.")
        print(f"PIDs: {format_process_matches(running)}")
        print("Clearing stale Kokoro launcher window before starting a fresh server.")
        stop_configured_processes(running, "Kokoro")
        time.sleep(1)
    print("Starting Kokoro-FastAPI ...")
    try:
        start_command_window(command, working_dir, "StoryDriver Kokoro")
    except Exception as error:
        print(f"Could not launch Kokoro: {error}")
        return False
    if wait_for_service("kokoro", int_config("KOKORO_WAIT_SECONDS", 45), "Kokoro"):
        return True
    print("Kokoro was launched, but it is not reachable yet.")
    print("Check the Kokoro window or run scripts\\test_kokoro.bat.")
    return True


def start_lmstudio() -> bool:
    openai = service_state("lmstudio_openai")
    rest = service_state("lmstudio_rest")
    if openai["ok"]:
        print("LM Studio server is already reachable.")
        if rest["ok"]:
            print("LM Studio REST model-management endpoint is reachable.")
        else:
            print("LM Studio REST is not reachable. Balanced and Manual resource modes still work.")
        return False
    if openai["listeners"] or rest["listeners"]:
        print("LM Studio appears to have a listener, but the OpenAI-compatible server is not reachable.")
        print(f"PIDs: {format_listeners(openai['listeners'] or rest['listeners'])}")
        print("Start LM Studio Server inside LM Studio or configure an LM Studio CLI/server command.")
        return False

    command = CONFIG["LM_STUDIO_START_COMMAND"].strip()
    if not command:
        print("LM Studio is not reachable and LM_STUDIO_START_COMMAND is not configured.")
        print("Start LM Studio manually and start the local server, or set LM_STUDIO_START_COMMAND in D:\\StoryDriver\\.env.")
        return False

    working_dir = working_dir_from_config("LM_STUDIO_WORKING_DIR", "LM Studio")
    running = configured_processes(command, str(working_dir))
    if running:
        print("LM Studio launch command appears to be running, but the server is not reachable.")
        print(f"PIDs: {format_process_matches(running)}")
        print("Start LM Studio Server inside LM Studio or configure an LM Studio CLI/server command.")
        return False
    print("Starting LM Studio ...")
    try:
        start_command_window(command, working_dir, "StoryDriver LM Studio")
    except Exception as error:
        print(f"Could not launch LM Studio: {error}")
        return False
    if wait_for_service("lmstudio_openai", int_config("LM_STUDIO_WAIT_SECONDS", 45), "LM Studio"):
        rest = service_state("lmstudio_rest")
        if rest["ok"]:
            print("LM Studio REST model-management endpoint is reachable.")
        else:
            print("LM Studio OpenAI endpoint is reachable, but REST model management is not.")
        return True
    print("LM Studio opened, but server is not reachable. Start LM Studio Server inside LM Studio or configure an LM Studio CLI/server command.")
    return True


def start_comfyui() -> bool:
    state = service_state("comfyui")
    if state["ok"]:
        print("ComfyUI is already reachable.")
        return False
    if state["listeners"]:
        print(f"ComfyUI port {state['port']} is occupied but did not respond as expected.")
        print(f"PIDs: {format_listeners(state['listeners'])}")
        return False

    command = CONFIG["COMFYUI_START_COMMAND"].strip()
    if not command:
        print("ComfyUI is not reachable and COMFYUI_START_COMMAND is not configured.")
        print("Open ComfyUI Desktop manually, wait until the backend is ready, then refresh StoryDriver.")
        print(f"Expected Desktop backend: {CONFIG['COMFYUI_BASE_URL']}")
        print(r"Desktop executable: C:\Users\wngar\AppData\Local\Programs\ComfyUI\Comfy Desktop\Comfy Desktop.exe")
        print("Run scripts\\diagnose_comfyui.bat for discovered installs, port checks, and recent crash logs.")
        return False

    working_dir = working_dir_from_config("COMFYUI_WORKING_DIR", "ComfyUI")
    running = configured_processes(command, str(working_dir))
    if running:
        print("ComfyUI launch command appears to be running, but the service is not reachable.")
        print(f"PIDs: {format_process_matches(running)}")
        print("Check the ComfyUI window; StoryDriver does not modify ComfyUI internals or GPU settings.")
        return False
    preflight_ok, preflight_detail = comfyui_backend_preflight(command, working_dir)
    if not preflight_ok:
        print(preflight_detail)
        print("ComfyUI was not launched. StoryDriver will still work; Image Settings will show ComfyUI offline.")
        print("Run scripts\\diagnose_comfyui.bat for current details.")
        return False
    print("Starting ComfyUI ...")
    try:
        start_command_window(command, working_dir, "StoryDriver ComfyUI")
    except Exception as error:
        print(f"Could not launch ComfyUI: {error}")
        return False
    if wait_for_service("comfyui", int_config("COMFYUI_WAIT_SECONDS", 60), "ComfyUI"):
        return True
    print("ComfyUI was launched, but it is not reachable yet.")
    print("Check the ComfyUI window; StoryDriver does not modify ComfyUI internals or GPU settings.")
    print("Run scripts\\diagnose_comfyui_launch.bat to capture launch output.")
    return True


def check_services() -> None:
    print("Checking StoryDriver local services...\n")
    show_state("StoryDriver Backend", service_state("backend"), f"{BACKEND_URL}/health")
    show_state("StoryDriver Frontend", service_state("frontend"), FRONTEND_URL)
    show_state("Kokoro", service_state("kokoro"), CONFIG["KOKORO_BASE_URL"])
    lm_base = CONFIG["LM_STUDIO_OPENAI_BASE_URL"].rstrip("/")
    show_state("LM Studio OpenAI", service_state("lmstudio_openai"), f"{lm_base}/models")
    lm_rest_base = CONFIG["LM_STUDIO_REST_BASE_URL"].rstrip("/")
    show_state("LM Studio REST", service_state("lmstudio_rest"), f"{lm_rest_base}/models")
    print_lan_access_summary()
    print("\nDone.")


def open_browser(force: bool = True) -> bool:
    backend = service_state("backend")
    frontend = service_state("frontend")
    if not backend["ok"]:
        print("Not opening browser because the backend is offline.")
        return False
    if not frontend["ok"]:
        print("Not opening browser because the frontend is offline.")
        return False
    if not force:
        print("Browser auto-open skipped to avoid a duplicate tab. Run scripts\\open_storydriver.bat to open it anyway.")
        return False
    print(f"Opening StoryDriver in your browser: {FRONTEND_URL}")
    try:
        if os.name == "nt":
            os.startfile(FRONTEND_URL)  # type: ignore[attr-defined]
        else:
            webbrowser.open_new_tab(FRONTEND_URL)
        return True
    except Exception as error:
        print(f"Could not open browser automatically: {error}")
        return False


def start_all() -> None:
    print("Starting StoryDriver local app...")
    print(f"RESTART_EXISTING_SERVICES={CONFIG['RESTART_EXISTING_SERVICES']}\n")
    print("Writing and narration services are the priority: backend, frontend, Kokoro, and LM Studio.")
    if is_true(CONFIG["RESTART_EXISTING_SERVICES"]):
        stop_storydriver()
        time.sleep(1)

    backend_started = start_backend()
    frontend_started = start_frontend()
    start_kokoro()
    start_lmstudio()

    print("\nWaiting for StoryDriver backend/frontend ...")
    for _ in range(120):
        time.sleep(1)
        if endpoint_reachable("backend") and endpoint_reachable("frontend"):
            break
    print("")
    check_services()

    if is_true(CONFIG["OPEN_BROWSER_AFTER_START"]):
        should_open = is_true(CONFIG["OPEN_BROWSER_ALWAYS"]) or backend_started or frontend_started
        open_browser(force=should_open)


def stop_storydriver() -> None:
    print("Stopping safely identifiable StoryDriver services...\n")
    stop_owned("backend", "StoryDriver backend", port_from_url(BACKEND_URL, 8001))
    stop_owned("frontend", "StoryDriver frontend", port_from_url(FRONTEND_URL, 5173))
    stop_owned("kokoro", "Kokoro-FastAPI", port_from_url(CONFIG["KOKORO_BASE_URL"], 8880))
    print("\nLM Studio is not stopped by StoryDriver.")


def run_backend_foreground() -> int:
    python, env, note = backend_python_env()
    if not python or not env or "missing Python runtime files" in note:
        print(note)
        return 1
    port = port_from_url(BACKEND_URL, 8001)
    print(f"Starting StoryDriver backend ({note}) on {BACKEND_URL} ...")
    return subprocess.call(
        [str(python), "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(BACKEND_DIR),
        env=env,
    )


def run_frontend_foreground() -> int:
    node = find_node()
    vite = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not node:
        print("No working node.exe was found. Install Node.js or set STORYDRIVER_NODE_EXE.")
        return 1
    port = port_from_url(FRONTEND_URL, 5173)
    mode = CONFIG.get("STORYDRIVER_FRONTEND_MODE", "preview").strip().lower()
    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if mode != "dev" and dist_index.exists():
        print(f"Starting StoryDriver frontend preview with {node} on {FRONTEND_URL} ...")
        return subprocess.call([str(node), str(vite), "preview", "--host", "0.0.0.0", "--port", str(port)], cwd=str(FRONTEND_DIR))
    if mode != "dev":
        print("Frontend dist build is missing; falling back to Vite dev server. Run npm.cmd run build for stable LAN/mobile preview.")
    print(f"Starting StoryDriver frontend dev server with {node} on {FRONTEND_URL} ...")
    return subprocess.call([str(node), str(vite), "--host", "0.0.0.0", "--port", str(port)], cwd=str(FRONTEND_DIR))


def main() -> int:
    load_config()
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    if action == "start-all":
        start_all()
    elif action == "start-backend":
        start_backend()
    elif action == "start-frontend":
        start_frontend()
    elif action == "start-kokoro":
        start_kokoro()
    elif action == "start-lmstudio":
        start_lmstudio()
    elif action == "start-comfyui":
        start_comfyui()
    elif action == "check":
        check_services()
    elif action == "stop":
        stop_storydriver()
    elif action == "restart":
        stop_storydriver()
        time.sleep(1)
        start_all()
    elif action == "open-browser":
        open_browser(force=True)
    elif action == "run-backend":
        return run_backend_foreground()
    elif action == "run-frontend":
        return run_frontend_foreground()
    else:
        print(f"Unknown action: {action}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
