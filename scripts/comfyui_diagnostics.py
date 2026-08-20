from __future__ import annotations

import csv
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
WORKFLOW_DIR = DATA_DIR / "comfy_workflows"
DIAGNOSTICS_LOG = LOG_DIR / "comfyui_diagnostics.txt"
LAUNCH_LOG = LOG_DIR / "comfyui_launch_diagnostics.txt"

DEFAULTS = {
    "COMFYUI_BASE_URL": "http://localhost:8188",
    "COMFYUI_WORKING_DIR": "",
    "COMFYUI_START_COMMAND": "",
    "COMFYUI_WAIT_SECONDS": "120",
    "COMFYUI_LAUNCH_ARGS": "",
    "COMFYUI_EXPECTED_BACKEND": "unknown",
    "COMFYUI_PERF_NOTES": "",
}

CONFIG = dict(DEFAULTS)


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
    load_env_file(ROOT / ".env")
    load_env_file(BACKEND_DIR / ".env")
    load_env_file(ROOT / "scripts" / "storydriver.local.env")
    for key in CONFIG:
        value = os.environ.get(key)
        if value:
            CONFIG[key] = value


def run_capture(args: list[str], timeout: int = 10, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        errors="replace",
    )


def port_from_url(url: str, fallback: int = 8188) -> int:
    try:
        return urlparse(url).port or fallback
    except ValueError:
        return fallback


def raw_http(method: str, url: str, body: str = "", timeout: float = 4.0) -> tuple[bool, str, str]:
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        payload = body.encode("utf-8")
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            headers = [
                f"{method} {path} HTTP/1.1",
                f"Host: {host}",
                "Connection: close",
            ]
            if payload:
                headers.extend(
                    [
                        "Content-Type: application/json",
                        f"Content-Length: {len(payload)}",
                    ]
                )
            request = "\r\n".join(headers) + "\r\n\r\n"
            sock.sendall(request.encode("ascii", errors="ignore") + payload)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        header, _, content = text.partition("\r\n\r\n")
        status_line = header.splitlines()[0] if header else ""
        parts = status_line.split()
        code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return 200 <= code < 500, f"HTTP {code}" if code else "No HTTP status", content
    except Exception as error:
        return False, str(error), ""


def loopback_base_variants(base_url: str | None = None) -> list[str]:
    base = (base_url or CONFIG["COMFYUI_BASE_URL"]).rstrip("/")
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


def reachable_base(base_url: str | None = None) -> tuple[str | None, str]:
    last_detail = "No response."
    for base in loopback_base_variants(base_url):
        for path in ("/system_stats", "/queue", "/"):
            _, detail, _ = raw_http("GET", f"{base}{path}", timeout=2.0)
            if detail.startswith("HTTP "):
                return base, f"{detail} via {base}{path}"
            last_detail = f"{detail} via {base}{path}"
            break
    return None, last_detail


def endpoint_reachable(base_url: str | None = None) -> bool:
    base, _ = reachable_base(base_url)
    return base is not None


def configured_backend_preflight() -> tuple[bool, str]:
    command = CONFIG["COMFYUI_START_COMMAND"].strip().lower()
    if "run_comfyui_desktop_backend_8188.bat" not in command:
        return True, ""
    working_dir = Path(CONFIG["COMFYUI_WORKING_DIR"].strip() or ROOT)
    python = working_dir / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        return False, f"{python} is missing."
    missing: list[str] = []
    for module in ("sqlalchemy", "torch"):
        try:
            result = run_capture([str(python), "-c", f"import {module}"], timeout=20, cwd=working_dir)
            if result.returncode != 0:
                missing.append(module)
        except Exception:
            missing.append(module)
    if missing:
        return False, f"{python} cannot import: {', '.join(missing)}."
    return True, ""


def process_name(pid: str) -> str:
    try:
        result = run_capture(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=5)
    except Exception:
        return "unknown"
    lines = result.stdout.strip().splitlines()
    if not lines or "INFO:" in lines[0]:
        return "unknown"
    return lines[0].split('","')[0].strip('"') or "unknown"


def command_line(pid: str) -> str:
    try:
        result = run_capture(["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"], timeout=5)
    except Exception:
        return ""
    for line in result.stdout.splitlines():
        if line.startswith("CommandLine="):
            return line.split("=", 1)[1].strip()
    return ""


