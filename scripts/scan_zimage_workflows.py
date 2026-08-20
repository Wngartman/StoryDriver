from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "backend" / "data" / "logs"
STORYDRIVER_WORKFLOW_DIR = REPO_ROOT / "backend" / "data" / "comfy_workflows"
COMFY_USER_DIR = Path(os.environ.get("USERPROFILE", r"C:\Users\wngar")) / "Documents" / "ComfyUI"
COMFY_WORKFLOW_DIR = COMFY_USER_DIR / "user" / "default" / "workflows"
COMFY_DEFAULT_DIR = COMFY_USER_DIR / "user" / "default"
COMFY_INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\wngar\AppData\Local")) / "Programs" / "ComfyUI"

WORKFLOW_KEYWORDS = (
    "zimage",
    "z_image",
    "z-image",
    "z image",
    "zimage_turbo",
    "turbo",
    "sion",
    "image",
)
MODEL_KEYWORDS = ("zimage", "z_image", "z-image", "z image", "z-img", "z_img", "turbo")
PROMPT_HINTS = ("prompt", "positive", "negative", "text", "cliptextencode", "conditioning")
SEED_HINTS = ("seed", "noise_seed")
OUTPUT_HINTS = ("saveimage", "previewimage", "output", "save", "image")
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "input",
    "output",
    "temp",
    "cache",
    "models",
    "custom_nodes",
    "venv",
}


@dataclass
class NodeCandidate:
    node_id: str
    class_type: str
    input_names: list[str] = field(default_factory=list)
    role: str = "prompt"
    preview: str = ""


def safe_read_text(path: Path, limit: int | None = None) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if limit is not None:
        data = data[:limit]
    return data.decode("utf-8", errors="replace")


def text_preview(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in keywords)


def is_api_workflow(data: Any) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    values = list(data.values())
    node_values = [value for value in values if isinstance(value, dict) and "class_type" in value and "inputs" in value]
    return bool(node_values) and len(node_values) >= max(1, len(values) // 2)


def is_ui_workflow(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("nodes"), list)


def find_strings(value: Any, keywords: tuple[str, ...], results: list[str], limit: int = 12) -> None:
    if len(results) >= limit:
        return
    if isinstance(value, str):
        if contains_keyword(value, keywords):
            results.append(text_preview(value, 160))
        return
    if isinstance(value, dict):
        for item in value.values():
            find_strings(item, keywords, results, limit)
            if len(results) >= limit:
                break
        return
    if isinstance(value, list):
        for item in value:
            find_strings(item, keywords, results, limit)
            if len(results) >= limit:
                break


def detect_api_candidates(data: dict[str, Any]) -> tuple[list[NodeCandidate], list[NodeCandidate], list[NodeCandidate], list[str]]:
    prompt_candidates: list[NodeCandidate] = []
    seed_candidates: list[NodeCandidate] = []
    output_candidates: list[NodeCandidate] = []
    class_types: list[str] = []
    for node_id, node in data.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        class_types.append(class_type)
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        input_names = [str(name) for name in inputs.keys()]
        haystack = " ".join([node_id, class_type, *input_names, *[str(v) for v in inputs.values() if isinstance(v, str)]])

        prompt_inputs = [name for name in input_names if contains_keyword(name, PROMPT_HINTS)]
        text_inputs = [name for name, value in inputs.items() if isinstance(value, str) and len(value.strip()) > 0]
        if contains_keyword(haystack, PROMPT_HINTS) or prompt_inputs or text_inputs:
            role = "negative" if contains_keyword(haystack, ("negative", "avoid", "bad", "lowres")) else "positive" if contains_keyword(haystack, ("positive",)) else "prompt"
            preview_value = next((inputs[name] for name in prompt_inputs + text_inputs if name in inputs and isinstance(inputs[name], str)), "")
            prompt_candidates.append(
                NodeCandidate(
                    node_id=str(node_id),
                    class_type=class_type,
                    input_names=input_names,
                    role=role,
                    preview=text_preview(preview_value),
                )
            )

        if contains_keyword(haystack, SEED_HINTS):
            seed_candidates.append(NodeCandidate(str(node_id), class_type, input_names, role="seed"))
        if contains_keyword(class_type, OUTPUT_HINTS):
            output_candidates.append(NodeCandidate(str(node_id), class_type, input_names, role="output"))
    return prompt_candidates, seed_candidates, output_candidates, sorted(set(filter(None, class_types)))


def detect_ui_candidates(data: dict[str, Any]) -> tuple[list[NodeCandidate], list[NodeCandidate], list[NodeCandidate], list[str]]:
    prompt_candidates: list[NodeCandidate] = []
    seed_candidates: list[NodeCandidate] = []
    output_candidates: list[NodeCandidate] = []
    class_types: list[str] = []
    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        class_type = str(node.get("type") or node.get("class_type") or "")
        class_types.append(class_type)
        input_names: list[str] = []
        if isinstance(node.get("inputs"), list):
            input_names.extend(str(item.get("name")) for item in node["inputs"] if isinstance(item, dict) and item.get("name"))
        if isinstance(node.get("widgets_values"), list):
            input_names.extend(f"widget_{index}" for index, _ in enumerate(node["widgets_values"]))
        widgets = node.get("widgets_values") if isinstance(node.get("widgets_values"), list) else []
        haystack = " ".join([node_id, class_type, *input_names, *[str(value) for value in widgets if isinstance(value, str)]])
        text_widgets = [value for value in widgets if isinstance(value, str) and value.strip()]
        if contains_keyword(haystack, PROMPT_HINTS) or text_widgets:
            role = "negative" if contains_keyword(haystack, ("negative", "avoid", "bad", "lowres")) else "positive" if contains_keyword(haystack, ("positive",)) else "prompt"
            prompt_candidates.append(NodeCandidate(node_id, class_type, input_names, role=role, preview=text_preview(text_widgets[0] if text_widgets else "")))
        if contains_keyword(haystack, SEED_HINTS):
            seed_candidates.append(NodeCandidate(node_id, class_type, input_names, role="seed"))
        if contains_keyword(class_type, OUTPUT_HINTS):
            output_candidates.append(NodeCandidate(node_id, class_type, input_names, role="output"))
    return prompt_candidates, seed_candidates, output_candidates, sorted(set(filter(None, class_types)))


def candidate_json_paths() -> list[Path]:
    roots = [
        STORYDRIVER_WORKFLOW_DIR,
        COMFY_USER_DIR,
        COMFY_WORKFLOW_DIR,
        COMFY_DEFAULT_DIR,
    ]
    seen: set[Path] = set()
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name.lower() not in SKIP_DIRS]
            current_path = Path(current)
            for name in files:
                path = current_path / name
                if path.suffix.lower() != ".json":
                    continue
                if not contains_keyword(path.name, WORKFLOW_KEYWORDS):
                    content = safe_read_text(path, limit=200000)
                    if not contains_keyword(content, WORKFLOW_KEYWORDS):
                        continue
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(resolved)
    return sorted(paths, key=lambda item: str(item).lower())


