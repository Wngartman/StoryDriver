from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
WORKFLOW_DIR = ROOT / "backend" / "data" / "comfy_workflows"
COMFY_USER_DIR = Path(os.environ.get("USERPROFILE", r"C:\Users\wngar")) / "Documents" / "ComfyUI"
COMFY_VENV_PYTHON = COMFY_USER_DIR / ".venv" / "Scripts" / "python.exe"
COMFY_WORKFLOW_DIR = COMFY_USER_DIR / "user" / "default" / "workflows"
REPORT_PATH = LOG_DIR / "COMFYUI_DEPENDENCY_REPORT.md"


MISSING_DEPS: dict[str, dict[str, Any]] = {
    "matplotlib": {
        "import": "matplotlib",
        "package": "matplotlib",
        "nodes": ["comfyui-detail-daemon"],
    },
    "cv2": {
        "import": "cv2",
        "package": "opencv-python",
        "nodes": ["comfyui-easy-use", "comfyui-impact-pack", "comfyui-impact-subpack"],
    },
    "piexif": {
        "import": "piexif",
        "package": "piexif",
        "nodes": ["comfyui-image-saver"],
    },
    "platformdirs": {
        "import": "platformdirs",
        "package": "platformdirs",
        "nodes": ["comfyui-lora-manager"],
    },
    "numba": {
        "import": "numba",
        "package": "numba",
        "nodes": ["was-ns"],
    },
}


NODE_DEP_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("comfyui-detail-daemon", "matplotlib", ("detail daemon", "DetailDaemon")),
    ("comfyui-easy-use", "cv2", ("easy ", "easy", "easyuse")),
    ("comfyui-impact-pack", "cv2", ("impact", "FaceDetailer", "UltralyticsDetectorProvider")),
    ("comfyui-impact-subpack", "cv2", ("impact", "FaceDetailer", "UltralyticsDetectorProvider")),
    ("comfyui-image-saver", "piexif", ("Image Saver", "ImageSaver")),
    ("comfyui-lora-manager", "platformdirs", ("LoraManager", "Lora Stacker")),
    ("was-ns", "numba", ("WAS", "was-ns", "was_suite")),
]


def load_env_value(key: str, default: str) -> str:
    for env_path in (ROOT / ".env", ROOT / "backend" / ".env", ROOT / "scripts" / "storydriver.local.env"):
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"')
    return os.environ.get(key, default)


def http_json(url: str, timeout: float = 5.0) -> tuple[bool, Any, str]:
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
        return True, json.loads(text), ""
    except Exception as error:
        return False, None, str(error)


def run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def module_import_status() -> dict[str, bool]:
    if not COMFY_VENV_PYTHON.exists():
        return {name: False for name in MISSING_DEPS}
    code = (
        "import importlib.util, json; "
        f"mods={json.dumps([item['import'] for item in MISSING_DEPS.values()])}; "
        "print(json.dumps({m: importlib.util.find_spec(m) is not None for m in mods}))"
    )
    result = run([str(COMFY_VENV_PYTHON), "-c", code], timeout=20)
    try:
        raw = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        raw = {}
    return {name: bool(raw.get(info["import"])) for name, info in MISSING_DEPS.items()}


def python_probe() -> list[str]:
    if not COMFY_VENV_PYTHON.exists():
        return [f"missing: {COMFY_VENV_PYTHON}"]
    code = (
        "import sys, argparse, ssl; "
        "print(sys.executable); "
        "print(sys.version); "
        "print('argparse ok'); "
        "print('ssl ok')"
    )
    result = run([str(COMFY_VENV_PYTHON), "-c", code], timeout=20)
    lines = [f"exit code: {result.returncode}"]
    lines.extend(line.strip() for line in result.stdout.splitlines() if line.strip())
    if result.stderr.strip():
        lines.append("stderr:")
        lines.extend(line.strip() for line in result.stderr.splitlines() if line.strip())
    return lines


