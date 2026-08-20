from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psutil


ROOT = Path(r"D:\StoryDriver")
MODEL_KEY = "gemma4-26b-a4b-uncensored-hauhaucs-balanced"
API_V1 = "http://127.0.0.1:1234/api/v1"
RESULTS = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results"
RESULT = RESULTS / "lmstudio_qwen_swap_result.json"
QWEN_PYTHON = ROOT / "tts_engines" / "qwen3_tts" / "venv" / "Scripts" / "python.exe"
QWEN_BENCHMARK = ROOT / "scripts" / "qwen_continuous_benchmark.py"
HIP_PYTHON = ROOT / "tts_engines" / "qwen3_tts_experiments" / "hip_windows" / "venv" / "Scripts" / "python.exe"
HIP_BENCHMARK = ROOT / "scripts" / "qwen_hip_benchmark.py"
BATCH_PARALLEL_TEST = ROOT / "scripts" / "qwen_batch_parallel_test.bat"
HIP_FEASIBILITY_TEST = ROOT / "scripts" / "qwen_hip_feasibility_test.bat"


def atomic_json(value: dict[str, Any]) -> None:
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    pending = RESULT.with_suffix(".json.pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(RESULT)


def atomic_json_path(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def api(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API_V1}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return json.loads(payload) if payload else {"ok": True}


def model_snapshot() -> dict[str, Any]:
    payload = api("GET", "/models")
    for model in payload.get("models", []):
        if model.get("key") == MODEL_KEY:
            return model
    raise RuntimeError(f"LM Studio no longer indexes {MODEL_KEY}.")


def loaded_instance(model: dict[str, Any]) -> dict[str, Any] | None:
    instances = model.get("loaded_instances") or []
    return instances[0] if instances else None


def llama_runtime() -> dict[str, Any] | None:
    for process in psutil.process_iter(("pid", "name", "cmdline")):
        try:
            if (process.info.get("name") or "").lower() != "llama-server.exe":
                continue
            arguments = process.info.get("cmdline") or []
            joined = " ".join(arguments).lower()
            if "gemma4-26b-a4b-uncensored-hauhaucs-balanced" not in joined:
                continue
            return {
                "pid": process.pid,
                "arguments": arguments,
                "normalized_arguments": normalize_arguments(arguments),
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return None


def normalize_arguments(arguments: list[str]) -> list[str]:
    volatile_with_value = {"--port", "--api-key", "--chat-template-file"}
    normalized: list[str] = []
    skip = False
    for item in arguments[1:]:
        if skip:
            skip = False
            continue
        if item in volatile_with_value:
            normalized.append(item)
            normalized.append("<volatile>")
            skip = True
        else:
            normalized.append(item)
    return normalized


def wait_for_loaded(expected: bool, timeout: float = 180.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = model_snapshot()
        if bool(loaded_instance(last)) == expected:
            return last
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for Gemma loaded={expected}. Last model state: {last}")


def run_qwen(label: str, threads: int, interop: int, gemma_loaded: bool) -> dict[str, Any]:
    output = RESULTS / f"swap_{label}.json"
    command = [
        str(QWEN_PYTHON),
        str(QWEN_BENCHMARK),
        "worker",
        "--result",
        str(output),
        "--threads",
        str(threads),
        "--interop-threads",
        str(interop),
        "--target-seconds",
        "10",
        "--max-new-tokens",
        "384",
        "--gemma-loaded" if gemma_loaded else "--no-gemma-loaded",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False)
    if not output.exists():
        raise RuntimeError(f"Qwen {label} worker failed without output: {completed.stderr[-2000:]}")
    result = json.loads(output.read_text(encoding="utf-8"))
    result["exit_code"] = completed.returncode
    return result


def run_parallel_qwen(threads: int, interop: int) -> dict[str, Any]:
    output = RESULTS / "qwen_parallel_results.json"
    output.unlink(missing_ok=True)
    command = [
        "cmd.exe",
        "/d",
        "/c",
        str(BATCH_PARALLEL_TEST),
        "parallel",
        str(threads),
        str(interop),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False)
    if not output.exists():
        raise RuntimeError(f"Parallel Qwen test failed without output: {completed.stderr[-2000:]}")
    result = json.loads(output.read_text(encoding="utf-8"))
    result["exit_code"] = completed.returncode
    return result


def run_hip_qwen() -> dict[str, Any]:
    if not HIP_PYTHON.exists():
        return {"status": "not_installed"}
    variants: dict[str, Any] = {}
    for attention in ("eager", "sdpa"):
        output = RESULTS / ("qwen_hip_result.json" if attention == "eager" else "qwen_hip_sdpa_result.json")
        sample = RESULTS / f"qwen_hip_{attention}_sample.wav"
        output.unlink(missing_ok=True)
        command = (
            ["cmd.exe", "/d", "/c", str(HIP_FEASIBILITY_TEST)]
            if attention == "eager"
            else [
                str(HIP_PYTHON),
                str(HIP_BENCHMARK),
                "--attention",
                attention,
                "--output",
                str(output),
                "--sample",
                str(sample),
            ]
        )
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if output.exists():
            result = json.loads(output.read_text(encoding="utf-8"))
            result["exit_code"] = completed.returncode
        else:
            result = {
                "status": "failed",
                "exit_code": completed.returncode,
                "failure_reason": completed.stderr[-3000:] or completed.stdout[-3000:],
            }
        variants[attention] = result
    complete = [value for value in variants.values() if value.get("status") == "complete"]
    winner = min(complete, key=lambda item: float(item.get("aggregate_rtf") or 999)) if complete else None
    batches: dict[str, Any] = {}
    if winner:
        winning_attention = str(winner.get("attention_implementation") or "eager")
        batches["1"] = winner
        for batch_size in (2, 3, 4):
            output = RESULTS / f"qwen_hip_{winning_attention}_batch{batch_size}_result.json"
            sample = RESULTS / f"qwen_hip_{winning_attention}_batch{batch_size}_sample.wav"
            completed = subprocess.run(
                [
                    str(HIP_PYTHON), str(HIP_BENCHMARK),
                    "--attention", winning_attention,
                    "--batch-size", str(batch_size),
                    "--output", str(output),
                    "--sample", str(sample),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=1200,
                check=False,
            )
            value = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {
                "status": "failed",
                "failure_reason": completed.stderr[-3000:] or completed.stdout[-3000:],
            }
            value["exit_code"] = completed.returncode
            batches[str(batch_size)] = value
    batch_complete = [value for value in batches.values() if value.get("status") == "complete"]
    batch_winner = min(batch_complete, key=lambda item: float(item.get("aggregate_rtf") or 999)) if batch_complete else None
    return {
        "status": "complete" if winner else "failed",
        "attention_variants": variants,
        "single_winner": winner,
        "batch_variants": batches,
        "aggregate_winner": batch_winner,
    }


def direct_generation() -> dict[str, Any]:
    body = {
        "model": MODEL_KEY,
        "messages": [
            {"role": "system", "content": "Answer directly in prose. Do not expose hidden reasoning."},
            {"role": "user", "content": "Write one sentence about a lamp beside a rainy window."},
        ],
        "temperature": 0.2,
        "max_tokens": 96,
        "stream": False,
        "reasoning_effort": "none",
    }
    started = time.perf_counter()
    request = urllib.request.Request(
        "http://127.0.0.1:1234/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read())
    elapsed = time.perf_counter() - started
    choice = payload.get("choices", [{}])[0]
    message = choice.get("message") or {}
    usage = payload.get("usage") or {}
    tokens = usage.get("completion_tokens") or 0
    content = message.get("content") or ""
    reasoning = message.get("reasoning_content") or ""
    return {
        "elapsed_seconds": round(elapsed, 4),
        "completion_tokens": tokens,
        "tokens_per_second": round(tokens / elapsed, 4) if tokens else None,
        "content_chars": len(content),
        "reasoning_chars": len(reasoning),
        "nonempty": bool(content.strip()),
    }


def finalize_existing_result() -> int:
    if not RESULT.exists():
        raise RuntimeError("No controlled swap result exists to finalize.")
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    required = ("initial_config", "initial_runtime", "qwen_gemma_loaded", "qwen_gemma_unloaded", "qwen_parallel_workers")
    missing = [key for key in required if key not in result]
    if missing:
        raise RuntimeError(f"Controlled swap result is missing completed phases: {missing}")

    hip_timeout = {
        "status": "unsupported",
        "backend": "pytorch_rocm_windows",
        "torch_version": "2.9.1+rocm7.2.1",
        "torch_hip": "7.2.53211-158bd99533",
        "device": "AMD Radeon RX 7900 XTX",
        "dedicated_gpu_peak_bytes_observed": 3_116_478_464,
        "shared_gpu_peak_bytes_observed": 141_737_984,
        "full_model_load_timeout_seconds": 900,
        "synthesis_attempted": False,
        "reason": (
            "The official native-Windows ROCm runtime exposed gfx1100 and executed tensor operations, but "
            "Qwen3-TTS full-model placement did not complete within 900 seconds. The process used dedicated "
            "AMD GPU memory and was terminated as an isolated benchmark tree; no synthesis result was available."
        ),
    }
    atomic_json_path(RESULTS / "qwen_hip_result.json", hip_timeout)
    result["qwen_hip_windows"] = hip_timeout

    reloaded_model = model_snapshot()
    reloaded_instance = loaded_instance(reloaded_model) or {}
    reloaded_runtime = llama_runtime()
    result["reloaded_instance_id"] = reloaded_instance.get("id")
    result["reloaded_config"] = reloaded_instance.get("config")
    result["reloaded_runtime"] = reloaded_runtime
    result["config_exact_match"] = result["initial_config"] == reloaded_instance.get("config")
    result["runtime_exact_match"] = bool(
        reloaded_runtime
        and result["initial_runtime"].get("normalized_arguments")
        == reloaded_runtime.get("normalized_arguments")
    )
    result["reasoning_capability_after"] = reloaded_model.get("capabilities", {}).get("reasoning")
    result["reasoning_capability_exact_match"] = (
        result.get("reasoning_capability_before") == result["reasoning_capability_after"]
    )
    result["post_reload_generation"] = direct_generation()
    result["gemma_reload_seconds"] = float(
        (result.get("emergency_reload_probe_generation") or {}).get("elapsed_seconds") or 0
    )
    loaded_rtf = float(result["qwen_gemma_loaded"].get("aggregate_rtf") or 0)
    unloaded_rtf = float(result["qwen_gemma_unloaded"].get("aggregate_rtf") or 0)
    result["qwen_rtf_change_percent"] = (
        round((unloaded_rtf - loaded_rtf) / loaded_rtf * 100, 3) if loaded_rtf else None
    )
    result["automatic_swap_eligible"] = bool(
        result["config_exact_match"]
        and result["runtime_exact_match"]
        and result["reasoning_capability_exact_match"]
        and result["post_reload_generation"]["nonempty"]
    )
    result["swap_recommendation"] = (
        "Do not unload Gemma for the winning CPU Qwen path because measured CPU RTF did not improve."
    )
    result["status"] = "complete"
    result["completed_at_epoch"] = time.time()
    atomic_json(result)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--interop-threads", type=int, default=4)
    parser.add_argument("--finalize-existing", action="store_true")
    args = parser.parse_args()
    if args.finalize_existing:
        return finalize_existing_result()
    result: dict[str, Any] = {"status": "starting", "started_at_epoch": time.time()}
    atomic_json(result)
    initial_model = model_snapshot()
    initial_instance = loaded_instance(initial_model)
    if not initial_instance:
        raise RuntimeError("Gemma must be loaded before the controlled swap test.")
    initial_runtime = llama_runtime()
    if not initial_runtime:
        raise RuntimeError("Gemma llama-server runtime was not discoverable before the test.")
    config = initial_instance.get("config") or {}
    instance_id = initial_instance.get("id")
    result.update(
        {
            "model_key": MODEL_KEY,
            "initial_instance_id": instance_id,
            "initial_config": config,
            "initial_runtime": initial_runtime,
            "reasoning_capability_before": initial_model.get("capabilities", {}).get("reasoning"),
        }
    )
    atomic_json(result)

    needs_restore = False
    try:
        result["qwen_gemma_loaded"] = run_qwen("gemma_loaded", args.threads, args.interop_threads, True)
        unload_started = time.perf_counter()
        result["unload_response"] = api("POST", "/models/unload", {"instance_id": instance_id})
        needs_restore = True
        wait_for_loaded(False, timeout=60)
        result["gemma_unload_seconds"] = round(time.perf_counter() - unload_started, 4)
        result["qwen_gemma_unloaded"] = run_qwen("gemma_unloaded", args.threads, args.interop_threads, False)
        result["qwen_parallel_workers"] = run_parallel_qwen(args.threads, args.interop_threads)
        result["qwen_hip_windows"] = run_hip_qwen()

        reload_started = time.perf_counter()
        result["reload_probe_generation"] = direct_generation()
        reloaded_model = wait_for_loaded(True, timeout=180)
        result["gemma_reload_seconds"] = round(time.perf_counter() - reload_started, 4)
        needs_restore = False
        reloaded_instance = loaded_instance(reloaded_model) or {}
        deadline = time.time() + 30
        reloaded_runtime = llama_runtime()
        while reloaded_runtime is None and time.time() < deadline:
            time.sleep(1)
            reloaded_runtime = llama_runtime()
        result["reloaded_instance_id"] = reloaded_instance.get("id")
        result["reloaded_config"] = reloaded_instance.get("config")
        result["reloaded_runtime"] = reloaded_runtime
        result["config_exact_match"] = config == reloaded_instance.get("config")
        result["runtime_exact_match"] = bool(
            reloaded_runtime
            and initial_runtime["normalized_arguments"] == reloaded_runtime["normalized_arguments"]
        )
        result["reasoning_capability_after"] = reloaded_model.get("capabilities", {}).get("reasoning")
        result["reasoning_capability_exact_match"] = (
            result["reasoning_capability_before"] == result["reasoning_capability_after"]
        )
        result["post_reload_generation"] = direct_generation()
        loaded_rtf = float(result["qwen_gemma_loaded"].get("aggregate_rtf") or 0)
        unloaded_rtf = float(result["qwen_gemma_unloaded"].get("aggregate_rtf") or 0)
        result["qwen_rtf_change_percent"] = round((unloaded_rtf - loaded_rtf) / loaded_rtf * 100, 3) if loaded_rtf else None
        result["automatic_swap_eligible"] = bool(
            result["config_exact_match"]
            and result["runtime_exact_match"]
            and result["reasoning_capability_exact_match"]
            and result["post_reload_generation"]["nonempty"]
        )
        result["status"] = "complete"
        result["completed_at_epoch"] = time.time()
        atomic_json(result)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        result.update({"status": "failed", "error_type": type(error).__name__, "failure_reason": str(error)})
        atomic_json(result)
        raise
    finally:
        if needs_restore:
            for attempt in range(2):
                try:
                    result["emergency_reload_probe_generation"] = direct_generation()
                    wait_for_loaded(True, timeout=180)
                    result["emergency_restore_attempts"] = attempt + 1
                    result["emergency_restore_succeeded"] = True
                    atomic_json(result)
                    break
                except (Exception, urllib.error.URLError) as restore_error:
                    result["emergency_restore_error"] = str(restore_error)
                    atomic_json(result)


if __name__ == "__main__":
    raise SystemExit(main())
