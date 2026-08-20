from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(r"D:\StoryDriver")
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "comfyui_desktop_repair_report.md"
BACKUP_DIR = LOG_DIR / "comfyui_backup_before_repair"
DESKTOP_INSTALL = Path(r"C:\Users\wngar\AppData\Local\Programs\ComfyUI")
DESKTOP_EXE = DESKTOP_INSTALL / "ComfyUI.exe"
DESKTOP_ICU = DESKTOP_INSTALL / "icudtl.dat"
DESKTOP_D3D = DESKTOP_INSTALL / "d3dcompiler_47.dll"
DESKTOP_RESOURCES = DESKTOP_INSTALL / "resources"
COMFY_BACKEND = DESKTOP_RESOURCES / "ComfyUI"
USER_DATA = Path(r"C:\Users\wngar\Documents\ComfyUI")
ROAMING = Path(r"C:\Users\wngar\AppData\Roaming\ComfyUI")
VENV = USER_DATA / ".venv"
VENV_PYTHON = VENV / "Scripts" / "python.exe"
UV = DESKTOP_RESOURCES / "uv" / "win" / "uv.exe"
BASE_PYTHON = Path(r"C:\Users\wngar\AppData\Roaming\uv\python\cpython-3.12.11-windows-x86_64-none\python.exe")
BASE_LIB = BASE_PYTHON.parent / "Lib"
MAX_BACKUP_BYTES = 25 * 1024 * 1024


def run(args: list[str] | str, cwd: Path | None = None, timeout: int = 30) -> dict:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
            shell=isinstance(args, str),
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except Exception as error:
        return {"returncode": None, "stdout": "", "stderr": str(error)}


def file_meta(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "is_dir": path.is_dir(),
    }


def read_text(path: Path, limit: int = 10000) -> str:
    if not path.exists() or path.is_dir():
        return ""
    data = path.read_bytes()[:limit]
    return data.decode("utf-8", errors="replace")


def copy_file(src: Path, dst: Path, copied: list[dict]) -> None:
    if not src.exists() or not src.is_file():
        return
    size = src.stat().st_size
    if size > MAX_BACKUP_BYTES:
        copied.append({"source": str(src), "skipped": "too_large", "size": size})
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append({"source": str(src), "backup": str(dst), "size": size})


