from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
REPORT_PATH = LOG_DIR / "SYSTEM_SPEED_DIAGNOSTICS_REPORT.md"
LATEST_JSON_PATH = LOG_DIR / "SYSTEM_SPEED_DIAGNOSTICS_LATEST.json"
DEFAULT_LM_OPENAI = "http://localhost:1234/v1"
DEFAULT_LM_REST = "http://localhost:1234/api/v1"
DEFAULT_COMFY = "http://localhost:8188"
DEFAULT_BACKEND = "http://localhost:8001"
DEFAULT_KOKORO = "http://localhost:8880"
TARGET_PORTS = {1234, 8188, 8880, 8000, 5173}
LMS_EXE = Path.home() / ".lmstudio" / "bin" / "lms.exe"
AI_PROCESS_HINTS = (
    "lm studio",
    "lmstudio",
    "lms.exe",
    "llama",
    "comfyui",
    "kokoro",
    "storydriver",
    "python",
    "node",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def merged_env() -> dict[str, str]:
    data = dict(os.environ)
    for path in (ROOT / ".env", ROOT / "backend" / ".env"):
        for key, value in load_env_file(path).items():
            data.setdefault(key, value)
    return data


def run_command(args: list[str], *, timeout: float = 20) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "command": args,
        }
    except Exception as error:  # noqa: BLE001
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(error),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "command": args,
        }


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
) -> tuple[Any | None, str | None, float | None]:
    started = time.perf_counter()
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
        duration = round(time.perf_counter() - started, 3)
        if not raw:
            return None, None, duration
        return json.loads(raw.decode("utf-8", errors="replace")), None, duration
    except Exception as error:  # noqa: BLE001
        return None, str(error), round(time.perf_counter() - started, 3)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def port_from_url(url: str, default: int) -> int:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return default
    if parsed.port:
        return int(parsed.port)
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return default


def parse_wmic_processes(stdout: str) -> list[dict[str, Any]]:
    if not stdout.strip():
        return []
    rows = []
    reader = csv.DictReader(StringIO(stdout))
    for row in reader:
        if not row.get("ProcessId"):
            continue
        pid = safe_int(row.get("ProcessId"))
        if not pid:
            continue
        working_set = safe_int(row.get("WorkingSetSize"))
        rows.append(
            {
                "pid": pid,
                "parent_pid": safe_int(row.get("ParentProcessId")),
                "name": row.get("Name") or "",
                "command_line": row.get("CommandLine") or "",
                "executable_path": row.get("ExecutablePath") or "",
                "working_set_mb": round(working_set / 1024 / 1024, 1) if working_set else None,
            }
        )
    return rows


def parse_wmic_list(stdout: str) -> dict[int, dict[str, str]]:
    rows: dict[int, dict[str, str]] = {}
    block: dict[str, str] = {}

    def flush() -> None:
        nonlocal block
        pid = safe_int(block.get("ProcessId"))
        if pid:
            rows[pid] = dict(block)
        block = {}

    for raw in stdout.splitlines():
        line = raw.strip().strip("\ufeff")
        if not line:
            if block.get("ProcessId"):
                flush()
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        block[key.strip()] = value.strip()
        if key.strip() == "ProcessId":
            flush()
    flush()
    return rows


def collect_processes() -> dict[str, Any]:
    # Keep CommandLine out of the CSV query. WMIC does not always quote command
    # lines with commas correctly, which can shift fields and corrupt PID data.
    wmic = run_command(
        [
            "wmic",
            "process",
            "get",
            "ProcessId,ParentProcessId,Name,ExecutablePath,WorkingSetSize",
            "/format:csv",
        ],
        timeout=30,
    )
    processes = parse_wmic_processes(wmic["stdout"]) if wmic["ok"] else []
    command_lines = run_command(
        [
            "wmic",
            "process",
            "get",
            "ProcessId,CommandLine",
            "/format:list",
        ],
        timeout=30,
    )
    targeted_command_lines = run_command(
        [
            "wmic",
            "process",
            "where",
            "name='python.exe' or name='pythonw.exe' or name='cmd.exe' or name='node.exe' or name='node_repl.exe' or name='ComfyUI.exe' or name='LM Studio.exe'",
            "get",
            "ProcessId,CommandLine",
            "/format:list",
        ],
        timeout=30,
    )
    if processes:
        command_by_pid: dict[int, dict[str, str]] = {}
        if command_lines["ok"]:
            command_by_pid.update(parse_wmic_list(command_lines["stdout"]))
        if targeted_command_lines["ok"]:
            for pid, details in parse_wmic_list(targeted_command_lines["stdout"]).items():
                if details.get("CommandLine"):
                    command_by_pid[pid] = details
        for process in processes:
            details = command_by_pid.get(int(process.get("pid") or 0)) or {}
            if details.get("CommandLine"):
                process["command_line"] = details["CommandLine"]
    if not processes:
        tasklist = run_command(["tasklist", "/fo", "csv", "/v"], timeout=20)
        return {
            "processes": [],
            "wmic": wmic,
            "wmic_command_lines": command_lines,
            "wmic_targeted_command_lines": targeted_command_lines,
            "tasklist": tasklist,
        }
    return {
        "processes": processes,
        "wmic": wmic,
        "wmic_command_lines": command_lines,
        "wmic_targeted_command_lines": targeted_command_lines,
    }


