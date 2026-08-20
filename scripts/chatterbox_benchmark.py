from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import soundfile as sf
import torch


ROOT = Path(r"D:\StoryDriver")
ENGINE_ROOT = ROOT / "tts_engines" / "chatterbox"
MODELS_ROOT = ENGINE_ROOT / "models"
RESULTS_ROOT = ENGINE_ROOT / "benchmarks" / "results"
SAMPLES_ROOT = ENGINE_ROOT / "samples"
CORPUS_PATH = ENGINE_ROOT / "benchmarks" / "synthetic_corpus.json"
REFERENCE_PATH = ENGINE_ROOT / "references" / "synthetic_qwen_serena.wav"
MODEL_REPOS = {
    "turbo": "ResembleAI/chatterbox-turbo",
    "original": "ResembleAI/chatterbox",
    "multilingual_v3": "ResembleAI/chatterbox",
}
TURBO_PATTERNS = ["*.safetensors", "*.json", "*.txt", "*.pt", "*.model"]
BASE_PATTERNS = [
    "ve.safetensors",
    "t3_cfg.safetensors",
    "s3gen.safetensors",
    "tokenizer.json",
    "conds.pt",
    "ve.pt",
    "t3_mtl23ls_v3.safetensors",
    "s3gen.pt",
    "grapheme_mtl_merged_expanded_v1.json",
    "Cangjie5_TC.json",
]
DEFAULT_TEXT = (
    "At seven fifteen, Elena Mercer checked the brass latch and listened to rain "
    "cross the apartment windows. The kettle clicked off, but neither woman "
    "reached for it; the unfinished question remained between them."
)