def listeners_for_ports(ports: list[int]) -> list[dict]:
    try:
        result = run_capture(["netstat", "-ano", "-p", "tcp"], timeout=10)
    except Exception:
        return []
    wanted = {str(port) for port in ports if port}
    listeners: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        local_address, pid = parts[1], parts[4]
        if ":" not in local_address:
            continue
        port = local_address.rsplit(":", 1)[1]
        if port not in wanted:
            continue
        listeners.append(
            {
                "port": int(port),
                "address": local_address.rsplit(":", 1)[0],
                "pid": pid,
                "name": process_name(pid),
                "command": command_line(pid),
            }
        )
    return listeners


def all_processes() -> list[dict]:
    try:
        result = run_capture(["wmic", "process", "get", "ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine", "/format:csv"], timeout=15)
    except Exception:
        return []
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        return []
    processes: list[dict] = []
    try:
        reader = csv.DictReader(StringIO("\n".join(rows)))
        for row in reader:
            processes.append({key: (value or "") for key, value in row.items()})
    except Exception:
        return []
    return processes


def comfy_processes() -> list[dict]:
    matches: list[dict] = []
    for process in all_processes():
        name = process.get("Name", "")
        command = process.get("CommandLine", "")
        exe = process.get("ExecutablePath", "")
        text = f"{name} {command} {exe}".lower()
        if "comfyui_diagnostics.py" in text or "diagnose_comfyui.bat" in text:
            continue
        if "comfyui" not in text and "\\comfyui\\" not in text:
            continue
        if not (
            name.lower() in {"comfyui.exe", "python.exe", "pythonw.exe", "cmd.exe"}
            or "main.py" in command.lower()
            or "comfyui.exe" in exe.lower()
        ):
            continue
        safe_reason = safe_stop_reason(process)
        port = None
        match = re.search(r"--port\s+(\d+)", command)
        if match:
            port = int(match.group(1))
        matches.append(
            {
                "pid": process.get("ProcessId", ""),
                "parent_pid": process.get("ParentProcessId", ""),
                "name": name,
                "exe": exe,
                "command": command,
                "server_port": port,
                "safe_to_stop": bool(safe_reason),
                "safe_reason": safe_reason,
            }
        )
    return sorted(matches, key=lambda item: int(item["pid"] or 0))


def safe_stop_reason(process: dict) -> str:
    command = str(process.get("CommandLine", ""))
    exe = str(process.get("ExecutablePath", ""))
    name = str(process.get("Name", ""))
    text = f"{command} {exe}".lower()
    configured = CONFIG["COMFYUI_WORKING_DIR"].strip().lower()
    storydriver_scripts = str(ROOT / "scripts").lower()
    if storydriver_scripts in text and (
        "run_comfyui_desktop_backend_8188.bat" in text
        or "launch_comfyui_desktop_debug.bat" in text
        or "diagnose_comfyui_launch.bat" in text
    ):
        return "StoryDriver ComfyUI launcher"
    if configured and configured in text:
        return "configured COMFYUI_WORKING_DIR"
    if re.search(r"(\\|/)comfyui(\\|/)main\.py", command, re.IGNORECASE):
        return "ComfyUI main.py command line"
    if name.lower() == "comfyui.exe" and "\\programs\\comfyui\\comfyui.exe" in exe.lower():
        return "ComfyUI Desktop executable"
    if "\\documents\\comfyui\\" in text and "comfyui" in text:
        return "Documents ComfyUI user directory"
    return ""


def recent_logs() -> list[Path]:
    roots = [
        Path.home() / "AppData" / "Roaming" / "ComfyUI" / "logs",
        Path.home() / "Documents" / "ComfyUI" / "user",
    ]
    logs: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("comfyui*.log", "*.log"):
            logs.extend(path for path in root.glob(pattern) if path.is_file())
    unique = {str(path).lower(): path for path in logs}
    return sorted(unique.values(), key=lambda path: path.stat().st_mtime, reverse=True)


def tail_text(path: Path, max_lines: int = 80) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as error:
        return [f"Could not read {path}: {error}"]
    return lines[-max_lines:]