def normalize(text: str | None) -> str:
    return (text or "").lower().replace("\\", "/")


def process_matches(process: dict[str, Any], *needles: str) -> bool:
    haystack = normalize(f"{process.get('name')} {process.get('command_line')} {process.get('executable_path')}")
    return any(needle.lower() in haystack for needle in needles)


def is_comfyui_backend_process(process: dict[str, Any]) -> bool:
    name = normalize(str(process.get("name") or ""))
    haystack = normalize(f"{process.get('command_line')} {process.get('executable_path')}")
    if name not in {"python.exe", "pythonw.exe"}:
        return False
    return "main.py" in haystack and "comfyui" in haystack


def is_comfyui_launcher_process(process: dict[str, Any]) -> bool:
    name = normalize(str(process.get("name") or ""))
    haystack = normalize(f"{process.get('command_line')} {process.get('executable_path')}")
    if name in {"cmd.exe", "powershell.exe", "pwsh.exe"}:
        return "launch_comfyui" in haystack or "launch_stable_comfyui" in haystack
    return "launch_stable_comfyui.py" in haystack


def collect_netstat() -> dict[str, Any]:
    result = run_command(["netstat", "-ano"], timeout=20)
    listeners: list[dict[str, Any]] = []
    if result["ok"]:
        for raw in result["stdout"].splitlines():
            line = raw.strip()
            parts = line.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local = parts[1]
            state = parts[3]
            pid_text = parts[4]
            if state.upper() != "LISTENING":
                continue
            try:
                port = int(local.rsplit(":", 1)[-1])
                pid = int(pid_text)
            except ValueError:
                continue
            if port in TARGET_PORTS:
                listeners.append({"port": port, "local": local, "pid": pid})
    return {"raw": result, "listeners": listeners}


def runtime_metadata(payload: Any) -> dict[str, Any]:
    keywords = ("gpu", "offload", "vram", "context", "ctx", "runtime", "device", "flash", "kv", "load", "quant")
    fields: list[dict[str, Any]] = []

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if depth > 7 or len(fields) >= 100:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if any(word in str(key).lower() for word in keywords) and not isinstance(child, (dict, list)):
                    fields.append({"path": next_path, "value": child})
                visit(child, next_path, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:20]):
                visit(child, f"{path}[{index}]", depth + 1)

    visit(payload, "")
    lower = [field["path"].lower() for field in fields]
    return {
        "fields": fields[:60],
        "gpu_offload_visible": any("gpu" in path or "offload" in path for path in lower),
        "context_length_visible": any("context" in path or "ctx" in path for path in lower),
        "flash_attention_visible": any("flash" in path for path in lower),
        "kv_cache_visible": any("kv" in path for path in lower),
        "runtime_info_visible": bool(fields),
    }