def model_refs() -> list[Path]:
    refs: list[Path] = []
    for folder_name in ("diffusion_models", "unet", "checkpoints", "text_encoders", "vae"):
        folder = COMFY_USER_DIR / "models" / folder_name
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and contains_keyword(path.name, MODEL_KEYWORDS):
                refs.append(path)
    return sorted(refs, key=lambda item: str(item).lower())


def recent_log_paths() -> list[Path]:
    roots = [
        COMFY_USER_DIR,
        COMFY_INSTALL_DIR,
        LOG_DIR,
    ]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name.lower() not in SKIP_DIRS]
            depth = len(Path(current).relative_to(root).parts) if Path(current) != root else 0
            if depth > 4:
                dirs[:] = []
            for name in files:
                lower = name.lower()
                if lower.endswith((".log", ".txt")) and ("comfy" in lower or "launch" in lower or "diagnostic" in lower):
                    path = Path(current) / name
                    try:
                        if path.stat().st_size <= 3_000_000:
                            candidates.append(path)
                    except OSError:
                        continue
    candidates.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return candidates[:12]


def parse_dependency_errors(log_paths: list[Path]) -> dict[str, dict[str, set[str]]]:
    missing: dict[str, dict[str, set[str]]] = {}
    module_pattern = re.compile(r"(?:ModuleNotFoundError|ImportError):.*?(?:No module named|cannot import name) ['\"]?([^'\"\s]+)")
    node_pattern = re.compile(r"Cannot import (.+?) module for custom nodes", re.IGNORECASE)
    for path in log_paths:
        text = safe_read_text(path)
        lines = text.splitlines()
        last_modules: list[str] = []
        for line in lines:
            module_match = module_pattern.search(line)
            if module_match:
                module = module_match.group(1).strip()
                if module:
                    last_modules.append(module)
                    missing.setdefault(module, {"logs": set(), "nodes": set()})["logs"].add(str(path))
            node_match = node_pattern.search(line)
            if node_match:
                node_name = node_match.group(1).strip()
                for module in last_modules[-3:] or ["unknown"]:
                    missing.setdefault(module, {"logs": set(), "nodes": set()})["nodes"].add(node_name)
    return missing