def interesting_log_lines(path: Path, max_lines: int = 120) -> list[str]:
    pattern = re.compile(
        r"error|exception|traceback|failed|crash|port|listen|8188|8000|started|to see the gui|cuda|rocm|directml|amd|vram|memory|python|torch|server|import failed|no module named",
        re.IGNORECASE,
    )
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as error:
        return [f"Could not read {path}: {error}"]
    return [line for line in lines if pattern.search(line)][-max_lines:]


def discover_candidates() -> list[dict]:
    home = Path.home()
    candidates: dict[str, dict] = {}

    def add(path: Path, reason: str) -> None:
        key = str(path.resolve()).lower()
        if key not in candidates:
            candidates[key] = {
                "path": str(path.resolve()),
                "reasons": set(),
                "install_type": "unknown",
                "likely_start_command": "",
                "has_models": False,
                "has_workflows": False,
                "amd_notes": "",
                "recent_logs": [],
            }
        candidates[key]["reasons"].add(reason)

    desktop = home / "AppData" / "Local" / "Programs" / "ComfyUI"
    if (desktop / "ComfyUI.exe").exists():
        add(desktop, "ComfyUI Desktop executable")
        candidates[str(desktop.resolve()).lower()]["install_type"] = "Desktop"
        candidates[str(desktop.resolve()).lower()]["likely_start_command"] = f'"{desktop / "ComfyUI.exe"}"'
        candidates[str(desktop.resolve()).lower()]["amd_notes"] = "Desktop install includes AMD override file, but recent logs show its Python runtime is broken."

    resource = desktop / "resources" / "ComfyUI"
    if (resource / "main.py").exists():
        add(resource, "ComfyUI main.py")
        candidates[str(resource.resolve()).lower()]["install_type"] = "Desktop bundled backend"

    user_dir = home / "Documents" / "ComfyUI"
    if user_dir.exists():
        add(user_dir, "ComfyUI user data")
        item = candidates[str(user_dir.resolve()).lower()]
        item["install_type"] = "Desktop user data"
        item["has_models"] = (user_dir / "models").exists()
        item["has_workflows"] = (user_dir / "user" / "default" / "workflows").exists()
        item["amd_notes"] = "Contains AMD/ROCm venv metadata and local models/workflows."

    for exact in [Path("D:/ComfyUI"), Path("D:/ComfyUI_windows_portable"), Path("D:/AI"), ROOT]:
        if not exact.exists():
            continue
        for path in exact.rglob("*"):
            if len(candidates) > 120:
                break
            lower = str(path).lower()
            if "node_modules" in lower or "\\.git\\" in lower or "\\.venv\\" in lower:
                continue
            if path.is_file() and path.name.lower() == "main.py" and "comfyui" in lower:
                add(path.parent, "ComfyUI main.py")
            if path.is_file() and re.match(r"(run|start).*\.bat$", path.name, re.IGNORECASE) and "comfy" in lower:
                add(path.parent, f"launcher {path.name}")

    logs = recent_logs()
    for item in candidates.values():
        base = item["path"].lower()
        related = [str(log) for log in logs if base in str(log).lower() or "comfyui" in str(log).lower()]
        item["recent_logs"] = related[:6]
        item["reasons"] = sorted(item["reasons"])
    return sorted(candidates.values(), key=lambda item: item["path"].lower())


def python_runtime_checks() -> list[dict]:
    home = Path.home()
    checks = [
        home / "Documents" / "ComfyUI" / ".venv" / "Scripts" / "python.exe",
        home / "AppData" / "Roaming" / "uv" / "python" / "cpython-3.12.11-windows-x86_64-none" / "python.exe",
    ]
    results = []
    for python in checks:
        result = {"python": str(python), "exists": python.exists(), "argparse_ok": False, "detail": ""}
        if python.exists():
            try:
                proc = run_capture([str(python), "-c", "import argparse, sys; print(sys.version); print(sys.executable)"], timeout=10)
                result["argparse_ok"] = proc.returncode == 0
                result["detail"] = (proc.stdout or proc.stderr).strip()
            except Exception as error:
                result["detail"] = str(error)
        results.append(result)
    return results