def is_api_workflow(data: Any) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    node_like = [
        value
        for value in data.values()
        if isinstance(value, dict) and isinstance(value.get("inputs"), dict) and value.get("class_type")
    ]
    return bool(node_like) and len(node_like) >= max(1, len(data) // 2)


def workflow_class_types(path: Path) -> tuple[str, list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return "invalid", []
    classes: list[str] = []
    if is_api_workflow(data):
        for node in data.values():
            if isinstance(node, dict) and node.get("class_type"):
                classes.append(str(node["class_type"]))
        return "api", sorted(set(classes))
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        for node in data["nodes"]:
            if isinstance(node, dict) and (node.get("type") or node.get("class_type")):
                classes.append(str(node.get("type") or node.get("class_type")))
        return "ui", sorted(set(classes))
    return "unknown", []


def dependency_hits(class_types: list[str]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    haystacks = [(class_type, class_type.lower()) for class_type in class_types]
    for node_name, dep_name, hints in NODE_DEP_RULES:
        for original, lower in haystacks:
            if any(hint.lower() in lower for hint in hints):
                hits.append({"custom_node": node_name, "dependency": dep_name, "class_type": original})
                break
    return hits


def storydriver_workflows() -> tuple[str | None, list[dict[str, Any]]]:
    base = load_env_value("STORYDRIVER_BACKEND_URL", "http://localhost:8001").rstrip("/")
    ok, data, _ = http_json(f"{base}/image-workflows", timeout=6)
    if not ok or not isinstance(data, dict):
        return None, []
    workflows = data.get("workflows") if isinstance(data.get("workflows"), list) else []
    selected = data.get("selected_workflow_id")
    return selected if isinstance(selected, str) else None, workflows


def analyze_workflows() -> tuple[list[dict[str, Any]], list[str]]:
    selected_id, api_workflows = storydriver_workflows()
    results: list[dict[str, Any]] = []
    notes: list[str] = []
    for workflow in api_workflows:
        path = Path(str(workflow.get("workflow_path") or ""))
        if not path.exists():
            continue
        workflow_type, classes = workflow_class_types(path)
        hits = dependency_hits(classes)
        results.append(
            {
                "source": "StoryDriver workflow folder",
                "name": workflow.get("name") or path.name,
                "id": workflow.get("id"),
                "selected": bool(selected_id and workflow.get("id") == selected_id),
                "status": workflow.get("status"),
                "path": str(path),
                "type": workflow_type,
                "class_types": classes,
                "dependency_hits": hits,
            }
        )
    if selected_id is None:
        notes.append("No StoryDriver image workflow is currently selected.")

    zimage_ui = COMFY_WORKFLOW_DIR / "image_z_image_turbo.json"
    if zimage_ui.exists():
        workflow_type, classes = workflow_class_types(zimage_ui)
        results.append(
            {
                "source": "ComfyUI Desktop workflow folder",
                "name": zimage_ui.name,
                "id": None,
                "selected": False,
                "status": "desktop-ui-workflow",
                "path": str(zimage_ui),
                "type": workflow_type,
                "class_types": classes,
                "dependency_hits": dependency_hits(classes),
            }
        )

    base = load_env_value("COMFYUI_BASE_URL", "http://localhost:8188").rstrip("/")
    ok, queue, error = http_json(f"{base}/queue", timeout=6)
    if ok and isinstance(queue, dict):
        prompt_classes: set[str] = set()
        for queue_key in ("queue_running", "queue_pending"):
            queue_items = queue.get(queue_key) if isinstance(queue.get(queue_key), list) else []
            for item in queue_items:
                if not isinstance(item, list) or len(item) < 3 or not isinstance(item[2], dict):
                    continue
                for node in item[2].values():
                    if isinstance(node, dict) and node.get("class_type"):
                        prompt_classes.add(str(node["class_type"]))
        if prompt_classes:
            results.append(
                {
                    "source": "ComfyUI current queue",
                    "name": "queued/running prompt",
                    "id": None,
                    "selected": False,
                    "status": "active" if queue.get("queue_running") else "pending",
                    "path": f"{base}/queue",
                    "type": "api",
                    "class_types": sorted(prompt_classes),
                    "dependency_hits": dependency_hits(sorted(prompt_classes)),
                }
            )
    elif error:
        notes.append(f"Could not inspect ComfyUI queue: {error}")
    return results, notes


def active_listener_command() -> str:
    result = run(["netstat", "-ano", "-p", "tcp"], timeout=10)
    pid = ""
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(":8188"):
                pid = parts[4]
                break
    if not pid:
        return ""
    result = run(["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"], timeout=10)
    for line in result.stdout.splitlines():
        if line.startswith("CommandLine="):
            return line.split("=", 1)[1].strip()
    return f"PID {pid}"


def render_dependency_report() -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    base = load_env_value("COMFYUI_BASE_URL", "http://localhost:8188").rstrip("/")
    ok, stats, stats_error = http_json(f"{base}/system_stats", timeout=6)
    imports = module_import_status()
    workflow_results, workflow_notes = analyze_workflows()
    active_command = active_listener_command()

    lines: list[str] = [
        "# ComfyUI Dependency Report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## ComfyUI Runtime",
        "",
        f"- Base URL: `{base}`",
        f"- Reachable: {'yes' if ok else 'no'}",
    ]
    if ok and isinstance(stats, dict):
        system = stats.get("system") if isinstance(stats.get("system"), dict) else {}
        devices = stats.get("devices") if isinstance(stats.get("devices"), list) else []
        lines.append(f"- ComfyUI version: `{system.get('comfyui_version', 'unknown')}`")
        lines.append(f"- Python version: `{system.get('python_version', 'unknown')}`")
        lines.append(f"- PyTorch version: `{system.get('pytorch_version', 'unknown')}`")
        if devices:
            first = devices[0] if isinstance(devices[0], dict) else {}
            lines.append(f"- Device: `{first.get('name', 'unknown')}`")
    elif stats_error:
        lines.append(f"- System stats error: `{stats_error}`")
    if active_command:
        redacted = re.sub(r"\s+", " ", active_command).strip()
        lines.append(f"- Active listener command: `{redacted}`")

    lines.extend(
        [
            "",
            "## Target Python Environment",
            "",
            f"- Target venv Python: `{COMFY_VENV_PYTHON}`",
            f"- Exists: {'yes' if COMFY_VENV_PYTHON.exists() else 'no'}",
        ]
    )
    lines.extend(f"- {line}" for line in python_probe())

    lines.extend(["", "## Missing Custom-Node Dependencies", ""])
    for dep_name, info in MISSING_DEPS.items():
        present = imports.get(dep_name, False)
        nodes = ", ".join(info["nodes"])
        lines.append(f"### `{info['package']}`")
        lines.append(f"- Import checked: `{info['import']}`")
        lines.append(f"- Present in target venv: {'yes' if present else 'no'}")
        lines.append(f"- Reported failing custom nodes: {nodes}")
        if not present:
            lines.append(f"- Repair helper package: `{info['package']}`")
        lines.append("")

    lines.extend(["## Z-Image Workflow Dependency Impact", ""])
    if workflow_notes:
        lines.extend(f"- {note}" for note in workflow_notes)
    blocked = False
    for result in workflow_results:
        lines.append(f"### {result['name']}")
        lines.append(f"- Source: {result['source']}")
        lines.append(f"- Path/status: `{result['path']}` / `{result['status']}`")
        lines.append(f"- Workflow type: {result['type']}")
        if result.get("selected"):
            lines.append("- Selected in StoryDriver: yes")
        classes = result.get("class_types") or []
        if classes:
            lines.append("- Class types:")
            lines.append("  - " + ", ".join(classes[:30]) + (" ..." if len(classes) > 30 else ""))
        hits = result.get("dependency_hits") or []
        if hits:
            blocked = True
            lines.append("- Uses failing custom-node classes:")
            for hit in hits:
                lines.append(f"  - `{hit['class_type']}` from {hit['custom_node']} needs `{hit['dependency']}`")
        else:
            lines.append("- Uses failing custom-node classes: no")
        lines.append("")
    if not workflow_results:
        lines.append("- No local workflow files or active queue items could be analyzed.")
    if blocked:
        lines.append("Result: at least one analyzed workflow references custom-node classes with missing dependencies.")
    else:
        lines.append("Result: the detected Z-Image Turbo workflow/active queue does not appear blocked by the listed missing custom-node dependencies.")

    packages = [info["package"] for name, info in MISSING_DEPS.items() if not imports.get(name)]
    lines.extend(["", "## Safe Repair Helper", ""])
    if packages:
        lines.append("- Dry run: `D:\\StoryDriver\\scripts\\repair_comfyui_custom_node_deps.bat`")
        lines.append("- Install allowlisted missing packages: `D:\\StoryDriver\\scripts\\repair_comfyui_custom_node_deps.bat install`")
        lines.append("- Packages it would install now:")
        lines.append("  - " + " ".join(packages))
    else:
        lines.append("- All five allowlisted dependency imports are already present in the target venv.")
    lines.extend(
        [
            "",
            "Safety notes:",
            "- The helper targets only the ComfyUI venv above.",
            "- It does not install globally.",
            "- It does not request torch, torchvision, torchaudio, ROCm, CUDA, or driver packages.",
            "- It uses pip's only-if-needed upgrade strategy and does not run broad upgrades.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    REPORT_PATH.write_text(render_dependency_report(), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