def run_comfy_python_probe(python_path: Path) -> list[str]:
    if not python_path.exists():
        return ["ComfyUI venv Python does not exist."]
    probe = (
        "import sys; "
        "print(sys.executable); "
        "import argparse; print('argparse ok'); "
        "import ssl; print('ssl ok'); "
        "import torch; print('torch ' + getattr(torch, '__version__', 'unknown')); "
        "import torchvision; print('torchvision ' + getattr(torchvision, '__version__', 'unknown')); "
        "import torchaudio; print('torchaudio ' + getattr(torchaudio, '__version__', 'unknown'))"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-c", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
    except Exception as error:
        return [f"Probe failed to run: {error}"]
    output = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    stderr = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    lines = [f"exit code: {result.returncode}", *output]
    if stderr:
        lines.append("stderr:")
        lines.extend(stderr[:12])
        if len(stderr) > 12:
            lines.append(f"... {len(stderr) - 12} more stderr lines")
    return lines


def render_node_list(nodes: list[NodeCandidate], limit: int = 8) -> list[str]:
    lines: list[str] = []
    for node in nodes[:limit]:
        inputs = ", ".join(node.input_names[:8]) or "none"
        lines.append(f"  - `{node.node_id}` `{node.class_type}` ({node.role}) inputs: {inputs}")
    if len(nodes) > limit:
        lines.append(f"  - ... {len(nodes) - limit} more")
    return lines


def render_scan_report() -> tuple[str, dict[str, dict[str, Any]]]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    workflows: dict[str, dict[str, Any]] = {}
    lines = [
        "# Z-Image Workflow Scan",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Search Roots",
        "",
        f"- StoryDriver API workflow folder: `{STORYDRIVER_WORKFLOW_DIR}`",
        f"- ComfyUI Desktop workflow folder: `{COMFY_WORKFLOW_DIR}`",
        f"- ComfyUI default user folder: `{COMFY_DEFAULT_DIR}`",
        "",
        "## Model/File Clues",
        "",
    ]
    refs = model_refs()
    if refs:
        lines.extend(f"- `{path}`" for path in refs[:20])
    else:
        lines.append("- No Z-Image-looking model files found in the common ComfyUI model folders.")

    paths = candidate_json_paths()
    lines.extend(["", "## Workflow Candidates", ""])
    if not paths:
        lines.append("No candidate workflow JSON files were found.")
        return "\n".join(lines) + "\n", workflows

    for path in paths:
        entry: dict[str, Any] = {"path": str(path)}
        try:
            data = json.loads(safe_read_text(path))
        except Exception as error:
            lines.extend([f"### `{path.name}`", "", f"- Path: `{path}`", f"- Status: could not parse JSON: {error}", ""])
            continue
        api = is_api_workflow(data)
        ui = is_ui_workflow(data)
        workflow_type = "API workflow" if api else "Desktop UI workflow" if ui else "unknown JSON"
        if api:
            prompt_nodes, seed_nodes, output_nodes, class_types = detect_api_candidates(data)
        elif ui:
            prompt_nodes, seed_nodes, output_nodes, class_types = detect_ui_candidates(data)
        else:
            prompt_nodes, seed_nodes, output_nodes, class_types = [], [], [], []
        refs_in_json: list[str] = []
        find_strings(data, MODEL_KEYWORDS, refs_in_json)
        entry.update(
            {
                "type": workflow_type,
                "api": api,
                "prompt_candidates": [node.__dict__ for node in prompt_nodes],
                "seed_candidates": [node.__dict__ for node in seed_nodes],
                "output_candidates": [node.__dict__ for node in output_nodes],
                "class_types": class_types,
                "model_refs": refs_in_json,
            }
        )
        workflows[str(path)] = entry
        lines.extend(
            [
                f"### `{path.name}`",
                "",
                f"- Path: `{path}`",
                f"- Type: {workflow_type}",
                f"- API-ready for `/prompt`: {'yes' if api else 'no'}",
                f"- Prompt-like candidates: {len(prompt_nodes)}",
                f"- Seed candidates: {len(seed_nodes)}",
                f"- Output/save candidates: {len(output_nodes)}",
            ]
        )
        if refs_in_json:
            lines.append("- Z-Image/model references:")
            lines.extend(f"  - {ref}" for ref in refs_in_json[:8])
        if class_types:
            lines.append("- Class types:")
            lines.append("  - " + ", ".join(class_types[:24]) + (" ..." if len(class_types) > 24 else ""))
        if prompt_nodes:
            lines.append("- Detected prompt-like nodes:")
            lines.extend(render_node_list(prompt_nodes))
        if seed_nodes:
            lines.append("- Detected seed nodes:")
            lines.extend(render_node_list(seed_nodes, limit=5))
        if output_nodes:
            lines.append("- Detected output/save nodes:")
            lines.extend(render_node_list(output_nodes, limit=5))
        if not api and ui:
            lines.append("- Note: this looks like a ComfyUI Desktop UI workflow. StoryDriver needs an exported API workflow JSON before real generation.")
        lines.append("")

    api_candidates = [entry for entry in workflows.values() if entry.get("api")]
    lines.extend(["## Summary", ""])
    lines.append(f"- Candidate JSON files scanned: {len(paths)}")
    lines.append(f"- API workflow candidates found: {len(api_candidates)}")
    if not api_candidates:
        lines.append("- No API-ready Z-Image workflow was found. Export API JSON from ComfyUI before running StoryDriver real image generation.")
    return "\n".join(lines) + "\n", workflows


def render_dependency_report() -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logs = recent_log_paths()
    missing = parse_dependency_errors(logs)
    python_path = COMFY_USER_DIR / ".venv" / "Scripts" / "python.exe"
    uv_path = COMFY_INSTALL_DIR / "resources" / "uv" / "win" / "uv.exe"
    lines = [
        "# ComfyUI Dependency Report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"- ComfyUI venv Python: `{python_path}`",
        f"- Python exists: {'yes' if python_path.exists() else 'no'}",
        f"- ComfyUI uv helper: `{uv_path}`",
        f"- uv helper exists: {'yes' if uv_path.exists() else 'no'}",
        "- Codex installed packages: no",
        "",
        "## Current Python Import Probe",
        "",
    ]
    lines.extend(f"- {line}" for line in run_comfy_python_probe(python_path))
    lines.extend(
        [
            "",
            "## Recent Logs Inspected",
            "",
        ]
    )
    if logs:
        lines.extend(f"- `{path}`" for path in logs)
    else:
        lines.append("- No recent ComfyUI logs found in the scanned folders.")
    lines.extend(["", "## Missing Imports Seen In Logs", ""])
    if not missing:
        lines.append("- No missing Python module import errors were found in recent scanned logs.")
    else:
        for module, details in sorted(missing.items()):
            nodes = sorted(details["nodes"]) or ["unknown custom node"]
            lines.append(f"### `{module}`")
            lines.append(f"- Affected custom nodes: {', '.join(nodes)}")
            if module == "cv2":
                package = "opencv-python or opencv-python-headless"
            elif module == "argparse":
                package = "none; argparse is Python standard library and the current probe imports it successfully"
            elif module.startswith("_") or "." in module:
                package = "uncertain; check the affected custom node requirements"
            else:
                package = module
            lines.append(f"- Suggested package to verify: `{package}`")
            lines.append("- Suggested install path: use ComfyUI Manager or the custom node's own requirements first.")
            if module == "argparse":
                lines.append("- No manual install suggested for argparse.")
            elif python_path.exists() and uv_path.exists():
                lines.append(
                    f"- If manual install is confirmed safe: `{uv_path} pip install <package> --python {python_path}`"
                )
            lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            "- This report is diagnostic only. It does not install packages or alter custom nodes.",
            "- Install dependencies only when a selected workflow requires the affected custom node.",
            "- Do not install CUDA/NVIDIA packages on this AMD system unless ComfyUI Desktop explicitly requires them.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    scan_report, _ = render_scan_report()
    try:
        from comfyui_dependency_check import render_dependency_report as render_focused_dependency_report

        dependency_report = render_focused_dependency_report()
    except Exception:
        dependency_report = render_dependency_report()
    (LOG_DIR / "ZIMAGE_WORKFLOW_SCAN.md").write_text(scan_report, encoding="utf-8")
    (LOG_DIR / "COMFYUI_DEPENDENCY_REPORT.md").write_text(dependency_report, encoding="utf-8")
    sys.stdout.write(f"Wrote {LOG_DIR / 'ZIMAGE_WORKFLOW_SCAN.md'}\n")
    sys.stdout.write(f"Wrote {LOG_DIR / 'COMFYUI_DEPENDENCY_REPORT.md'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
