from __future__ import annotations

import argparse
import gc
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from torch import nn


ROOT = Path(r"D:\StoryDriver")
MODEL_PATH = ROOT / "tts_engines" / "qwen3_tts" / "models" / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
EXPERIMENT = ROOT / "tts_engines" / "qwen3_tts_experiments" / "onnx_directml"
RESULT = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results" / "qwen_onnx_directml_result.json"
EXPORT_PATH = EXPERIMENT / "talker_decoder_layer_probe.onnx"


def atomic_json(value: dict) -> None:
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    pending = RESULT.with_suffix(".json.pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(RESULT)


class DecoderLayerProbe(nn.Module):
    def __init__(self, layer: nn.Module, rotary: nn.Module) -> None:
        super().__init__()
        self.layer = layer
        self.rotary = rotary

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        batch, sequence, _ = hidden_states.shape
        positions = torch.arange(sequence, device=hidden_states.device, dtype=torch.long)
        position_ids = positions.view(1, 1, -1).expand(3, batch, -1)
        text_position_ids = position_ids[0]
        cos, sin = self.rotary(hidden_states, position_ids)
        minimum = torch.finfo(hidden_states.dtype).min
        causal = torch.triu(
            torch.full((sequence, sequence), minimum, dtype=hidden_states.dtype, device=hidden_states.device),
            diagonal=1,
        ).view(1, 1, sequence, sequence)
        return self.layer(
            hidden_states,
            attention_mask=causal,
            position_ids=text_position_ids,
            output_attentions=False,
            use_cache=False,
            cache_position=positions,
            position_embeddings=(cos, sin),
        )[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence", type=int, default=8)
    args = parser.parse_args()
    result = {
        "status": "starting",
        "backend": "onnxruntime_directml",
        "onnxruntime_version": ort.__version__,
        "available_providers": ort.get_available_providers(),
        "tested_at_epoch": time.time(),
        "probe_scope": "one real talker decoder layer with real checkpoint weights",
        "complete_backend_scope": False,
    }
    atomic_json(result)
    model = None
    try:
        from qwen_tts import Qwen3TTSModel

        load_started = time.perf_counter()
        model = Qwen3TTSModel.from_pretrained(
            str(MODEL_PATH),
            device_map="cpu",
            dtype=torch.float32,
            attn_implementation="eager",
            local_files_only=True,
        )
        result["source_model_load_seconds"] = round(time.perf_counter() - load_started, 4)
        layer = model.model.talker.model.layers[0]
        rotary = model.model.talker.model.rotary_emb
        probe = DecoderLayerProbe(layer, rotary).eval()
        model = None
        gc.collect()
        torch.manual_seed(20260714)
        hidden = torch.randn(1, args.sequence, 1024, dtype=torch.float32)
        with torch.inference_mode():
            expected = probe(hidden).numpy()
        EXPERIMENT.mkdir(parents=True, exist_ok=True)
        export_started = time.perf_counter()
        torch.onnx.export(
            probe,
            (hidden,),
            str(EXPORT_PATH),
            input_names=["hidden_states"],
            output_names=["hidden_states_out"],
            opset_version=18,
            do_constant_folding=True,
            dynamo=False,
        )
        result["export_seconds"] = round(time.perf_counter() - export_started, 4)
        result["export_bytes"] = EXPORT_PATH.stat().st_size
        if "DmlExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("onnxruntime-directml did not expose DmlExecutionProvider.")
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_mem_pattern = False
        options.enable_profiling = True
        options.profile_file_prefix = str(EXPERIMENT / "ort_profile")
        session_started = time.perf_counter()
        session = ort.InferenceSession(
            str(EXPORT_PATH),
            sess_options=options,
            providers=["DmlExecutionProvider", "CPUExecutionProvider"],
        )
        result["session_load_seconds"] = round(time.perf_counter() - session_started, 4)
        result["session_providers"] = session.get_providers()
        feed = {"hidden_states": hidden.numpy()}
        session.run(None, feed)
        timings = []
        actual = None
        for _ in range(5):
            started = time.perf_counter()
            actual = session.run(None, feed)[0]
            timings.append(time.perf_counter() - started)
        profile_path = Path(session.end_profiling())
        result["median_layer_seconds"] = round(float(np.median(timings)), 6)
        result["worst_layer_seconds"] = round(max(timings), 6)
        result["max_absolute_error"] = round(float(np.max(np.abs(expected - actual))), 6)
        result["numerically_valid"] = bool(np.isfinite(actual).all() and result["max_absolute_error"] < 0.02)
        provider_counts: dict[str, int] = {}
        if profile_path.exists():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            for event in profile:
                provider = (event.get("args") or {}).get("provider")
                if provider:
                    provider_counts[provider] = provider_counts.get(provider, 0) + 1
            profile_path.unlink(missing_ok=True)
        result["profile_node_provider_counts"] = provider_counts
        result["directml_nodes_confirmed"] = any("DML" in key.upper() for key in provider_counts)
        result["status"] = "unsupported_for_complete_qwen"
        result["reason"] = (
            "A static real decoder layer can be tested on DirectML, but the dominant full stage is a Python "
            "autoregressive GenerationMixin loop with nested sub-talker generation, sampling, mutable KV cache, "
            "past_hidden, and stopping state. This probe cannot replace or accelerate complete synthesis without "
            "a new stateful generation runtime."
        )
        atomic_json(result)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        result.update({"status": "unsupported", "error_type": type(error).__name__, "failure_reason": str(error)})
        atomic_json(result)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        model = None
        gc.collect()
        EXPORT_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