def workflow_summary() -> dict:
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    story_workflows = sorted(path.name for path in WORKFLOW_DIR.glob("*.json") if not path.name.endswith(".storydriver.json"))
    desktop_workflow_dir = Path.home() / "Documents" / "ComfyUI" / "user" / "default" / "workflows"
    desktop_workflows = []
    if desktop_workflow_dir.exists():
        desktop_workflows = sorted(path.name for path in desktop_workflow_dir.glob("*.json"))
    return {
        "storydriver_folder": str(WORKFLOW_DIR),
        "storydriver_workflows": story_workflows,
        "desktop_workflow_folder": str(desktop_workflow_dir),
        "desktop_workflows": desktop_workflows[:30],
    }


def system_summary() -> dict:
    result = {"gpus": [], "ram": "", "events": []}
    try:
        gpu = run_capture(["wmic", "path", "Win32_VideoController", "get", "Name,AdapterRAM,DriverVersion", "/format:csv"], timeout=10)
        result["gpus"] = [line for line in gpu.stdout.splitlines() if line.strip()][1:8]
    except Exception as error:
        result["gpus"] = [f"GPU query failed: {error}"]
    try:
        ram = run_capture(["wmic", "computersystem", "get", "TotalPhysicalMemory", "/value"], timeout=10)
        result["ram"] = ram.stdout.strip()
    except Exception as error:
        result["ram"] = f"RAM query failed: {error}"
    try:
        events = run_capture(
            [
                "wevtutil",
                "qe",
                "Application",
                "/rd:true",
                "/c:40",
                "/f:text",
                "/q:*[System[(Level=2) and TimeCreated[timediff(@SystemTime) <= 604800000]]]",
            ],
            timeout=20,
        )
        result["events"] = [
            line
            for line in events.stdout.splitlines()
            if re.search(r"comfyui|python|pythonw|appcrash|application error", line, re.IGNORECASE)
        ][:80]
    except Exception as error:
        result["events"] = [f"Event query failed: {error}"]
    return result


def write_report(lines: list[str], path: Path) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def safe_console(text: str = "") -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def append_json(lines: list[str], title: str, value) -> None:
    lines.append("")
    lines.append(title)
    lines.append(json.dumps(value, indent=2, ensure_ascii=False))