def loaded_instances_from_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    instances: list[dict[str, Any]] = []
    raw_top = payload.get("loaded_instances") or payload.get("loadedInstances") or []
    if isinstance(raw_top, dict):
        raw_top = [raw_top]
    if isinstance(raw_top, list):
        instances.extend(item for item in raw_top if isinstance(item, dict))
    models = payload.get("data") or payload.get("models") or payload.get("items") or []
    if isinstance(models, dict):
        models = [models]
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            raw = model.get("loaded_instances") or model.get("loadedInstances") or model.get("instances") or []
            if isinstance(raw, dict):
                raw = [raw]
            if not isinstance(raw, list):
                continue
            for instance in raw:
                if not isinstance(instance, dict):
                    continue
                item = dict(instance)
                item.setdefault("model_key", model.get("id") or model.get("key") or model.get("model_key"))
                item.setdefault("display_name", model.get("name") or model.get("display_name") or item.get("model_key"))
                instances.append(item)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for instance in instances:
        instance_id = str(instance.get("id") or instance.get("instance_id") or instance.get("instanceId") or "").strip()
        key = instance_id or json.dumps(instance, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique.append(instance)
    return unique


def queue_is_busy(queue: Any) -> bool:
    if not isinstance(queue, dict):
        return False
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return bool((isinstance(running, list) and running) or (isinstance(pending, list) and pending))


def collect_gpu_tools() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for tool in ("amd-smi", "rocm-smi", "rocminfo"):
        path = shutil.which(tool)
        if not path:
            tools[tool] = {"found": False}
            continue
        args = [path]
        if tool == "amd-smi":
            args = [path, "metric"]
        tools[tool] = {"found": True, "path": path, "result": run_command(args, timeout=20)}
    return tools


def collect_lms_cli() -> dict[str, Any]:
    if not LMS_EXE.exists():
        return {"found": False, "path": str(LMS_EXE)}
    return {
        "found": True,
        "path": str(LMS_EXE),
        "ps": run_command([str(LMS_EXE), "ps"], timeout=20),
        "runtime_ls": run_command([str(LMS_EXE), "runtime", "ls"], timeout=20),
        "load_help": run_command([str(LMS_EXE), "load", "--help"], timeout=20),
    }


def classify_processes(
    processes: list[dict[str, Any]],
    listeners: list[dict[str, Any]],
    *,
    configured_comfy_port: int = 8188,
) -> dict[str, Any]:
    listener_by_port = {item["port"]: item for item in listeners}
    active_8188_pid = listener_by_port.get(8188, {}).get("pid")
    active_comfyui_pid = listener_by_port.get(configured_comfy_port, {}).get("pid") or active_8188_pid
    active_comfyui_port = configured_comfy_port if listener_by_port.get(configured_comfy_port, {}).get("pid") else 8188 if active_8188_pid else None
    by_pid = {int(p.get("pid") or 0): p for p in processes}

    if not active_comfyui_pid:
        for process in processes:
            if not is_comfyui_backend_process(process):
                continue
            pid = safe_int(process.get("pid"))
            for listener in listeners:
                if listener.get("pid") == pid:
                    active_comfyui_pid = pid
                    active_comfyui_port = listener.get("port")
                    break
            if active_comfyui_pid:
                break

    def ancestors(pid: int | None) -> set[int]:
        result: set[int] = set()
        current = pid
        for _ in range(20):
            if not current:
                break
            parent = by_pid.get(current, {}).get("parent_pid")
            parent_id = safe_int(parent)
            if not parent_id or parent_id in result:
                break
            result.add(parent_id)
            current = parent_id
        return result

    active_ancestors = ancestors(active_comfyui_pid)
    comfy_processes = [p for p in processes if process_matches(p, "comfyui", "ComfyUI")]
    lm_processes = [p for p in processes if process_matches(p, "lm studio", "lmstudio", "lms.exe")]
    storydriver_processes = [p for p in processes if process_matches(p, str(ROOT).lower(), "storydriver")]
    kokoro_processes = [p for p in processes if process_matches(p, "kokoro")]
    comfy_backend_processes = [p for p in comfy_processes if is_comfyui_backend_process(p)]
    comfy_launcher_processes = [p for p in comfy_processes if is_comfyui_launcher_process(p)]
    ignored_comfy_processes: list[dict[str, Any]] = []
    duplicate_comfy_candidates: list[dict[str, Any]] = []
    for process in comfy_processes:
        pid = safe_int(process.get("pid"))
        haystack = normalize(f"{process.get('name')} {process.get('command_line')} {process.get('executable_path')}")
        if not pid or pid == active_comfyui_pid or pid in active_ancestors:
            continue
        # Only a real Python ComfyUI main.py process can be a safe duplicate
        # backend candidate. Hidden launchers, AMD installer probes, and helper
        # scripts may mention ComfyUI/main.py without owning GPU backend state.
        if is_comfyui_backend_process(process):
            duplicate_comfy_candidates.append(process)
        elif "comfyui" in haystack:
            ignored = dict(process)
            ignored["duplicate_exclusion_reason"] = "not a Python ComfyUI backend process"
            ignored_comfy_processes.append(ignored)
    ai_processes = [
        p
        for p in processes
        if any(hint in normalize(f"{p.get('name')} {p.get('command_line')}") for hint in AI_PROCESS_HINTS)
    ]
    top_ai = sorted(ai_processes, key=lambda p: p.get("working_set_mb") or 0, reverse=True)[:20]
    return {
        "lm_studio": lm_processes,
        "comfyui": comfy_processes,
        "storydriver": storydriver_processes,
        "kokoro": kokoro_processes,
        "top_ai_memory": top_ai,
        "configured_comfyui_port": configured_comfy_port,
        "active_comfyui_pid": active_comfyui_pid,
        "active_comfyui_port": active_comfyui_port,
        "active_comfyui_ancestor_pids": sorted(active_ancestors),
        "active_comfyui_8188_pid": active_8188_pid,
        "active_comfyui_8188_ancestor_pids": sorted(ancestors(active_8188_pid)),
        "comfyui_backend_processes": comfy_backend_processes,
        "comfyui_launcher_processes": comfy_launcher_processes,
        "ignored_comfyui_related_processes": ignored_comfy_processes,
        "duplicate_comfyui_candidates": duplicate_comfy_candidates,
        "multiple_comfyui_processes": len(comfy_backend_processes) > 1,
    }


def collect_http(env: dict[str, str], *, free_impact: bool = False) -> dict[str, Any]:
    openai_url = env.get("LM_STUDIO_BASE_URL", DEFAULT_LM_OPENAI).rstrip("/")
    rest_url = env.get("LM_STUDIO_REST_BASE_URL", DEFAULT_LM_REST).rstrip("/")
    comfy_url = env.get("COMFYUI_BASE_URL", DEFAULT_COMFY).rstrip("/")
    backend_url = env.get("STORYDRIVER_BACKEND_URL", DEFAULT_BACKEND).rstrip("/")
    kokoro_url = env.get("KOKORO_BASE_URL", DEFAULT_KOKORO).rstrip("/")
    data: dict[str, Any] = {
        "urls": {
            "lm_openai": openai_url,
            "lm_rest": rest_url,
            "comfyui": comfy_url,
            "backend": backend_url,
            "kokoro": kokoro_url,
        }
    }
    for key, url in {
        "lm_openai_models": f"{openai_url}/models",
        "lm_rest_models": f"{rest_url}/models",
        "comfyui_system_stats": f"{comfy_url}/system_stats",
        "comfyui_queue": f"{comfy_url}/queue",
        "backend_health": f"{backend_url}/health",
        "backend_diagnostics": f"{backend_url}/diagnostics",
        "backend_resource_status": f"{backend_url}/resource-status",
        "backend_image_job_status": f"{backend_url}/images/job-status",
        "backend_tts_status": f"{backend_url}/tts/status",
        "kokoro_health": f"{kokoro_url}/health",
    }.items():
        payload, error, seconds = request_json(url, timeout=12)
        data[key] = {"ok": error is None, "error": error, "duration_seconds": seconds, "payload": payload}

    rest_payload = data.get("lm_rest_models", {}).get("payload")
    data["lm_runtime_metadata"] = runtime_metadata(rest_payload)
    data["lm_loaded_instances"] = loaded_instances_from_payload(rest_payload)

    if free_impact:
        queue = data.get("comfyui_queue", {}).get("payload")
        before_stats = data.get("comfyui_system_stats", {}).get("payload")
        free_result: dict[str, Any] = {"attempted": False}
        if queue_is_busy(queue):
            free_result.update({"skipped": True, "skip_reason": "comfyui_queue_busy"})
        else:
            payload, error, seconds = request_json(
                f"{comfy_url}/free",
                method="POST",
                payload={"unload_models": True, "free_memory": True},
                timeout=30,
            )
            time.sleep(8)
            after_stats, after_error, after_seconds = request_json(f"{comfy_url}/system_stats", timeout=12)
            after_queue, after_queue_error, after_queue_seconds = request_json(f"{comfy_url}/queue", timeout=12)
            free_result.update(
                {
                    "attempted": True,
                    "ok": error is None,
                    "error": error,
                    "duration_seconds": seconds,
                    "payload": payload,
                    "stats_before": before_stats,
                    "stats_after": after_stats,
                    "stats_after_error": after_error,
                    "stats_after_duration_seconds": after_seconds,
                    "queue_after": after_queue,
                    "queue_after_error": after_queue_error,
                    "queue_after_duration_seconds": after_queue_seconds,
                }
            )
        data["comfyui_free_impact"] = free_result
    return data


def summarize_process(process: dict[str, Any]) -> str:
    command = str(process.get("command_line") or process.get("executable_path") or "")[:520]
    parent = process.get("parent_pid")
    reason = process.get("duplicate_exclusion_reason")
    suffix = f" ({reason})" if reason else ""
    return f"- PID {process.get('pid')} parent {parent} `{process.get('name')}` {process.get('working_set_mb')} MB{suffix} :: {command}"


def write_report(report: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    classified = report["classified_processes"]
    http = report["http"]
    lines = [
        "# System Speed Diagnostics Report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Loaded LM Studio instances: {len(http.get('lm_loaded_instances') or [])}",
        f"- ComfyUI processes found: {len(classified.get('comfyui') or [])}",
        f"- Active ComfyUI PID: {classified.get('active_comfyui_pid') or 'not detected'} on port {classified.get('active_comfyui_port') or 'unknown'}",
        f"- Active ComfyUI 8188 PID: {classified.get('active_comfyui_8188_pid') or 'not detected'}",
        f"- Duplicate ComfyUI candidates: {len(classified.get('duplicate_comfyui_candidates') or [])}",
        f"- LM runtime/offload fields visible: {'yes' if http.get('lm_runtime_metadata', {}).get('runtime_info_visible') else 'no'}",
        f"- GPU/offload visible through LM Studio REST: {'yes' if http.get('lm_runtime_metadata', {}).get('gpu_offload_visible') else 'no'}",
        f"- Context length visible through LM Studio REST: {'yes' if http.get('lm_runtime_metadata', {}).get('context_length_visible') else 'no'}",
        "",
        "## Ports",
        "",
    ]
    for listener in report["netstat"].get("listeners", []):
        lines.append(f"- {listener['local']} PID {listener['pid']}")
    lines.extend(["", "## LM Studio", ""])
    for process in classified.get("lm_studio") or []:
        lines.append(summarize_process(process))
    lines.append("")
    lines.append("Loaded instances:")
    for instance in http.get("lm_loaded_instances") or []:
        lines.append(f"- `{instance.get('display_name') or instance.get('model_key') or instance.get('id')}` id `{instance.get('id') or instance.get('instance_id') or ''}`")
    if not http.get("lm_loaded_instances"):
        lines.append("- None reported by REST API.")
    lines.append("")
    lines.append("Runtime/offload fields visible from REST:")
    fields = http.get("lm_runtime_metadata", {}).get("fields") or []
    if fields:
        for field in fields[:30]:
            lines.append(f"- `{field['path']}` = `{field['value']}`")
    else:
        lines.append("- No runtime/offload fields exposed by the local REST response.")
    lms_cli = report.get("lms_cli") or {}
    lines.extend(["", "LM Studio CLI:", ""])
    if not lms_cli.get("found"):
        lines.append(f"- `lms.exe` not found at `{lms_cli.get('path')}`.")
    else:
        lines.append(f"- Path: `{lms_cli.get('path')}`")
        ps_output = ((lms_cli.get("ps") or {}).get("stdout") or "").splitlines()
        if ps_output:
            lines.append("- `lms ps`:")
            lines.extend(f"  {line}" for line in ps_output[:12])
        runtime_output = ((lms_cli.get("runtime_ls") or {}).get("stdout") or "").splitlines()
        if runtime_output:
            lines.append("- `lms runtime ls`:")
            lines.extend(f"  {line}" for line in runtime_output[:12])
    lines.extend(["", "## ComfyUI", ""])
    lines.append("ComfyUI-related processes:")
    for process in classified.get("comfyui") or []:
        lines.append(summarize_process(process))
    lines.append("")
    lines.append("ComfyUI backend processes:")
    for process in classified.get("comfyui_backend_processes") or []:
        lines.append(summarize_process(process))
    if not classified.get("comfyui_backend_processes"):
        lines.append("- None detected.")
    lines.append("")
    lines.append("ComfyUI launcher/helper processes:")
    for process in classified.get("comfyui_launcher_processes") or []:
        lines.append(summarize_process(process))
    if not classified.get("comfyui_launcher_processes"):
        lines.append("- None detected.")
    lines.append("")
    lines.append("Duplicate/stale candidates:")
    for process in classified.get("duplicate_comfyui_candidates") or []:
        lines.append(summarize_process(process))
    if not classified.get("duplicate_comfyui_candidates"):
        lines.append("- None clearly identified.")
    ignored_related = classified.get("ignored_comfyui_related_processes") or []
    if ignored_related:
        lines.append("")
        lines.append("Ignored ComfyUI-related non-backend processes:")
        for process in ignored_related[:12]:
            lines.append(summarize_process(process))
    queue = http.get("comfyui_queue", {})
    lines.append("")
    lines.append(f"Queue reachable: {'yes' if queue.get('ok') else 'no'}")
    if queue.get("payload") is not None:
        payload = queue["payload"]
        lines.append(f"- queue_running: {len(payload.get('queue_running') or []) if isinstance(payload, dict) else 'unknown'}")
        lines.append(f"- queue_pending: {len(payload.get('queue_pending') or []) if isinstance(payload, dict) else 'unknown'}")
    if "comfyui_free_impact" in http:
        free = http["comfyui_free_impact"]
        lines.extend(
            [
                "",
                "## ComfyUI /free Impact",
                "",
                f"- Attempted: {free.get('attempted')}",
                f"- OK: {free.get('ok')}",
                f"- Error: {free.get('error') or free.get('skip_reason') or 'none'}",
                f"- Duration: {free.get('duration_seconds')}",
                f"- ComfyUI stayed reachable after /free: {'yes' if not free.get('stats_after_error') else 'no'}",
            ]
        )
    lines.extend(["", "## Top Local AI Processes By Working Set", ""])
    for process in classified.get("top_ai_memory") or []:
        lines.append(summarize_process(process))
    lines.extend(["", "## GPU Telemetry Tools", ""])
    for tool, info in report["gpu_tools"].items():
        lines.append(f"- {tool}: {'found' if info.get('found') else 'not found'}")
        result = info.get("result")
        if result:
            stdout = (result.get("stdout") or "").splitlines()[:12]
            if stdout:
                lines.extend(f"  {line}" for line in stdout)
            if result.get("stderr"):
                lines.append(f"  stderr: {result['stderr'][:400]}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This script does not assume NVIDIA/CUDA.",
            "- If VRAM telemetry tools are unavailable or do not report memory, the report leaves VRAM unknown rather than guessing.",
            "- ComfyUI is not closed by this diagnostic. `/free` only runs when `--free-impact` is passed and the queue is idle.",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def collect_report(*, free_impact: bool = False) -> dict[str, Any]:
    env = merged_env()
    process_data = collect_processes()
    netstat = collect_netstat()
    processes = process_data.get("processes") or []
    configured_comfy_port = port_from_url(env.get("COMFYUI_BASE_URL", DEFAULT_COMFY), 8188)
    classified = classify_processes(processes, netstat.get("listeners") or [], configured_comfy_port=configured_comfy_port)
    report = {
        "generated_at": utc_now(),
        "free_impact_requested": free_impact,
        "process_collection": process_data,
        "netstat": netstat,
        "classified_processes": classified,
        "gpu_tools": collect_gpu_tools(),
        "lms_cli": collect_lms_cli(),
        "http": collect_http(env, free_impact=free_impact),
    }
    write_report(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect local StoryDriver / LM Studio / ComfyUI speed diagnostics.")
    parser.add_argument("--free-impact", action="store_true", help="If ComfyUI is idle, call /free once and capture after-stats.")
    args = parser.parse_args()
    report = collect_report(free_impact=args.free_impact)
    summary = {
        "report": str(REPORT_PATH),
        "latest_json": str(LATEST_JSON_PATH),
        "lm_loaded_instances": len(report["http"].get("lm_loaded_instances") or []),
        "comfyui_processes": len(report["classified_processes"].get("comfyui") or []),
        "duplicate_comfyui_candidates": len(report["classified_processes"].get("duplicate_comfyui_candidates") or []),
        "free_impact_attempted": report["http"].get("comfyui_free_impact", {}).get("attempted"),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
