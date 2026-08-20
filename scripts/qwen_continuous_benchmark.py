from __future__ import annotations

import argparse
import csv
import gc
import inspect
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Callable

import numpy as np
import psutil
import soundfile as sf
import torch


ROOT = Path(r"D:\StoryDriver")
ENGINE_ROOT = ROOT / "tts_engines" / "qwen3_tts"
MODEL_PATH = ENGINE_ROOT / "models" / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
RESULTS_DIR = ENGINE_ROOT / "benchmarks" / "results"
CORPUS_PATH = ENGINE_ROOT / "benchmarks" / "synthetic_corpus.json"
JSON_RESULTS = RESULTS_DIR / "qwen_continuous_results.json"
CSV_RESULTS = RESULTS_DIR / "qwen_continuous_results.csv"

SYNTHETIC_SENTENCES = (
    "At seven fifteen, Elena Mercer checked the brass latch and listened to rain cross the apartment windows.",
    "The kettle clicked off, but neither woman reached for it; the unfinished question remained between them.",
    "'You knew before Tuesday,' Priya said quietly, setting the blue notebook beside the lamp.",
    "Elena turned from the window. 'I suspected. Knowing is a different thing.'",
    "Beyond the glass, traffic made a low river of sound, bright tires hissing over wet pavement.",
    "A message from Dr. Imani Vale showed 14:32, platform nine, and the code Aster-47B.",
    "Priya folded her coat over the chair so the key in its pocket would not strike the wooden floor.",
    "They did not agree, but the silence changed: it became a decision waiting for a name.",
    "In the western watchtower, Captain Ysabet marked three roads without moving the carved pieces toward battle.",
    "The plan depended on weather, tired horses, two guarded bridges, and a gate that opened only at dawn.",
    "Aboard the survey ship Caldera, the coolant alarm repeated twice before Engineer Nia Solano muted it.",
    "Numbers alone could not explain why deck four smelled of ozone or why Tomas avoided her eyes.",
    "Marek crossed behind the broken fountain, kept the stone wall on his left, and passed the compass to Rhea.",
    "Rhea held the compass in her gloved right hand while Jun watched the archway from six paces away.",
    "No one rushed the final answer; each small movement altered what the others were willing to risk.",
    "By the paragraph's end, they knew more than before, yet the next choice still belonged to them.",
)