def diagnose() -> int:
    load_config()
    base = CONFIG["COMFYUI_BASE_URL"].rstrip("/") or DEFAULTS["COMFYUI_BASE_URL"]
    port = port_from_url(base)
    processes = comfy_processes()
    process_ports = [item["server_port"] for item in processes if item.get("server_port")]
    ports = sorted({8188, 8000, 8001, 8002, 8003, port, *process_ports})
    lines: list[str] = [
        "StoryDriver ComfyUI Diagnostics",
        f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Configuration",
        f"  COMFYUI_BASE_URL={CONFIG['COMFYUI_BASE_URL']}",
        f"  COMFYUI_WORKING_DIR={CONFIG['COMFYUI_WORKING_DIR']}",
        f"  COMFYUI_START_COMMAND={CONFIG['COMFYUI_START_COMMAND']}",
        f"  COMFYUI_WAIT_SECONDS={CONFIG['COMFYUI_WAIT_SECONDS']}",
        f"  COMFYUI_LAUNCH_ARGS={CONFIG['COMFYUI_LAUNCH_ARGS']}",
        f"  COMFYUI_EXPECTED_BACKEND={CONFIG['COMFYUI_EXPECTED_BACKEND']}",
        f"  COMFYUI_PERF_NOTES={CONFIG['COMFYUI_PERF_NOTES']}",
        "",
        "Endpoint checks",
    ]
    for candidate_base in loopback_base_variants(base):
        for path in ("", "/system_stats", "/queue"):
            ok, detail, body = raw_http("GET", f"{candidate_base}{path}", timeout=5.0)
            snippet = body[:800].replace("\r", "").replace("\n", " ")
            lines.append(f"  GET {candidate_base}{path or '/'} -> {detail}; ok={ok}; body={snippet}")

    append_json(lines, "Port listeners", listeners_for_ports(ports))
    append_json(lines, "ComfyUI-like processes", processes)
    append_json(lines, "Python runtime checks", python_runtime_checks())
    append_json(lines, "Discovered install candidates", discover_candidates())
    append_json(lines, "Workflow folders", workflow_summary())
    append_json(lines, "System snapshot", system_summary())

    lines.append("")
    lines.append("Recent ComfyUI logs")
    logs = recent_logs()
    if not logs:
        lines.append("  No ComfyUI logs found.")
    for log in logs[:8]:
        stat = log.stat()
        lines.append("")
        lines.append(f"  {log} size={stat.st_size} modified={datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds')}")
        for line in interesting_log_lines(log, 80):
            lines.append(f"    {line}")

    lines.append("")
    lines.append("Diagnosis notes")
    runtime = python_runtime_checks()
    broken_argparse = any(item["exists"] and not item["argparse_ok"] and "argparse" in item["detail"] for item in runtime)
    missing_venv_python = any("Documents\\ComfyUI\\.venv\\Scripts\\python.exe" in item["python"] and not item["exists"] for item in runtime)
    if missing_venv_python:
        lines.append("  The ComfyUI Desktop venv Python executable is missing from Documents\\ComfyUI\\.venv\\Scripts.")
    if broken_argparse:
        lines.append("  The discovered ComfyUI Python runtime cannot import argparse, so ComfyUI exits before parsing launch arguments.")
    if any("To see the GUI go to: http://127.0.0.1:8000" in line for log in logs[:8] for line in interesting_log_lines(log, 200)):
        lines.append("  Recent logs show ComfyUI Desktop started on 127.0.0.1:8000, which conflicts with StoryDriver backend. Use port 8188 for StoryDriver.")
    if not CONFIG["COMFYUI_START_COMMAND"].strip():
        lines.append("  StoryDriver cannot auto-launch ComfyUI yet because COMFYUI_START_COMMAND is blank.")
    preflight_ok, preflight_detail = configured_backend_preflight()
    if not preflight_ok:
        lines.append(f"  Configured backend-only launch preflight failed: {preflight_detail}")

    write_report(lines, DIAGNOSTICS_LOG)
    safe_console("\n".join(lines[:60]))
    safe_console(f"\nSaved report to {DIAGNOSTICS_LOG}")
    return 0


