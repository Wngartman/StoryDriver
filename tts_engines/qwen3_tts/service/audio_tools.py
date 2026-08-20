from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


TARGET_SAMPLE_RATE = 24_000
NORMALIZATION_VERSION = "qwen-reference-v1"
BREATH_NORMALIZATION_VERSION = "storydriver-breath-v1"


def _frame_rms(audio: np.ndarray, frame_length: int = 2048, hop_length: int = 512) -> np.ndarray:
    if audio.size < frame_length:
        padded = np.pad(audio, (0, frame_length - audio.size))
        return np.array([float(np.sqrt(np.mean(np.square(padded))))], dtype=np.float32)
    frames = librosa.util.frame(audio, frame_length=frame_length, hop_length=hop_length)
    return np.sqrt(np.mean(np.square(frames), axis=0)).astype(np.float32)


def normalize_reference(source: Path, destination: Path, transcript: str) -> dict:
    if not transcript.strip():
        raise ValueError("An exact reference transcript is required.")
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if not audio.size or sample_rate <= 0:
        raise ValueError("The uploaded file contains no decodable audio.")
    original_channels = int(audio.shape[1])
    waveform = np.mean(audio, axis=1, dtype=np.float32)
    if not np.isfinite(waveform).all():
        raise ValueError("The uploaded audio contains invalid samples.")
    original_peak = float(np.max(np.abs(waveform)))
    clipping_ratio = float(np.mean(np.abs(waveform) >= 0.999))
    if sample_rate != TARGET_SAMPLE_RATE:
        waveform = librosa.resample(
            waveform,
            orig_sr=int(sample_rate),
            target_sr=TARGET_SAMPLE_RATE,
            res_type="soxr_hq",
        ).astype(np.float32)
    trimmed, trim_index = librosa.effects.trim(waveform, top_db=45, frame_length=2048, hop_length=512)
    if trimmed.size:
        padding = int(0.12 * TARGET_SAMPLE_RATE)
        start = max(0, int(trim_index[0]) - padding)
        end = min(waveform.size, int(trim_index[1]) + padding)
        waveform = waveform[start:end]
    duration = waveform.size / TARGET_SAMPLE_RATE
    if duration < 3.0:
        raise ValueError("Reference speech must be at least 3 seconds long.")
    if duration > 60.0:
        raise ValueError("Reference speech must be no longer than 60 seconds.")
    rms = float(np.sqrt(np.mean(np.square(waveform))))
    if rms < 0.001:
        raise ValueError("Reference speech is too quiet to use reliably.")
    target_rms = 10 ** (-20 / 20)
    gain = min(4.0, max(0.25, target_rms / rms))
    waveform = waveform * gain
    peak = float(np.max(np.abs(waveform)))
    if peak > 0.98:
        waveform = waveform * (0.98 / peak)
    rms_frames = _frame_rms(waveform)
    speech_threshold = max(0.003, float(np.percentile(rms_frames, 25)) * 1.8)
    speech_ratio = float(np.mean(rms_frames >= speech_threshold))
    final_rms = float(np.sqrt(np.mean(np.square(waveform))))
    noise_floor = float(np.percentile(rms_frames, 10))
    signal_level = float(np.percentile(rms_frames, 90))
    estimated_snr_db = float(20 * np.log10(max(signal_level, 1e-7) / max(noise_floor, 1e-7)))
    words_per_second = len(transcript.split()) / max(duration, 0.001)
    warnings: list[str] = []
    if not 10.0 <= duration <= 20.0:
        warnings.append("A clean 10-20 second reference usually gives the most stable long-form voice.")
    if speech_ratio < 0.6:
        warnings.append("The reference contains substantial silence or very quiet speech.")
    if estimated_snr_db < 12:
        warnings.append("The reference may contain enough background noise to reduce clone quality.")
    if not 1.0 <= words_per_second <= 4.5:
        warnings.append("The transcript length is unusual for the measured speech duration; verify it exactly.")
    if clipping_ratio > 0.002 or original_peak > 1.0:
        warnings.append("The source contains clipped samples; use a cleaner recording if artifacts are audible.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(".pending.wav")
    sf.write(pending, waveform, TARGET_SAMPLE_RATE, subtype="PCM_16", format="WAV")
    pending.replace(destination)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "path": str(destination),
        "checksum": checksum,
        "normalization_version": NORMALIZATION_VERSION,
        "sample_rate": TARGET_SAMPLE_RATE,
        "channels": 1,
        "subtype": "PCM_16",
        "duration_seconds": round(duration, 3),
        "source_sample_rate": int(sample_rate),
        "source_channels": original_channels,
        "source_peak": round(original_peak, 6),
        "clipping_ratio": round(clipping_ratio, 6),
        "speech_ratio": round(speech_ratio, 4),
        "estimated_snr_db": round(estimated_snr_db, 2),
        "normalized_rms": round(final_rms, 6),
        "words_per_second": round(words_per_second, 3),
        "warnings": warnings,
        "validation_status": "ready_with_warnings" if warnings else "ready",
    }