def backup_lightweight_files() -> list[dict]:
    copied: list[dict] = []
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    roots_and_patterns = [
        (ROAMING, ["*.json", "*.yaml", "*.yml", "*.txt", "*.log", "Preferences", "Local State", ".updaterId"]),
        (ROAMING / "logs", ["*.log"]),
        (USER_DATA / "user", ["*.json", "*.yaml", "*.yml", "*.txt", "*.log", "*.db"]),
        (USER_DATA / "user" / "default" / "workflows", ["*.json"]),
        (USER_DATA / "user" / "default", ["*.json", "*.yaml", "*.yml", "*.txt", "*.log", "*.db"]),
        (USER_DATA / "custom_nodes", ["*.json", "*.yaml", "*.yml", "*.txt", "*.ini", "*.toml"]),
        (VENV, ["pyvenv.cfg", ".lock", ".gitignore"]),
    ]
    for root, patterns in roots_and_patterns:
        if not root.exists():
            continue
        for pattern in patterns:
            for src in root.rglob(pattern) if root.name == "custom_nodes" else root.glob(pattern):
                if not src.is_file():
                    continue
                try:
                    rel = src.relative_to(src.anchor)
                except ValueError:
                    rel = Path(src.name)
                copy_file(src, BACKUP_DIR / rel, copied)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "note": "Lightweight ComfyUI config/workflow/log backup before StoryDriver repair diagnostics. Model files were intentionally not copied.",
        "copied": copied,
    }
    (BACKUP_DIR / "backup_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return copied


def find_json_bom_files() -> list[dict]:
    candidates: list[dict] = []
    for root in [ROAMING, USER_DATA / "user"]:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            if not path.is_file() or path.stat().st_size > MAX_BACKUP_BYTES:
                continue
            data = path.read_bytes()
            if not data.startswith(b"\xef\xbb\xbf"):
                continue
            result = {"path": str(path), "size": len(data), "starts_with_utf8_bom": True}
            try:
                json.loads(data.decode("utf-8-sig"))
                result["valid_after_bom_strip"] = True
            except Exception as error:
                result["valid_after_bom_strip"] = False
                result["error"] = str(error)
            candidates.append(result)
    return candidates


def remove_json_bom(path: Path) -> bool:
    data = path.read_bytes()
    if not data.startswith(b"\xef\xbb\xbf"):
        return False
    json.loads(data.decode("utf-8-sig"))
    backup = BACKUP_DIR / path.relative_to(path.anchor)
    copy_file(path, backup, [])
    path.write_bytes(data[3:])
    return True


def set_desktop_port_8188() -> bool:
    path = USER_DATA / "user" / "default" / "comfy.settings.json"
    if not path.exists():
        return False
    data = path.read_bytes()
    text = data.decode("utf-8-sig")
    settings = json.loads(text)
    launch_args = settings.setdefault("Comfy.Server.LaunchArgs", {})
    server_values = settings.setdefault("Comfy.Server.ServerConfigValues", {})
    changed = False
    if launch_args.get("port") != "8188":
        launch_args["port"] = "8188"
        changed = True
    if server_values.get("port") != 8188:
        server_values["port"] = 8188
        changed = True
    if changed:
        copy_file(path, BACKUP_DIR / path.relative_to(path.anchor), [])
        path.write_text(json.dumps(settings, indent=4), encoding="utf-8")
    return changed


def desktop_settings() -> dict:
    path = USER_DATA / "user" / "default" / "comfy.settings.json"
    if not path.exists():
        return {"path": str(path), "exists": False}
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as error:
        return {"path": str(path), "exists": True, "error": str(error)}
    return {
        "path": str(path),
        "exists": True,
        "launch_args": settings.get("Comfy.Server.LaunchArgs", {}),
        "server_config_values": settings.get("Comfy.Server.ServerConfigValues", {}),
    }


def inspect() -> dict:
    py_tests = {
        "venv_python_version": run([str(VENV_PYTHON), "--version"]) if VENV_PYTHON.exists() else {"missing": True},
        "venv_argparse": run([str(VENV_PYTHON), "-c", "import sys; print(sys.executable); import argparse; print('argparse ok')"]) if VENV_PYTHON.exists() else {"missing": True},
        "venv_ssl": run([str(VENV_PYTHON), "-c", "import ssl; print('ssl ok')"]) if VENV_PYTHON.exists() else {"missing": True},
        "venv_site": run([str(VENV_PYTHON), "-c", "import site; print(site.getsitepackages())"]) if VENV_PYTHON.exists() else {"missing": True},
        "base_python_version": run([str(BASE_PYTHON), "--version"]) if BASE_PYTHON.exists() else {"missing": True},
        "base_argparse": run([str(BASE_PYTHON), "-c", "import sys; print(sys.executable); import argparse; print('argparse ok')"]) if BASE_PYTHON.exists() else {"missing": True},
        "base_ssl": run([str(BASE_PYTHON), "-c", "import ssl; print('ssl ok')"]) if BASE_PYTHON.exists() else {"missing": True},
        "base_site": run([str(BASE_PYTHON), "-c", "import site; print(site.getsitepackages())"]) if BASE_PYTHON.exists() else {"missing": True},
    }
    if VENV_PYTHON.exists():
        py_tests["torch"] = run([str(VENV_PYTHON), "-c", "import torch; print(torch.__version__); print(getattr(torch.version, 'hip', None)); print(torch.cuda.is_available())"], timeout=20)
        py_tests["directml"] = run([str(VENV_PYTHON), "-c", "import torch_directml; print('torch_directml ok')"], timeout=20)
    else:
        py_tests["torch"] = {"missing": True}
        py_tests["directml"] = {"missing": True}

    logs = []
    log_dir = ROAMING / "logs"
    if log_dir.exists():
        for path in sorted(log_dir.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)[:8]:
            logs.append({**file_meta(path), "tail": read_text(path, 12000)[-4000:]})

    workflows = []
    workflow_dir = USER_DATA / "user" / "default" / "workflows"
    if workflow_dir.exists():
        workflows = [path.name for path in sorted(workflow_dir.glob("*.json"))]

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "install_type": "ComfyUI Desktop",
        "desktop_install": file_meta(DESKTOP_INSTALL),
        "desktop_exe": file_meta(DESKTOP_EXE),
        "desktop_icudtl": file_meta(DESKTOP_ICU),
        "desktop_d3dcompiler": file_meta(DESKTOP_D3D),
        "desktop_backend": file_meta(COMFY_BACKEND / "main.py"),
        "user_data": file_meta(USER_DATA),
        "roaming_data": file_meta(ROAMING),
        "venv": file_meta(VENV),
        "venv_python": file_meta(VENV_PYTHON),
        "base_python": file_meta(BASE_PYTHON),
        "base_argparse_py": file_meta(BASE_LIB / "argparse.py"),
        "uv": file_meta(UV),
        "uv_version": run([str(UV), "--version"]) if UV.exists() else {"missing": True},
        "pyvenv_cfg": read_text(VENV / "pyvenv.cfg", 2000),
        "roaming_config": read_text(ROAMING / "config.json", 4000),
        "window_config": read_text(ROAMING / "window.json", 2000),
        "extra_models_config": read_text(ROAMING / "extra_models_config.yaml", 4000),
        "desktop_settings": desktop_settings(),
        "json_bom_files": find_json_bom_files(),
        "python_tests": py_tests,
        "environment": {key: os.environ.get(key) for key in ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"]},
        "ports": run("netstat -ano -p tcp | findstr /C:\":8000\" /C:\":8188\"", timeout=10),
        "comfyui_system_stats": run(["curl.exe", "--silent", "--show-error", "--max-time", "5", "http://localhost:8188/system_stats"], timeout=8),
        "comfyui_queue": run(["curl.exe", "--silent", "--show-error", "--max-time", "5", "http://localhost:8188/queue"], timeout=8),
        "processes": run('wmic process where "name=\'ComfyUI.exe\' or name=\'python.exe\' or name=\'pythonw.exe\'" get ProcessId,Name,CommandLine /format:list', timeout=10),
        "desktop_debug_log": read_text(DESKTOP_INSTALL / "debug.log", 4000),
        "logs": logs,
        "workflows": workflows,
        "models_subdirs": [path.name for path in sorted((USER_DATA / "models").iterdir())] if (USER_DATA / "models").exists() else [],
    }


def write_report(data: dict, copied: list[dict] | None = None, repairs: list[str] | None = None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    repairs = repairs or []
    copied = copied or []
    latest_log_errors = []
    for log in data.get("logs", [])[:3]:
        tail = log.get("tail", "")
        interesting = "\n".join(
            line for line in tail.splitlines()
            if "error" in line.lower() or "traceback" in line.lower() or "running command" in line.lower() or "port" in line.lower()
        )
        latest_log_errors.append((log.get("path"), interesting[-1500:]))

    likely_reason = []
    if not data["venv_python"].get("exists"):
        likely_reason.append("The ComfyUI venv is incomplete: .venv\\Scripts\\python.exe is missing.")
    if not data["desktop_icudtl"].get("exists"):
        likely_reason.append("ComfyUI Desktop is missing icudtl.dat, so Electron exits before normal app logs are written.")
    elif "Invalid file descriptor to ICU data" in data.get("desktop_debug_log", ""):
        likely_reason.append("Earlier Desktop launches failed in Electron with `Invalid file descriptor to ICU data received`; this matched a missing icudtl.dat runtime file before repair.")
    if not data["desktop_d3dcompiler"].get("exists"):
        likely_reason.append("ComfyUI Desktop is missing d3dcompiler_47.dll, another root Electron runtime file from the installer.")
    if not data["base_argparse_py"].get("exists"):
        likely_reason.append("The uv-managed base Python is missing Lib\\argparse.py, so ComfyUI crashes on startup with ModuleNotFoundError.")
    if data.get("json_bom_files"):
        likely_reason.append("At least one ComfyUI JSON settings file starts with a UTF-8 BOM; Desktop logs report that as invalid JSON.")
    if "--port 8000" in "\n".join(str(item.get("tail", "")) for item in data.get("logs", [])):
        likely_reason.append("Older Desktop logs show a backend launch on --port 8000, which conflicts with StoryDriver's backend. StoryDriver needs ComfyUI on 8188.")
    settings = data.get("desktop_settings", {})
    launch_port = settings.get("launch_args", {}).get("port")
    server_port = settings.get("server_config_values", {}).get("port")
    comfy_reachable = data.get("comfyui_system_stats", {}).get("returncode") == 0

    lines = [
        "# ComfyUI Desktop Repair Report",
        "",
        f"Generated: {data['timestamp']}",
        "",
        "## Summary",
        "",
        f"- Detected install type: {data['install_type']}",
        f"- Executable path: `{DESKTOP_EXE}`",
        f"- User data path: `{USER_DATA}`",
        f"- Expected venv Python: `{VENV_PYTHON}`",
        f"- `.venv` exists: {data['venv'].get('exists')}",
        f"- `.venv\\Scripts\\python.exe` exists: {data['venv_python'].get('exists')}",
        f"- `icudtl.dat` exists: {data['desktop_icudtl'].get('exists')}",
        f"- `d3dcompiler_47.dll` exists: {data['desktop_d3dcompiler'].get('exists')}",
        f"- uv-managed base Python: `{BASE_PYTHON}`",
        f"- Base Python `Lib\\argparse.py` exists: {data['base_argparse_py'].get('exists')}",
        f"- ComfyUI settings launch port: {launch_port}",
        f"- ComfyUI settings server port: {server_port}",
        f"- `http://localhost:8188/system_stats` reachable: {comfy_reachable}",
        f"- Workflow files found: {len(data.get('workflows', []))}",
        f"- Model subdirectories found: {', '.join(data.get('models_subdirs', [])) or 'none'}",
        "",
        "## Likely Reason Desktop Click Does Nothing",
        "",
        *[f"- {item}" for item in likely_reason],
        "",
        "## Current Reachability",
        "",
        "### /system_stats",
        "```text",
        data.get("comfyui_system_stats", {}).get("stdout") or data.get("comfyui_system_stats", {}).get("stderr") or "Not reachable.",
        "```",
        "",
        "### /queue",
        "```text",
        data.get("comfyui_queue", {}).get("stdout") or data.get("comfyui_queue", {}).get("stderr") or "Not reachable.",
        "```",
        "",
        "## Python Checks",
        "",
        "```json",
        json.dumps(data.get("python_tests", {}), indent=2),
        "```",
        "",
        "## Port Checks",
        "",
        "```text",
        data.get("ports", {}).get("stdout") or data.get("ports", {}).get("stderr") or "No listeners on 8000/8188 reported.",
        "```",
        "",
        "## ComfyUI Config",
        "",
        "### Roaming config.json",
        "```json",
        data.get("roaming_config", "").strip(),
        "```",
        "",
        "### window.json",
        "```json",
        data.get("window_config", "").strip(),
        "```",
        "",
        "### extra_models_config.yaml",
        "```yaml",
        data.get("extra_models_config", "").strip(),
        "```",
        "",
        "## JSON Files With UTF-8 BOM",
        "",
        "```json",
        json.dumps(data.get("json_bom_files", []), indent=2),
        "```",
        "",
        "## Latest Log Evidence",
        "",
    ]
    for path, excerpt in latest_log_errors:
        lines.extend([f"### `{path}`", "", "```text", excerpt or "(no error excerpt)", "```", ""])
    lines.extend([
        "## Workflows Found",
        "",
        *[f"- {name}" for name in data.get("workflows", [])],
        "",
        "## Backup",
        "",
        f"- Backup path: `{BACKUP_DIR}`",
        f"- Files copied/skipped: {len(copied)}",
        "- Model files were intentionally not copied.",
        "",
        "## Repairs Attempted",
        "",
        *([f"- {item}" for item in repairs] if repairs else ["- None yet."]),
        "",
        "## Current Repair Notes",
        "",
        "- If this report is generated after the repair pass, `icudtl.dat` and `d3dcompiler_47.dll` should be present in the Desktop install root.",
        "- Those files were restored from the local installer `C:\\Users\\wngar\\OneDrive\\Desktop\\ComfyUI Setup 0.9.2 - x64.exe` when they were missing.",
        "- The ComfyUI venv was repaired against Desktop's AMD lockfile at `C:\\Users\\wngar\\AppData\\Local\\Programs\\ComfyUI\\resources\\requirements\\windows_amd.compiled`.",
        "- StoryDriver expects ComfyUI on `http://localhost:8188`; StoryDriver backend remains on `http://localhost:8001`.",
        "",
    ])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    (LOG_DIR / "comfyui_desktop_repair_snapshot.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--strip-json-bom", action="store_true")
    parser.add_argument("--set-port-8188", action="store_true")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    copied = backup_lightweight_files() if args.backup else []
    repairs: list[str] = []
    if args.strip_json_bom:
        for item in find_json_bom_files():
            if not item.get("valid_after_bom_strip"):
                continue
            path = Path(item["path"])
            if remove_json_bom(path):
                repairs.append(f"Removed UTF-8 BOM from {path}")
    if args.set_port_8188:
        if set_desktop_port_8188():
            repairs.append(r"Set Comfy.Server.LaunchArgs port and ServerConfigValues port to 8188 in user\default\comfy.settings.json")
        else:
            repairs.append("ComfyUI settings port was already 8188 or settings file was missing.")
    data = inspect()
    write_report(data, copied=copied, repairs=repairs)
    print(f"Report: {REPORT_PATH}")
    if args.backup:
        print(f"Backup: {BACKUP_DIR}")
    for item in repairs:
        print(f"Repair: {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