def launch_diagnostics() -> int:
    load_config()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    base = CONFIG["COMFYUI_BASE_URL"].rstrip("/") or DEFAULTS["COMFYUI_BASE_URL"]
    lines = [
        "StoryDriver ComfyUI Launch Diagnostics",
        f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"Configured URL: {base}",
        f"Configured working directory: {CONFIG['COMFYUI_WORKING_DIR']}",
        f"Configured start command: {CONFIG['COMFYUI_START_COMMAND']}",
        "",
    ]
    active_base, active_detail = reachable_base(base)
    if active_base:
        lines.append(f"ComfyUI is already reachable at {active_base}. No launch attempted. {active_detail}")
        write_report(lines, LAUNCH_LOG)
        print("\n".join(lines))
        return 0

    command = CONFIG["COMFYUI_START_COMMAND"].strip()
    working_dir = Path(CONFIG["COMFYUI_WORKING_DIR"].strip() or ROOT)
    if not command:
        lines.append("No safe ComfyUI launch command found.")
        lines.append("COMFYUI_START_COMMAND is blank, and the discovered Desktop runtime currently fails before startup.")
        lines.append("Candidates are listed below; choose a working manual command before enabling auto-start.")
        lines.append(json.dumps(discover_candidates(), indent=2, ensure_ascii=False))
        write_report(lines, LAUNCH_LOG)
        print("\n".join(lines))
        print(f"\nSaved report to {LAUNCH_LOG}")
        return 0

    if not working_dir.exists():
        lines.append(f"Configured COMFYUI_WORKING_DIR does not exist: {working_dir}")
        write_report(lines, LAUNCH_LOG)
        print("\n".join(lines))
        return 1
    preflight_ok, preflight_detail = configured_backend_preflight()
    if not preflight_ok:
        lines.append(f"Configured backend-only launch preflight failed: {preflight_detail}")
        lines.append("No launch attempted. Repair the ComfyUI Desktop runtime or configure a proven launch command.")
        write_report(lines, LAUNCH_LOG)
        print("\n".join(lines))
        return 0

    redirection = f'1>>"{LAUNCH_LOG}" 2>>&1'
    cmd_line = (
        f'title StoryDriver ComfyUI diagnostics && '
        f'echo. >> "{LAUNCH_LOG}" && '
        f'echo ===== Launch {datetime.now().isoformat(timespec="seconds")} ===== >> "{LAUNCH_LOG}" && '
        f'echo Working directory: {working_dir} >> "{LAUNCH_LOG}" && '
        f'echo Command: {command} >> "{LAUNCH_LOG}" && '
        f'cd /d "{working_dir}" && ({command}) {redirection}'
    )
    lines.append(f"Launching configured command in a diagnostic terminal.")
    lines.append(f"Working directory: {working_dir}")
    lines.append(f"Command: {command}")
    write_report(lines, LAUNCH_LOG)
    subprocess.Popen(["cmd.exe", "/k", cmd_line], cwd=str(working_dir), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    wait_seconds = max(1, int(CONFIG.get("COMFYUI_WAIT_SECONDS") or "60"))
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(1)
        active_base, active_detail = reachable_base(base)
        if active_base:
            print(f"ComfyUI is reachable at {active_base}. {active_detail}")
            print(f"Launch log: {LAUNCH_LOG}")
            return 0
    print(f"ComfyUI did not become reachable at {base} within {wait_seconds} seconds.")
    print(f"Launch log: {LAUNCH_LOG}")
    return 0


def start() -> int:
    load_config()
    base = CONFIG["COMFYUI_BASE_URL"].rstrip("/") or DEFAULTS["COMFYUI_BASE_URL"]
    active_base, active_detail = reachable_base(base)
    if active_base:
        print(f"ComfyUI is already reachable at {active_base}. {active_detail}")
        return 0
    command = CONFIG["COMFYUI_START_COMMAND"].strip()
    if not command:
        print("ComfyUI is not reachable and COMFYUI_START_COMMAND is not configured.")
        print("Start ComfyUI manually or set COMFYUI_WORKING_DIR and COMFYUI_START_COMMAND in D:\\StoryDriver\\.env.")
        print(f"Run D:\\StoryDriver\\scripts\\diagnose_comfyui.bat for candidate paths and recent crash logs.")
        return 0
    return launch_diagnostics()


def stop_safe() -> int:
    load_config()
    processes = [process for process in comfy_processes() if process["safe_to_stop"]]
    if not processes:
        print("No safely identifiable ComfyUI process found.")
        print("No random Python/LM Studio/Kokoro/StoryDriver processes were stopped.")
        return 0
    print("Stopping safely identifiable ComfyUI processes only...")
    for process in sorted(processes, key=lambda item: int(item["pid"] or 0), reverse=True):
        print(f"Stopping PID {process['pid']} {process['name']} ({process['safe_reason']})")
        subprocess.run(["taskkill", "/PID", str(process["pid"]), "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def restart() -> int:
    stop_safe()
    time.sleep(2)
    return start()


def free_memory() -> int:
    load_config()
    base = CONFIG["COMFYUI_BASE_URL"].rstrip("/") or DEFAULTS["COMFYUI_BASE_URL"]
    active_base, active_detail = reachable_base(base)
    if not active_base:
        print(f"ComfyUI is not reachable at {base}; no memory-free request sent.")
        print(active_detail)
        return 0
    ok, detail, queue_body = raw_http("GET", f"{active_base}/queue", timeout=4.0)
    if ok and ("queue_running" in queue_body and "[]" not in queue_body[:200]):
        print("ComfyUI queue may not be empty. Refusing to request memory free while a workflow may be active.")
        print(queue_body[:800])
        return 0
    ok, detail, body = raw_http("POST", f"{active_base}/free", '{"unload_models":true,"free_memory":true}', timeout=10.0)
    print(f"POST {active_base}/free -> {detail}")
    if body:
        print(body[:800])
    return 0 if ok else 1


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "diagnose"
    if action == "diagnose":
        return diagnose()
    if action == "launch-diagnose":
        return launch_diagnostics()
    if action == "start":
        return start()
    if action == "stop":
        return stop_safe()
    if action == "restart":
        return restart()
    if action == "free":
        return free_memory()
    print(f"Unknown action: {action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