def configure_local_environment() -> None:
    temp = ROOT / "backend" / "data" / "temp"
    values = {
        "TMP": temp,
        "TEMP": temp,
        "PIP_CACHE_DIR": temp / "pip-cache",
        "HF_HOME": ROOT / "tts_engines" / "cache" / "huggingface",
        "HUGGINGFACE_HUB_CACHE": ROOT / "tts_engines" / "cache" / "huggingface" / "hub",
        "TRANSFORMERS_CACHE": ROOT / "tts_engines" / "cache" / "transformers",
        "TORCH_HOME": ROOT / "tts_engines" / "cache" / "torch",
        "XDG_CACHE_HOME": ROOT / "tts_engines" / "cache",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    for key, value in values.items():
        os.environ[key] = str(value)
    temp.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def corpus_for_seconds(target_seconds: int) -> str:
    target_words = max(12, math.ceil(target_seconds * 2.42))
    sentences: list[str] = []
    words = 0
    index = 0
    while words < target_words:
        sentence = SYNTHETIC_SENTENCES[index % len(SYNTHETIC_SENTENCES)]
        sentences.append(sentence)
        words += len(sentence.split())
        index += 1
    return "\n\n".join(sentences)


def ensure_corpus() -> dict[str, str]:
    corpus = {
        "10_seconds": corpus_for_seconds(10),
        "30_seconds": corpus_for_seconds(30),
        "60_seconds": corpus_for_seconds(60),
        "6_minutes": corpus_for_seconds(360),
        "12_minutes": corpus_for_seconds(720),
    }
    atomic_json(CORPUS_PATH, corpus)
    return corpus


class ProcessMonitor:
    def __init__(self) -> None:
        self.process = psutil.Process()
        self.stop_event = threading.Event()
        self.peak_rss = self.process.memory_info().rss
        self.peak_cpu_percent = 0.0
        self.samples = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.process.cpu_percent(None)
        while not self.stop_event.wait(0.2):
            try:
                self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
                self.peak_cpu_percent = max(self.peak_cpu_percent, self.process.cpu_percent(None))
                self.samples += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return

    def __enter__(self) -> "ProcessMonitor":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
        try:
            self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
        except psutil.Error:
            pass


def audio_checks(waveform: np.ndarray, sample_rate: int) -> dict[str, Any]:
    audio = np.asarray(waveform, dtype=np.float32).reshape(-1)
    finite = bool(np.isfinite(audio).all())
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    silence_ratio = float(np.mean(np.abs(audio) < 0.001)) if len(audio) else 1.0
    clipping_ratio = float(np.mean(np.abs(audio) >= 0.999)) if len(audio) else 0.0
    duration = len(audio) / max(sample_rate, 1)
    valid = finite and duration > 0.25 and rms > 0.002 and clipping_ratio < 0.01 and silence_ratio < 0.95
    return {
        "audio_valid": valid,
        "audio_corruption": not finite,
        "duration_seconds": round(duration, 4),
        "peak_amplitude": round(peak, 6),
        "rms": round(rms, 6),
        "silence_ratio": round(silence_ratio, 6),
        "clipping_ratio": round(clipping_ratio, 6),
    }


def set_process_policy(affinity: str, priority: str) -> dict[str, Any]:
    process = psutil.Process()
    available = process.cpu_affinity()
    selected = available
    if affinity == "physical":
        selected = available[::2] or available
    elif affinity == "first8":
        selected = available[:8]
    process.cpu_affinity(selected)
    if priority == "below_normal":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    elif priority == "normal":
        process.nice(psutil.NORMAL_PRIORITY_CLASS)
    return {"available_affinity": available, "selected_affinity": selected, "priority": priority}


def dtype_from_name(name: str) -> torch.dtype:
    return {
        "float32": torch.float32,
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
    }[name]


def timed_wrapper(target: object, attribute: str, timings: dict[str, float], label: str) -> Callable[[], None]:
    original = getattr(target, attribute)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            timings[label] = timings.get(label, 0.0) + time.perf_counter() - started

    setattr(target, attribute, wrapped)

    def restore() -> None:
        setattr(target, attribute, original)

    return restore


def worker(args: argparse.Namespace) -> int:
    result_path = Path(args.result)
    result: dict[str, Any] = {
        "status": "starting",
        "engine": "qwen3_tts_custom_voice_0.6b",
        "backend": "pytorch_cpu",
        "dtype": args.dtype,
        "threads": args.threads,
        "interop_threads": args.interop_threads,
        "process_count": args.process_count,
        "batch_size": args.batch_size,
        "chunk_target_seconds": args.target_seconds,
        "trial": args.trial,
        "gemma_loaded": args.gemma_loaded,
        "mode": args.mode,
        "attention_implementation": args.attention,
        "cache_implementation": args.cache_implementation,
        "affinity": args.affinity,
        "priority": args.priority,
        "do_sample": args.do_sample,
        "non_streaming_mode": args.non_streaming_mode,
        "started_at_epoch": time.time(),
        "first_packet_seconds": None,
        "first_packet_reason": "Official API returns only after full token generation and decode.",
        "dedicated_gpu_peak_bytes": None,
        "shared_gpu_peak_bytes": None,
        "gpu_engine": None,
        "gpu_measurement_reason": "No Qwen GPU backend active in this CPU worker.",
    }
    atomic_json(result_path, result)
    model = None
    try:
        policy = set_process_policy(args.affinity, args.priority)
        result.update(policy)
        os.environ["OMP_NUM_THREADS"] = str(args.threads)
        os.environ["MKL_NUM_THREADS"] = str(args.threads)
        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(args.interop_threads)
        torch.manual_seed(20260714 + args.trial)
        result["torch_version"] = torch.__version__
        result["mkldnn_enabled"] = torch.backends.mkldnn.enabled
        result["inference_mode_used"] = True

        from qwen_tts import Qwen3TTSModel

        with ProcessMonitor() as monitor:
            process_started = time.perf_counter()
            load_started = time.perf_counter()
            model = Qwen3TTSModel.from_pretrained(
                str(MODEL_PATH),
                device_map="cpu",
                dtype=dtype_from_name(args.dtype),
                attn_implementation=args.attention,
                local_files_only=True,
            )
            model_load_seconds = time.perf_counter() - load_started
            model.model.eval()
            compile_seconds = 0.0
            if args.mode == "compile":
                compile_started = time.perf_counter()
                model.model.talker = torch.compile(model.model.talker, mode="reduce-overhead", dynamic=True)
                compile_seconds = time.perf_counter() - compile_started
            elif args.mode == "dynamic_int8":
                quantize_started = time.perf_counter()
                model.model.talker = torch.ao.quantization.quantize_dynamic(
                    model.model.talker,
                    {torch.nn.Linear},
                    dtype=torch.qint8,
                    inplace=False,
                )
                compile_seconds = time.perf_counter() - quantize_started
            elif args.mode == "torchao_int8":
                from torchao.quantization import Int8WeightOnlyConfig, quantize_

                quantize_started = time.perf_counter()
                quantize_(model.model.talker, Int8WeightOnlyConfig())
                compile_seconds = time.perf_counter() - quantize_started

            text = corpus_for_seconds(args.target_seconds)
            texts = [text] * args.batch_size
            speakers = [args.voice] * args.batch_size
            languages = ["English"] * args.batch_size
            timings: dict[str, float] = {}
            restores = [
                timed_wrapper(model, "_tokenize_texts", timings, "tokenizer_text_preparation_seconds"),
                timed_wrapper(model.model, "generate", timings, "autoregressive_generation_seconds"),
                timed_wrapper(model.model.speech_tokenizer, "decode", timings, "codec_decode_seconds"),
            ]
            synth_started = time.perf_counter()
            try:
                with torch.inference_mode():
                    generation_options: dict[str, Any] = {
                        "max_new_tokens": args.max_new_tokens,
                        "do_sample": args.do_sample,
                        "subtalker_dosample": args.do_sample,
                    }
                    if args.cache_implementation != "default":
                        generation_options["cache_implementation"] = args.cache_implementation
                    wavs, sample_rate = model.generate_custom_voice(
                        text=texts if args.batch_size > 1 else texts[0],
                        language=languages if args.batch_size > 1 else languages[0],
                        speaker=speakers if args.batch_size > 1 else speakers[0],
                        non_streaming_mode=args.non_streaming_mode,
                        **generation_options,
                    )
            finally:
                for restore in reversed(restores):
                    restore()
            synthesis_seconds = time.perf_counter() - synth_started
            first_playable_seconds = time.perf_counter() - process_started

            write_started = time.perf_counter()
            output_paths: list[str] = []
            checks: list[dict[str, Any]] = []
            for index, waveform in enumerate(wavs):
                output = result_path.with_suffix(f".audio{index}.wav")
                sf.write(output, waveform, sample_rate, subtype="PCM_16")
                output_paths.append(str(output))
                checks.append(audio_checks(waveform, sample_rate))
            disk_write_seconds = time.perf_counter() - write_started
            generated_duration = sum(item["duration_seconds"] for item in checks)
            max_duration = max((item["duration_seconds"] for item in checks), default=0.0)
            result.update(
                {
                    "status": "complete",
                    "model_load_seconds": round(model_load_seconds, 4),
                    "compile_or_quantize_seconds": round(compile_seconds, 4),
                    "text_normalization_seconds": 0.0,
                    **{key: round(value, 4) for key, value in timings.items()},
                    "waveform_construction_seconds": 0.0,
                    "synthesis_seconds": round(synthesis_seconds, 4),
                    "file_encoding_and_disk_write_seconds": round(disk_write_seconds, 4),
                    "http_response_seconds": None,
                    "frontend_decode_seconds": None,
                    "first_playable_audio_seconds": round(first_playable_seconds, 4),
                    "first_audio_after_model_load_seconds": round(synthesis_seconds + disk_write_seconds, 4),
                    "generated_audio_duration_seconds": round(generated_duration, 4),
                    "longest_item_duration_seconds": round(max_duration, 4),
                    "real_time_factor": round(synthesis_seconds / max(generated_duration, 0.001), 4),
                    "per_item_equivalent_rtf": round(synthesis_seconds / max(max_duration, 0.001), 4),
                    "aggregate_rtf": round(synthesis_seconds / max(generated_duration, 0.001), 4),
                    "peak_rss_bytes": monitor.peak_rss,
                    "peak_cpu_percent_of_one_core": round(monitor.peak_cpu_percent, 2),
                    "monitor_samples": monitor.samples,
                    "audio_checks": checks,
                    "audio_valid": all(item["audio_valid"] for item in checks),
                    "output_paths": output_paths,
                    "total_wall_seconds": round(time.perf_counter() - process_started, 4),
                    "text_chars_per_item": len(text),
                    "sample_rate": sample_rate,
                }
            )
        atomic_json(result_path, result)
        print(json.dumps(result), flush=True)
        return 0
    except Exception as error:
        result.update(
            {
                "status": "unsupported" if args.mode in {"compile", "dynamic_int8", "torchao_int8"} or args.dtype != "float32" else "failed",
                "error_type": type(error).__name__,
                "failure_reason": str(error),
                "traceback": traceback.format_exc(limit=20),
            }
        )
        atomic_json(result_path, result)
        print(json.dumps(result), flush=True)
        return 0 if result["status"] == "unsupported" else 1
    finally:
        if model is not None:
            del model
        gc.collect()


def run_worker(output: Path, extra: list[str], timeout: int = 600) -> dict[str, Any]:
    command = [sys.executable, str(Path(__file__).resolve()), "worker", "--result", str(output), *extra]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if output.exists():
        value = json.loads(output.read_text(encoding="utf-8"))
    else:
        value = {
            "status": "failed",
            "failure_reason": f"Worker exited {completed.returncode} without a result file.",
        }
    value["worker_exit_code"] = completed.returncode
    if completed.stderr.strip():
        value["worker_stderr_tail"] = completed.stderr.strip()[-4000:]
    return value


def write_combined_results(records: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    complete = [record for record in records if record.get("status") == "complete" and record.get("audio_valid")]
    grouped: dict[str, list[float]] = {}
    for record in complete:
        key = "|".join(
            str(record.get(field))
            for field in ("mode", "dtype", "threads", "interop_threads", "batch_size", "affinity", "priority")
        )
        grouped.setdefault(key, []).append(float(record["aggregate_rtf"]))
    summaries = [
        {
            "configuration": key,
            "trials": len(values),
            "median_aggregate_rtf": round(statistics.median(values), 4),
            "worst_aggregate_rtf": round(max(values), 4),
            "best_aggregate_rtf": round(min(values), 4),
        }
        for key, values in grouped.items()
    ]
    summaries.sort(key=lambda item: item["median_aggregate_rtf"])
    payload = {"metadata": metadata, "summaries": summaries, "records": records}
    atomic_json(JSON_RESULTS, payload)

    fields = sorted({key for record in records for key, value in record.items() if not isinstance(value, (dict, list))})
    with CSV_RESULTS.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def matrix(args: argparse.Namespace) -> int:
    records: list[dict[str, Any]] = []
    started = time.time()
    for threads in (1, 2, 4, 6, 8, 10, 12, 16):
        for interop in (1, 2, 4):
            for trial in range(1, args.trials + 1):
                name = f"cpu_t{threads}_i{interop}_trial{trial}.json"
                record = run_worker(
                    RESULTS_DIR / name,
                    [
                        "--threads", str(threads),
                        "--interop-threads", str(interop),
                        "--trial", str(trial),
                        "--target-seconds", str(args.target_seconds),
                        "--max-new-tokens", str(args.max_new_tokens),
                        "--affinity", args.affinity,
                    ],
                )
                records.append(record)
                print(json.dumps({"completed": name, "status": record.get("status"), "rtf": record.get("aggregate_rtf")}), flush=True)
                write_combined_results(records, {"kind": "cpu_matrix_partial", "started_at": started})
    write_combined_results(
        records,
        {
            "kind": "cpu_matrix",
            "started_at": started,
            "completed_at": time.time(),
            "trials_per_configuration": args.trials,
            "synthetic_target_seconds": args.target_seconds,
        },
    )
    return 0


def variants(args: argparse.Namespace) -> int:
    variants_to_run = [
        ("float32_eager", ["--dtype", "float32"]),
        ("bfloat16_eager", ["--dtype", "bfloat16"]),
        ("float16_eager", ["--dtype", "float16"]),
        ("torch_compile", ["--mode", "compile"]),
        ("dynamic_int8", ["--mode", "dynamic_int8"]),
        ("greedy", ["--no-do-sample"]),
        ("sdpa", ["--attention", "sdpa"]),
        ("static_cache", ["--cache-implementation", "static"]),
        ("simulated_streaming_input", ["--no-non-streaming-mode"]),
        ("physical_affinity", ["--affinity", "physical"]),
        ("below_normal", ["--priority", "below_normal"]),
    ]
    records = []
    for name, extra in variants_to_run:
        record = run_worker(
            RESULTS_DIR / f"variant_{name}.json",
            [
                "--threads", str(args.threads),
                "--interop-threads", str(args.interop_threads),
                "--target-seconds", str(args.target_seconds),
                *extra,
            ],
            timeout=900,
        )
        records.append(record)
        print(json.dumps({"completed": name, "status": record.get("status"), "rtf": record.get("aggregate_rtf"), "reason": record.get("failure_reason")}), flush=True)
    atomic_json(RESULTS_DIR / "qwen_variant_results.json", records)
    return 0


def batches(args: argparse.Namespace) -> int:
    records = []
    for batch_size in (1, 2, 3, 4):
        for trial in range(1, args.trials + 1):
            record = run_worker(
                RESULTS_DIR / f"batch_{batch_size}_trial{trial}.json",
                [
                    "--threads", str(args.threads),
                    "--interop-threads", str(args.interop_threads),
                    "--target-seconds", str(args.target_seconds),
                    "--batch-size", str(batch_size),
                    "--trial", str(trial),
                ],
                timeout=900,
            )
            records.append(record)
            print(json.dumps({"batch": batch_size, "trial": trial, "status": record.get("status"), "rtf": record.get("aggregate_rtf")}), flush=True)
    atomic_json(RESULTS_DIR / "qwen_batch_results.json", records)
    return 0


def gemma_loaded_in_lm_studio() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:1234/api/v1/models", timeout=3) as response:
            payload = json.loads(response.read())
        for model in payload.get("models", []):
            if model.get("key") == "gemma4-26b-a4b-uncensored-hauhaucs-balanced":
                return bool(model.get("loaded_instances"))
    except Exception:
        return True
    return False


def parallel_workers(args: argparse.Namespace) -> int:
    if gemma_loaded_in_lm_studio():
        result = {
            "status": "refused",
            "reason": "Gemma is loaded. Multi-worker Qwen tests are allowed only while the writer is explicitly unloaded.",
        }
        atomic_json(RESULTS_DIR / "qwen_parallel_results.json", result)
        print(json.dumps(result, indent=2))
        return 2
    configurations = []
    for process_count in (1, 2, 3):
        workers: list[tuple[subprocess.Popen[str], Path]] = []
        started = time.perf_counter()
        for index in range(process_count):
            output = RESULTS_DIR / f"parallel_{process_count}_worker{index}.json"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "worker",
                "--result",
                str(output),
                "--threads",
                str(args.threads),
                "--interop-threads",
                str(args.interop_threads),
                "--process-count",
                str(process_count),
                "--target-seconds",
                str(args.target_seconds),
                "--no-gemma-loaded",
                "--trial",
                str(index + 1),
            ]
            workers.append(
                (
                    subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
                    output,
                )
            )
        records = []
        for process, output in workers:
            stdout, stderr = process.communicate(timeout=900)
            if output.exists():
                record = json.loads(output.read_text(encoding="utf-8"))
            else:
                record = {"status": "failed", "failure_reason": stderr[-2000:] or stdout[-2000:]}
            record["worker_exit_code"] = process.returncode
            records.append(record)
        wall = time.perf_counter() - started
        complete = [record for record in records if record.get("status") == "complete" and record.get("audio_valid")]
        total_audio = sum(float(record.get("generated_audio_duration_seconds") or 0) for record in complete)
        configuration = {
            "process_count": process_count,
            "status": "complete" if len(complete) == process_count else "failed",
            "wall_seconds": round(wall, 4),
            "total_audio_seconds": round(total_audio, 4),
            "aggregate_rtf": round(wall / max(total_audio, 0.001), 4),
            "sum_peak_rss_bytes": sum(int(record.get("peak_rss_bytes") or 0) for record in records),
            "worker_rtfs": [record.get("aggregate_rtf") for record in records],
            "audio_valid": len(complete) == process_count,
            "records": records,
        }
        configurations.append(configuration)
        print(json.dumps({key: value for key, value in configuration.items() if key != "records"}), flush=True)
    atomic_json(
        RESULTS_DIR / "qwen_parallel_results.json",
        {"status": "complete", "gemma_loaded": False, "configurations": configurations},
    )
    return 0


def stream_check(args: argparse.Namespace) -> int:
    from qwen_tts import Qwen3TTSModel
    from qwen_tts.inference import qwen3_tts_model as wrapper_module

    wrapper_source = inspect.getsource(wrapper_module.Qwen3TTSModel.generate_custom_voice)
    model = Qwen3TTSModel.from_pretrained(
        str(MODEL_PATH), device_map="cpu", dtype=torch.float32, attn_implementation="eager", local_files_only=True
    )
    started = time.perf_counter()
    value = model.generate_custom_voice(
        text=corpus_for_seconds(args.target_seconds),
        language="English",
        speaker="Serena",
        non_streaming_mode=False,
        max_new_tokens=args.max_new_tokens,
    )
    returned_after = time.perf_counter() - started
    waveform, sample_rate = value[0][0], value[1]
    result = {
        "status": "unsupported",
        "true_packet_streaming": False,
        "return_type": type(value).__name__,
        "is_iterator": hasattr(value, "__next__"),
        "returned_after_seconds": round(returned_after, 4),
        "returned_audio_duration_seconds": round(len(waveform) / sample_rate, 4),
        "first_packet_seconds": None,
        "first_playable_seconds": round(returned_after, 4),
        "source_declares_simulated_streaming_only": "only simulates streaming text input" in wrapper_source,
        "source_waits_for_model_generate": "self.model.generate" in wrapper_source,
        "source_decodes_after_generate": "speech_tokenizer.decode" in wrapper_source,
        "official_vllm_omni_packet_streaming_exists": True,
        "official_vllm_omni_native_windows_supported": False,
        "official_vllm_omni_requirement": "Linux, Python 3.12, platform-specific CUDA/ROCm/XPU runtime",
        "reason": (
            "The installed official Python wrapper returns a complete (wavs, sample_rate) tuple only after "
            "autoregressive generation and full decode. Current vLLM-Omni has genuine Qwen PCM/SSE/WebSocket "
            "streaming, but its official runtime requires Linux and explicitly does not support native Windows."
        ),
    }
    atomic_json(RESULTS_DIR / "qwen_streaming_packet_result.json", result)
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reproducible local Qwen3-TTS acceleration benchmark.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument("--result", required=True)
    worker_parser.add_argument("--threads", type=int, default=4)
    worker_parser.add_argument("--interop-threads", type=int, default=2)
    worker_parser.add_argument("--process-count", type=int, default=1)
    worker_parser.add_argument("--batch-size", type=int, default=1)
    worker_parser.add_argument("--target-seconds", type=int, default=10)
    worker_parser.add_argument("--max-new-tokens", type=int, default=384)
    worker_parser.add_argument("--trial", type=int, default=1)
    worker_parser.add_argument("--dtype", choices=("float32", "bfloat16", "float16"), default="float32")
    worker_parser.add_argument("--mode", choices=("eager", "compile", "dynamic_int8", "torchao_int8"), default="eager")
    worker_parser.add_argument("--attention", choices=("eager", "sdpa"), default="eager")
    worker_parser.add_argument("--cache-implementation", choices=("default", "dynamic", "static"), default="default")
    worker_parser.add_argument("--affinity", choices=("all", "physical", "first8"), default="all")
    worker_parser.add_argument("--priority", choices=("normal", "below_normal"), default="normal")
    worker_parser.add_argument("--gemma-loaded", action=argparse.BooleanOptionalAction, default=True)
    worker_parser.add_argument("--do-sample", action=argparse.BooleanOptionalAction, default=True)
    worker_parser.add_argument("--non-streaming-mode", action=argparse.BooleanOptionalAction, default=True)
    worker_parser.add_argument("--voice", default="Serena")
    worker_parser.set_defaults(handler=worker)

    matrix_parser = subparsers.add_parser("matrix")
    matrix_parser.add_argument("--trials", type=int, default=2)
    matrix_parser.add_argument("--target-seconds", type=int, default=10)
    matrix_parser.add_argument("--max-new-tokens", type=int, default=384)
    matrix_parser.add_argument("--affinity", choices=("all", "physical", "first8"), default="all")
    matrix_parser.set_defaults(handler=matrix)

    variants_parser = subparsers.add_parser("variants")
    variants_parser.add_argument("--threads", type=int, default=4)
    variants_parser.add_argument("--interop-threads", type=int, default=2)
    variants_parser.add_argument("--target-seconds", type=int, default=10)
    variants_parser.set_defaults(handler=variants)

    batch_parser = subparsers.add_parser("batches")
    batch_parser.add_argument("--threads", type=int, default=4)
    batch_parser.add_argument("--interop-threads", type=int, default=2)
    batch_parser.add_argument("--target-seconds", type=int, default=10)
    batch_parser.add_argument("--trials", type=int, default=2)
    batch_parser.set_defaults(handler=batches)

    parallel_parser = subparsers.add_parser("parallel")
    parallel_parser.add_argument("--threads", type=int, default=4)
    parallel_parser.add_argument("--interop-threads", type=int, default=2)
    parallel_parser.add_argument("--target-seconds", type=int, default=10)
    parallel_parser.set_defaults(handler=parallel_workers)

    stream_parser = subparsers.add_parser("stream-check")
    stream_parser.add_argument("--target-seconds", type=int, default=10)
    stream_parser.add_argument("--max-new-tokens", type=int, default=384)
    stream_parser.set_defaults(handler=stream_check)
    return parser


def main() -> int:
    configure_local_environment()
    ensure_corpus()
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
