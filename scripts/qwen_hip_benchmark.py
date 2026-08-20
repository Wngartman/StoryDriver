from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf
import torch


ROOT = Path(r"D:\StoryDriver")
MODEL = ROOT / "tts_engines" / "qwen3_tts" / "models" / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
RESULT = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results" / "qwen_hip_result.json"
SAMPLE = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results" / "qwen_hip_sample.wav"
TEXT = (
    "At seven fifteen, Elena Mercer checked the brass latch and listened to rain cross the apartment windows. "
    "The kettle clicked off, but neither woman reached for it; the unfinished question remained between them."
)


def atomic_json(value: dict) -> None:
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    pending = RESULT.with_suffix(".json.pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(RESULT)


def main() -> int:
    global RESULT, SAMPLE
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--attention", choices=("eager", "sdpa"), default="eager")
    parser.add_argument("--output", type=Path, default=RESULT)
    parser.add_argument("--sample", type=Path, default=SAMPLE)
    parser.add_argument("--batch-size", type=int, choices=(1, 2, 3, 4), default=1)
    args = parser.parse_args()
    RESULT = args.output
    SAMPLE = args.sample
    result = {
        "status": "starting",
        "backend": "pytorch_rocm_windows",
        "torch_version": torch.__version__,
        "torch_hip": torch.version.hip,
        "cuda_available": torch.cuda.is_available(),
        "tested_at_epoch": time.time(),
        "attention_implementation": args.attention,
        "batch_size": args.batch_size,
    }
    atomic_json(result)
    model = None
    try:
        if not torch.cuda.is_available():
            raise RuntimeError("The official ROCm PyTorch wheel did not expose an AMD CUDA/HIP device.")
        result["device_name"] = torch.cuda.get_device_name(0)
        result["device_capability"] = list(torch.cuda.get_device_capability(0))
        torch.cuda.reset_peak_memory_stats(0)
        from qwen_tts import Qwen3TTSModel

        process = psutil.Process()
        process_started = time.perf_counter()
        load_started = time.perf_counter()
        model = Qwen3TTSModel.from_pretrained(
            str(MODEL),
            device_map="cuda:0",
            dtype=torch.float16,
            attn_implementation=args.attention,
            local_files_only=True,
        )
        result["model_load_seconds"] = round(time.perf_counter() - load_started, 4)
        synth_started = time.perf_counter()
        with torch.inference_mode():
            wavs, sample_rate = model.generate_custom_voice(
                text=[TEXT] * args.batch_size if args.batch_size > 1 else TEXT,
                language=["English"] * args.batch_size if args.batch_size > 1 else "English",
                speaker=["Serena"] * args.batch_size if args.batch_size > 1 else "Serena",
                max_new_tokens=args.max_new_tokens,
            )
        result["synthesis_seconds"] = round(time.perf_counter() - synth_started, 4)
        waveforms = [np.asarray(item, dtype=np.float32) for item in wavs]
        waveform = waveforms[0]
        durations = [len(item) / sample_rate for item in waveforms]
        result["duration_seconds"] = round(durations[0], 4)
        result["generated_audio_duration_seconds"] = round(sum(durations), 4)
        result["aggregate_rtf"] = round(result["synthesis_seconds"] / result["generated_audio_duration_seconds"], 4)
        result["per_item_equivalent_rtf"] = round(result["synthesis_seconds"] / max(durations), 4)
        result["first_playable_audio_seconds"] = round(time.perf_counter() - process_started, 4)
        result["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated(0)
        result["peak_gpu_reserved_bytes"] = torch.cuda.max_memory_reserved(0)
        result["rss_bytes"] = process.memory_info().rss
        result["rms"] = [round(float(np.sqrt(np.mean(np.square(item)))), 6) for item in waveforms]
        result["clipping_ratio"] = [round(float(np.mean(np.abs(item) >= 0.999)), 6) for item in waveforms]
        result["audio_valid"] = all(
            np.isfinite(item).all() and result["rms"][index] > 0.002 and result["clipping_ratio"][index] < 0.01
            for index, item in enumerate(waveforms)
        )
        sf.write(SAMPLE, waveform, sample_rate, subtype="PCM_16")
        result["sample"] = str(SAMPLE)
        result["status"] = "complete" if result["audio_valid"] else "failed"
        result["dedicated_gpu_activity_confirmed"] = result["peak_gpu_allocated_bytes"] > 0
        atomic_json(result)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "complete" else 1
    except Exception as error:
        result.update({"status": "failed", "error_type": type(error).__name__, "failure_reason": str(error)})
        atomic_json(result)
        print(json.dumps(result, indent=2))
        return 1
    finally:
        unload_started = time.perf_counter()
        if model is not None:
            del model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if RESULT.exists():
            final = json.loads(RESULT.read_text(encoding="utf-8"))
            final["unload_seconds"] = round(time.perf_counter() - unload_started, 4)
            atomic_json(final)


if __name__ == "__main__":
    raise SystemExit(main())