def normalize_breath(source: Path, destination: Path) -> dict:
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if not audio.size or sample_rate <= 0:
        raise ValueError("The uploaded file contains no decodable audio.")
    original_channels = int(audio.shape[1])
    waveform = np.mean(audio, axis=1, dtype=np.float32)
    if not np.isfinite(waveform).all():
        raise ValueError("The uploaded breath contains invalid samples.")
    clipping_ratio = float(np.mean(np.abs(waveform) >= 0.999))
    if sample_rate != TARGET_SAMPLE_RATE:
        waveform = librosa.resample(
            waveform,
            orig_sr=int(sample_rate),
            target_sr=TARGET_SAMPLE_RATE,
            res_type="soxr_hq",
        ).astype(np.float32)
    trimmed, trim_index = librosa.effects.trim(waveform, top_db=50, frame_length=1024, hop_length=256)
    if trimmed.size:
        padding = int(0.04 * TARGET_SAMPLE_RATE)
        start = max(0, int(trim_index[0]) - padding)
        end = min(waveform.size, int(trim_index[1]) + padding)
        waveform = waveform[start:end]
    duration = waveform.size / TARGET_SAMPLE_RATE
    if duration < 0.25:
        raise ValueError("An isolated breath must be at least 0.25 seconds long.")
    if duration > 2.5:
        raise ValueError("An isolated breath must be no longer than 2.5 seconds.")
    rms = float(np.sqrt(np.mean(np.square(waveform))))
    if rms < 0.0005:
        raise ValueError("The breath sample is too quiet to use reliably.")
    # Keep inserted performance elements quieter than speech. Limit gain so
    # room tone is not promoted into an obvious effect.
    target_rms = 10 ** (-26 / 20)
    gain = min(2.0, max(0.4, target_rms / rms))
    waveform = waveform * gain
    peak = float(np.max(np.abs(waveform)))
    if peak > 0.9:
        waveform = waveform * (0.9 / peak)
    fade_samples = min(int(0.02 * TARGET_SAMPLE_RATE), max(1, waveform.size // 8))
    fade = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    waveform[:fade_samples] *= fade
    waveform[-fade_samples:] *= fade[::-1]
    final_peak = float(np.max(np.abs(waveform)))
    final_rms = float(np.sqrt(np.mean(np.square(waveform))))
    warnings: list[str] = []
    if not 0.4 <= duration <= 1.5:
        warnings.append("A clean 0.4-1.5 second isolated breath usually sounds most natural.")
    if clipping_ratio > 0.002:
        warnings.append("The source contains clipped samples; a cleaner recording is recommended.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(".pending.wav")
    sf.write(pending, waveform, TARGET_SAMPLE_RATE, subtype="PCM_16", format="WAV")
    pending.replace(destination)
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "path": str(destination),
        "checksum": checksum,
        "normalization_version": BREATH_NORMALIZATION_VERSION,
        "sample_rate": TARGET_SAMPLE_RATE,
        "channels": 1,
        "subtype": "PCM_16",
        "duration_seconds": round(duration, 3),
        "source_sample_rate": int(sample_rate),
        "source_channels": original_channels,
        "clipping_ratio": round(clipping_ratio, 6),
        "normalized_rms": round(final_rms, 6),
        "normalized_peak": round(final_peak, 6),
        "warnings": warnings,
        "validation_status": "ready_with_warnings" if warnings else "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--transcript")
    parser.add_argument("--breath", action="store_true")
    args = parser.parse_args()
    if args.breath:
        result = normalize_breath(args.source, args.destination)
    else:
        result = normalize_reference(args.source, args.destination, args.transcript or "")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
