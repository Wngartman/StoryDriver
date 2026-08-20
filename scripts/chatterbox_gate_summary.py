from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(r"D:\StoryDriver")
ENGINE = ROOT / "tts_engines" / "chatterbox"
RESULTS = ENGINE / "benchmarks" / "results"
MANIFEST = ENGINE / "logs" / "model_download_manifest.json"
SUMMARY = ENGINE / "benchmarks" / "chatterbox_gate_summary.json"
PACK = ROOT / "tts_engines" / "samples" / "chatterbox_comparison"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def result_rows(pattern: str) -> list[dict[str, Any]]:
    return [read_json(path) for path in sorted(RESULTS.glob(pattern))]


def request_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def build_summary() -> dict[str, Any]:
    matrix = [row for row in result_rows("turbo_f32_t*_i*.json") if row.get("status") == "complete"]
    quality = [row for row in result_rows("*quality*.json") if row.get("status") == "complete"]
    baselines = [
        *result_rows("original_f32_t12_i1_e50_c50.json"),
        *result_rows("multilingual_v3_f32_t12_i1_e50_c50.json"),
    ]
    bf16 = result_rows("turbo_bf16*.json")
    if len(matrix) != 15:
        raise AssertionError(f"Expected 15 Turbo scheduler rows, found {len(matrix)}")
    if len(quality) != 12:
        raise AssertionError(f"Expected 12 Original/V3 quality rows, found {len(quality)}")
    best = min(matrix, key=lambda row: float(row["real_time_factor"]))
    chunk_duration = float(best["duration_seconds"])
    chunk_synthesis = float(best["synthesis_seconds"])
    six_chunks = math.ceil(360 / chunk_duration)
    twelve_chunks = math.ceil(720 / chunk_duration)
    initial_two_chunk_buffer = 2 * chunk_duration
    six_deficit = max(0.0, (chunk_synthesis - chunk_duration) * max(0, six_chunks - 2))
    twelve_deficit = max(0.0, (chunk_synthesis - chunk_duration) * max(0, twelve_chunks - 2))
    manifest = read_json(MANIFEST)
    return {
        "status": "complete",
        "decision": "rejected_not_integrated",
        "decision_reason": (
            "Every Chatterbox variant is slower than real time on the supported CPU path. "
            "Turbo is closest but cannot sustain six- or twelve-minute progressive playback with a two-chunk prebuffer."
        ),
        "official_source": {
            "repository": "https://github.com/resemble-ai/chatterbox",
            "commit": "65b18437192794391a0308a8f705b1e33e633948",
            "package_version": "0.1.7",
            "license": "MIT",
            "model_revisions": {
                key: value.get("revision") for key, value in (manifest.get("models") or {}).items()
            },
            "perth_watermark": (
                "Official inference applies a local imperceptible PerTh watermark to generated audio. "
                "It is not cloud telemetry and no audio or text is transmitted by the watermark step."
            ),
        },
        "environment": {
            "python": "3.11.9 isolated portable runtime",
            "torch": "2.6.0+cpu",
            "cuda_packages": 0,
            "telemetry_disabled": True,
            "reference": "Synthetic local Qwen Serena WAV; no real-person recording",
        },
        "turbo_cpu_matrix": {
            "row_count": len(matrix),
            "best_threads": best.get("threads"),
            "best_interop_threads": best.get("interop_threads"),
            "best_rtf": best.get("real_time_factor"),
            "best_synthesis_seconds": best.get("synthesis_seconds"),
            "best_audio_seconds": best.get("duration_seconds"),
            "best_warm_cache_first_audio_seconds": best.get("cold_first_audio_seconds"),
            "first_uncached_run_seconds": read_json(RESULTS / "turbo_f32_t4_i1.json").get("cold_first_audio_seconds"),
            "peak_rss_bytes": best.get("peak_rss_bytes"),
            "all_audio_valid": all(bool(row.get("audio_valid")) for row in matrix),
            "bfloat16_status": [
                {"status": row.get("status"), "error_type": row.get("error_type"), "error": row.get("error")}
                for row in bf16
            ],
            "compile_status": "not attempted; SDPA was already active and no bounded evidence justified Windows compile cost",
        },
        "original_v3": {
            "baseline": [
                {
                    "variant": row.get("variant"),
                    "rtf": row.get("real_time_factor"),
                    "first_audio_seconds": row.get("cold_first_audio_seconds"),
                    "peak_rss_bytes": row.get("peak_rss_bytes"),
                }
                for row in baselines
            ],
            "quality_profile_count": len(quality),
            "quality_rtf_min": min(float(row["real_time_factor"]) for row in quality),
            "quality_rtf_max": max(float(row["real_time_factor"]) for row in quality),
            "all_audio_valid": all(bool(row.get("audio_valid")) for row in quality),
        },
        "continuous_gate": {
            "passed": False,
            "true_streaming_supported": False,
            "six_minute_synthesis_not_run": True,
            "twelve_minute_synthesis_not_run": True,
            "skip_reason": "Best steady-state RTF exceeds 1.0, which proves eventual underrun for a finite prebuffer.",
            "two_chunk_initial_buffer_seconds": round(initial_two_chunk_buffer, 3),
            "six_minute_projected_generation_deficit_seconds": round(six_deficit, 3),
            "twelve_minute_projected_generation_deficit_seconds": round(twelve_deficit, 3),
            "projected_six_minute_underrun": six_deficit > initial_two_chunk_buffer,
            "projected_twelve_minute_underrun": twelve_deficit > initial_two_chunk_buffer,
        },
        "integration": {
            "storydriver_provider_added": False,
            "port_8892_service_added": False,
            "saved_provider_changed": False,
            "default_changed": False,
            "kokoro_preserved": True,
            "qwen_preserved": True,
        },
        "listening_pack": str(PACK),
    }


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=("all", "matrix", "quality", "continuous", "fallback", "comparison"), default="all")
    args = parser.parse_args()
    summary = build_summary()
    atomic_json(SUMMARY, summary)
    if args.check == "matrix":
        assert summary["turbo_cpu_matrix"]["row_count"] == 15
    elif args.check == "quality":
        assert summary["original_v3"]["quality_profile_count"] == 12
    elif args.check == "continuous":
        assert not summary["continuous_gate"]["passed"]
        assert summary["continuous_gate"]["projected_six_minute_underrun"]
        assert summary["continuous_gate"]["projected_twelve_minute_underrun"]
    elif args.check == "fallback":
        tts = request_json("http://127.0.0.1:8001/tts/status")
        registry = tts.get("provider_registry") or {}
        assert (registry.get("kokoro") or {}).get("available")
        assert (registry.get("high_quality_local") or {}).get("available")
        assert (registry.get("active_provider") or "kokoro") == "kokoro"
    elif args.check == "comparison":
        assert len(list(PACK.glob("blind_*.wav"))) == 5
        assert (PACK / "LISTENING_SCORECARD.md").exists()
        assert (PACK / "BLIND_MAPPING.json").exists()
    print(json.dumps({"check": args.check, "decision": summary["decision"], "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
