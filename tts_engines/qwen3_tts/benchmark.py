from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

import psutil
import soundfile as sf
import torch

from qwen_tts import Qwen3TTSModel


ROOT = Path(r"D:\StoryDriver\tts_engines\qwen3_tts")
DEFAULT_MODEL = ROOT / "models" / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
DEFAULT_OUTPUT = ROOT / "logs" / "benchmark_latest.json"
DEFAULT_SAMPLE_DIR = ROOT / "samples"
DEFAULT_TEXT = (
    "Mara closed the old brass latch and listened as the rain moved softly "
    "across the windows. At seven fifteen, the apartment was finally quiet."
)


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Qwen3-TTS CPU feasibility benchmark.")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--voices", default="Serena")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--interop-threads", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    torch.set_num_threads(max(1, args.threads))
    torch.set_num_interop_threads(max(1, args.interop_threads))
    torch.manual_seed(20260713)

    process = psutil.Process()
    process_started = time.perf_counter()
    report = {
        "status": "starting",
        "model_path": str(args.model.resolve()),
        "device": "cpu",
        "dtype": "float32",
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "threads": torch.get_num_threads(),
        "interop_threads": torch.get_num_interop_threads(),
        "voices_requested": [voice.strip() for voice in args.voices.split(",") if voice.strip()],
        "text_chars": len(args.text),
        "max_new_tokens": args.max_new_tokens,
        "started_at_epoch": time.time(),
        "samples": [],
    }
    write_report(args.output, report)

    try:
        load_started = time.perf_counter()
        model = Qwen3TTSModel.from_pretrained(
            str(args.model),
            device_map="cpu",
            dtype=torch.float32,
            attn_implementation="eager",
            local_files_only=True,
        )
        report["model_load_seconds"] = round(time.perf_counter() - load_started, 3)
        report["supported_speakers"] = model.get_supported_speakers()
        report["supported_languages"] = model.get_supported_languages()
        report["rss_after_load_bytes"] = process.memory_info().rss
        report["status"] = "loaded"
        write_report(args.output, report)
        print(json.dumps({"event": "loaded", "seconds": report["model_load_seconds"], "rss": report["rss_after_load_bytes"]}), flush=True)

        args.sample_dir.mkdir(parents=True, exist_ok=True)
        for voice in report["voices_requested"]:
            synth_started = time.perf_counter()
            wavs, sample_rate = model.generate_custom_voice(
                text=args.text,
                language="English",
                speaker=voice,
                max_new_tokens=args.max_new_tokens,
            )
            synth_seconds = time.perf_counter() - synth_started
            waveform = wavs[0]
            duration_seconds = len(waveform) / sample_rate
            output_path = args.sample_dir / f"qwen_{voice.lower()}_neutral.wav"
            sf.write(output_path, waveform, sample_rate)
            sample = {
                "voice": voice,
                "path": str(output_path),
                "sample_rate": sample_rate,
                "duration_seconds": round(duration_seconds, 3),
                "synthesis_seconds": round(synth_seconds, 3),
                "real_time_factor": round(synth_seconds / max(duration_seconds, 0.001), 3),
                "rss_bytes": process.memory_info().rss,
                "cold_process_to_first_audio_seconds": round(time.perf_counter() - process_started, 3)
                if not report["samples"]
                else None,
            }
            report["samples"].append(sample)
            report["status"] = "generated"
            write_report(args.output, report)
            print(json.dumps({"event": "sample", **sample}), flush=True)

        unload_started = time.perf_counter()
        del model
        gc.collect()
        report["unload_seconds"] = round(time.perf_counter() - unload_started, 3)
        report["rss_after_unload_bytes"] = process.memory_info().rss
        report["total_seconds"] = round(time.perf_counter() - process_started, 3)
        report["status"] = "complete"
        write_report(args.output, report)
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        report["failed_after_seconds"] = round(time.perf_counter() - process_started, 3)
        report["rss_at_failure_bytes"] = process.memory_info().rss
        write_report(args.output, report)
        raise


if __name__ == "__main__":
    sys.exit(main())
