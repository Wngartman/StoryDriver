from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(r"D:\StoryDriver")
RESULTS = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results"
EXPERIMENTS = ROOT / "tts_engines" / "qwen3_tts_experiments"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def directml() -> int:
    experiment = EXPERIMENTS / "directml"
    experiment.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "pip", "index", "versions", "torch-directml"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    result = {
        "status": "unsupported",
        "backend": "torch-directml",
        "tested_at_epoch": time.time(),
        "python": sys.version,
        "platform": platform.platform(),
        "torch_directml_importable": importlib.util.find_spec("torch_directml") is not None,
        "pip_index_exit_code": completed.returncode,
        "pip_index_stdout": completed.stdout.strip(),
        "pip_index_stderr": completed.stderr.strip(),
        "official_supported_torch_ceiling": "2.3.1",
        "qwen_working_torch": "2.11.0+cpu",
        "reason": (
            "No torch-directml distribution is published for this Python 3.12 Windows environment. "
            "The current official backend supports only PyTorch through 2.3.1, below the working Qwen stack."
        ),
        "model_load_attempted": False,
        "model_load_reason": "A compatible torch-directml package could not be installed, so a GPU model load would be false testing.",
        "dedicated_gpu_activity_confirmed": False,
    }
    atomic_json(RESULTS / "qwen_directml_result.json", result)
    atomic_json(experiment / "result.json", result)
    print(json.dumps(result, indent=2))
    return 0


def onnx_assess() -> int:
    source = (
        ROOT
        / "tts_engines"
        / "qwen3_tts"
        / "venv"
        / "Lib"
        / "site-packages"
        / "qwen_tts"
        / "core"
        / "models"
        / "modeling_qwen3_tts.py"
    )
    text = source.read_text(encoding="utf-8")
    evidence = {
        "uses_generation_mixin": "GenerationMixin" in text,
        "nested_subtalker_generate": "predictor_result = self.code_predictor.generate" in text,
        "mutable_generation_step": 'model_kwargs["generation_step"]' in text,
        "mutable_past_hidden": 'model_kwargs["past_hidden"]' in text,
        "dynamic_past_key_values": "past_key_values" in text,
        "python_autoregressive_return": "return talker_codes_list, talker_hidden_states_list" in text,
    }
    result = {
        "status": "structural_gate_passed_for_export_attempt",
        "backend": "onnxruntime-directml",
        "tested_at_epoch": time.time(),
        "source": str(source),
        "evidence": evidence,
        "dominant_stage": "autoregressive speech-token generation",
        "assessment": (
            "A static neural subgraph can be export-tested, but complete synthesis includes a Python GenerationMixin loop, "
            "a nested code-predictor generation loop, sampling, mutable KV cache, mutable past_hidden, and stopping logic. "
            "Exporting one static forward does not constitute a working TTS backend."
        ),
        "export_attempt_required": True,
    }
    atomic_json(RESULTS / "qwen_onnx_structural_assessment.json", result)
    print(json.dumps(result, indent=2))
    return 0


def hip_info() -> int:
    import winreg

    driver_version = None
    software_version = None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\AMD\CN") as key:
            driver_version = winreg.QueryValueEx(key, "DriverVersion")[0]
    except OSError:
        pass
    radeon = Path(r"C:\Program Files\AMD\CNext\CNext\RadeonSoftware.exe")
    if radeon.exists():
        query = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"(Get-Item '{radeon}').VersionInfo.ProductVersion"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        software_version = query.stdout.strip() or None
    tool_results = {}
    for tool in ("hipInfo.exe", "rocminfo.exe", "hipcc.exe"):
        found = subprocess.run(
            ["where.exe", tool], capture_output=True, text=True, timeout=10, check=False
        )
        tool_results[tool] = found.stdout.strip().splitlines() if found.returncode == 0 else []
    result = {
        "status": "portable_wheel_test_required",
        "backend": "pytorch_rocm_windows",
        "tested_at_epoch": time.time(),
        "driver_version": driver_version,
        "radeon_software_product_version": software_version,
        "tools": tool_results,
        "official_required_driver": "26.2.2",
        "official_rocm_version": "7.2.1",
        "official_pytorch_version": "2.9.1+rocm7.2.1",
        "official_python_version": "3.12",
        "official_gpu_support": "RX 7900 XTX / gfx1100",
        "system_hip_sdk_present": any(tool_results.values()),
        "reason": (
            "No system HIP SDK is installed, but AMD now publishes official D-only-installable Python wheels. "
            "The isolated wheel path must be tested without changing the display driver or global environment."
        ),
    }
    atomic_json(RESULTS / "qwen_hip_environment_info.json", result)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("backend", choices=("directml", "onnx", "hip-info"))
    args = parser.parse_args()
    os.environ.setdefault("PIP_CACHE_DIR", str(ROOT / "backend" / "data" / "temp" / "pip-cache"))
    if args.backend == "directml":
        return directml()
    if args.backend == "onnx":
        return onnx_assess()
    return hip_info()


if __name__ == "__main__":
    raise SystemExit(main())