def configure_environment(*, offline: bool) -> None:
    temp = ROOT / "backend" / "data" / "temp"
    cache = ROOT / "tts_engines" / "cache"
    values = {
        "TEMP": temp,
        "TMP": temp,
        "HF_HOME": MODELS_ROOT / "huggingface",
        "HUGGINGFACE_HUB_CACHE": MODELS_ROOT / "huggingface" / "hub",
        "TRANSFORMERS_CACHE": MODELS_ROOT / "huggingface" / "transformers",
        "TORCH_HOME": cache / "torch",
        "XDG_CACHE_HOME": cache / "xdg",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
    }
    if offline:
        values["HF_HUB_OFFLINE"] = "1"
        values["TRANSFORMERS_OFFLINE"] = "1"
    for key, value in values.items():
        os.environ[key] = str(value)
    for path in (temp, MODELS_ROOT, RESULTS_ROOT, SAMPLES_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def directory_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def file_manifest(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in sorted(path.rglob("*")):
        if not item.is_file() or ".cache" in item.parts:
            continue
        entries.append({"path": str(item.relative_to(path)), "bytes": item.stat().st_size})
    return entries


def download_models() -> int:
    configure_environment(offline=False)
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi()
    report: dict[str, Any] = {
        "status": "starting",
        "official_source_repository": "https://github.com/resemble-ai/chatterbox",
        "models": {},
        "telemetry_disabled": True,
    }
    output = ENGINE_ROOT / "logs" / "model_download_manifest.json"
    atomic_json(output, report)
    jobs = (
        ("turbo", "ResembleAI/chatterbox-turbo", MODELS_ROOT / "turbo", TURBO_PATTERNS),
        ("base", "ResembleAI/chatterbox", MODELS_ROOT / "base", BASE_PATTERNS),
    )
    try:
        for name, repo_id, target, patterns in jobs:
            info = api.model_info(repo_id=repo_id)
            revision = str(info.sha)
            started = time.perf_counter()
            resolved = snapshot_download(
                repo_id=repo_id,
                revision=revision,
                local_dir=target,
                allow_patterns=patterns,
            )
            report["models"][name] = {
                "repo_id": repo_id,
                "revision": revision,
                "resolved_path": str(Path(resolved).resolve()),
                "download_seconds": round(time.perf_counter() - started, 3),
                "bytes": directory_bytes(target),
                "files": file_manifest(target),
            }
            atomic_json(output, report)
        report["status"] = "complete"
        atomic_json(output, report)
        print(json.dumps(report, indent=2))
        return 0
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        atomic_json(output, report)
        raise


class ProcessMonitor:
    def __init__(self) -> None:
        self.process = psutil.Process()
        self.stop = threading.Event()
        self.peak_rss = self.process.memory_info().rss
        self.peak_cpu_percent = 0.0
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        self.process.cpu_percent(None)
        while not self.stop.wait(0.2):
            try:
                self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
                self.peak_cpu_percent = max(self.peak_cpu_percent, self.process.cpu_percent(None))
            except psutil.Error:
                return

    def __enter__(self) -> "ProcessMonitor":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=2)


def cast_floating(value: Any, dtype: torch.dtype, seen: set[int] | None = None) -> Any:
    seen = seen or set()
    if torch.is_tensor(value):
        return value.to(dtype=dtype) if value.is_floating_point() else value
    identity = id(value)
    if identity in seen:
        return value
    seen.add(identity)
    if isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = cast_floating(item, dtype, seen)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = cast_floating(item, dtype, seen)
    elif isinstance(value, tuple):
        return tuple(cast_floating(item, dtype, seen) for item in value)
    elif hasattr(value, "__dict__") and value.__class__.__module__.startswith("chatterbox"):
        for key, item in vars(value).items():
            setattr(value, key, cast_floating(item, dtype, seen))
    return value


def load_model(variant: str) -> Any:
    if variant == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        return ChatterboxTurboTTS.from_local(MODELS_ROOT / "turbo", "cpu")
    if variant == "original":
        from chatterbox.tts import ChatterboxTTS

        return ChatterboxTTS.from_local(MODELS_ROOT / "base", "cpu")
    if variant == "multilingual_v3":
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        return ChatterboxMultilingualTTS.from_local(
            MODELS_ROOT / "base", "cpu", t3_model="t3_mtl23ls_v3.safetensors"
        )
    raise ValueError(f"Unknown variant: {variant}")


def audio_metrics(waveform: np.ndarray, sample_rate: int) -> dict[str, Any]:
    audio = np.asarray(waveform, dtype=np.float32).reshape(-1)
    finite = bool(np.isfinite(audio).all())
    duration = len(audio) / max(1, sample_rate)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    silence = float(np.mean(np.abs(audio) < 0.001)) if len(audio) else 1.0
    clipping = float(np.mean(np.abs(audio) >= 0.999)) if len(audio) else 0.0
    return {
        "duration_seconds": round(duration, 4),
        "finite": finite,
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "silence_ratio": round(silence, 6),
        "clipping_ratio": round(clipping, 6),
        "audio_valid": finite and duration > 0.5 and rms > 0.002 and silence < 0.95 and clipping < 0.01,
    }


def benchmark(args: argparse.Namespace) -> int:
    configure_environment(offline=True)
    os.environ["OMP_NUM_THREADS"] = str(args.threads)
    os.environ["MKL_NUM_THREADS"] = str(args.threads)
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(args.interop_threads)
    torch.set_float32_matmul_precision("high")
    torch.manual_seed(args.seed)
    process = psutil.Process()
    result_path = Path(args.result)
    result: dict[str, Any] = {
        "status": "starting",
        "variant": args.variant,
        "repo_id": MODEL_REPOS[args.variant],
        "device": "cpu",
        "dtype": args.dtype,
        "threads": args.threads,
        "interop_threads": args.interop_threads,
        "torch_version": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "reference_path": str(REFERENCE_PATH),
        "reference_sha256": hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest(),
        "text": args.text,
        "text_chars": len(args.text),
        "true_streaming_supported": False,
        "first_audio_definition": "cold process start through complete returned chunk",
        "watermark": "PerTh imperceptible watermark applied locally by official Chatterbox code",
        "telemetry_disabled": True,
        "compile_attempted": False,
        "compile_reason": "Official CPU path already uses SDPA where configured; no bounded evidence justified Windows torch.compile cost.",
        "started_at_epoch": time.time(),
    }
    atomic_json(result_path, result)
    model = None
    process_started = time.perf_counter()
    try:
        with ProcessMonitor() as monitor:
            load_started = time.perf_counter()
            model = load_model(args.variant)
            result["model_load_seconds"] = round(time.perf_counter() - load_started, 4)
            result["rss_after_load_bytes"] = process.memory_info().rss
            result["sample_rate"] = int(model.sr)
            config = getattr(model.t3.tfmr, "config", None)
            result["attention_implementation"] = str(
                getattr(config, "_attn_implementation", None)
                or getattr(config, "attn_implementation", None)
                or "framework_default"
            )

            conditioning_started = time.perf_counter()
            model.prepare_conditionals(str(REFERENCE_PATH), exaggeration=args.exaggeration)
            result["conditioning_seconds"] = round(time.perf_counter() - conditioning_started, 4)

            dtype = {"float32": torch.float32, "bfloat16": torch.bfloat16}[args.dtype]
            if dtype != torch.float32:
                model.t3.to(dtype=dtype)
                model.s3gen.to(dtype=dtype)
                model.ve.to(dtype=dtype)
                model.conds = cast_floating(model.conds, dtype)

            generation_started = time.perf_counter()
            with torch.inference_mode():
                if args.variant == "turbo":
                    wav = model.generate(args.text)
                elif args.variant == "multilingual_v3":
                    wav = model.generate(
                        args.text,
                        language_id="en",
                        exaggeration=args.exaggeration,
                        cfg_weight=args.cfg,
                    )
                else:
                    wav = model.generate(
                        args.text,
                        exaggeration=args.exaggeration,
                        cfg_weight=args.cfg,
                    )
            synthesis_seconds = time.perf_counter() - generation_started
            audio = wav.detach().float().cpu().numpy().reshape(-1)
            metrics = audio_metrics(audio, model.sr)
            sample_path = Path(args.sample)
            sample_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(sample_path, audio, model.sr, subtype="PCM_16")
            result.update(metrics)
            result.update(
                synthesis_seconds=round(synthesis_seconds, 4),
                real_time_factor=round(synthesis_seconds / max(metrics["duration_seconds"], 0.001), 4),
                cold_first_audio_seconds=round(time.perf_counter() - process_started, 4),
                output_path=str(sample_path.resolve()),
                output_bytes=sample_path.stat().st_size,
                output_sha256=hashlib.sha256(sample_path.read_bytes()).hexdigest(),
                rss_after_synthesis_bytes=process.memory_info().rss,
                peak_rss_bytes=monitor.peak_rss,
                peak_cpu_percent=round(monitor.peak_cpu_percent, 2),
                status="complete",
            )
        atomic_json(result_path, result)
        print(json.dumps(result, indent=2))
        return 0 if result["audio_valid"] else 3
    except Exception as error:
        result.update(
            status="failed",
            error_type=type(error).__name__,
            error=str(error),
            failed_after_seconds=round(time.perf_counter() - process_started, 4),
            rss_at_failure_bytes=process.memory_info().rss,
        )
        atomic_json(result_path, result)
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 2
    finally:
        if model is not None:
            del model
        gc.collect()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Offline Chatterbox CPU benchmark harness")
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("download")
    run = sub.add_parser("run")
    run.add_argument("--variant", choices=tuple(MODEL_REPOS), required=True)
    run.add_argument("--dtype", choices=("float32", "bfloat16"), default="float32")
    run.add_argument("--threads", type=int, default=4)
    run.add_argument("--interop-threads", type=int, default=1)
    run.add_argument("--exaggeration", type=float, default=0.5)
    run.add_argument("--cfg", type=float, default=0.5)
    run.add_argument("--seed", type=int, default=20260714)
    run.add_argument("--text", default=DEFAULT_TEXT)
    run.add_argument("--result", required=True)
    run.add_argument("--sample", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    if args.command == "download":
        return download_models()
    return benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
