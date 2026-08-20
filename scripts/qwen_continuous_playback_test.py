from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gc
import hashlib
import importlib.util
import json
import mimetypes
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
import psutil
import soundfile as sf
import torch


ROOT = Path(r"D:\StoryDriver")
MODEL_PATH = ROOT / "tts_engines" / "qwen3_tts" / "models" / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
BASE_MODEL_PATH = ROOT / "tts_engines" / "qwen3_tts" / "models" / "Qwen3-TTS-12Hz-0.6B-Base"
RESULTS = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results"
DEFAULT_CLONE_REFERENCE = ROOT / "tts_engines" / "samples" / "qwen_customvoice_neutral.wav"
DEFAULT_CLONE_TRANSCRIPT = "Elena opened the notebook and read the first careful line to the room."
STYLE_REFERENCES = {
    "normal": (ROOT / "tts_engines" / "samples" / "qwen_customvoice_neutral.wav", DEFAULT_CLONE_TRANSCRIPT),
    "soft": (
        ROOT / "tts_engines" / "samples" / "qwen_customvoice_soft.wav",
        '"Read the next line," Elena said softly.',
    ),
    "whisper": (
        ROOT / "tts_engines" / "samples" / "qwen_customvoice_whisper.wav",
        '"Keep the lantern low," she whispered, and everyone leaned closer.',
    ),
    "heightened": (
        ROOT / "tts_engines" / "samples" / "qwen_customvoice_heightened.wav",
        '"Get away from the door!" Elena shouted.',
    ),
}
STYLE_REFERENCE_FALLBACKS = {
    "normal": "normal",
    "soft": "soft",
    "whisper": "whisper",
    "heightened": "heightened",
    "distressed": "soft",
    "intimate": "soft",
    "somber": "soft",
    "tense": "soft",
}
SENTENCES = (
    "At seven fifteen, Elena Mercer checked the brass latch and listened to rain cross the apartment windows.",
    '"You knew before Tuesday," Priya said.',
    '"Read the next line," Elena said softly.',
    '"Keep the lantern low," Priya whispered.',
    '"Get away from the door!" Elena shouted.',
    '"I am fine," Priya said, though her voice broke.',
    '"Come closer," Elena murmured against her ear.',
    '"There is nothing left," Priya said in a hollow voice.',
    '"Wait," Elena said in a clipped voice.',
    "The dark corridor seemed to narrow around them.",
    '"Stop!" Priya said.',
    'She leaned closer. "I missed you."',
    "Beyond the glass, traffic made a low river of sound over wet pavement.",
    "Aboard Caldera, the coolant alarm repeated twice before Nia muted it.",
    "Marek passed the compass to Rhea and kept the broken wall on his left.",
    "No one rushed the answer; each movement altered what the others would risk.",
)


def load_direction_classifier():
    path = ROOT / "backend" / "app" / "tts" / "prosody.py"
    spec = importlib.util.spec_from_file_location("storydriver_continuous_prosody", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load StoryDriver narration classifier.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.narration_direction


class SharedState:
    def __init__(self, minutes: int, backend: str, output_dir: Path) -> None:
        self.lock = threading.Lock()
        self.start_event = threading.Event()
        self.result_event = threading.Event()
        self.minutes = minutes
        self.backend = backend
        self.output_dir = output_dir
        self.manifest: dict[str, Any] = {
            "status": "waiting_for_browser",
            "minutes": minutes,
            "target_audio_seconds": minutes * 60,
            "initial_buffer_target_seconds": 28,
            "backend": backend,
            "voice_mode": "custom_clone" if output_dir.name.startswith("continuous_custom_") else "built_in_serena",
            "automatic_direction": False,
            "started_at_epoch_ms": None,
            "model_load_seconds": None,
            "chunks": [],
            "generated_audio_seconds": 0.0,
            "generation_complete": False,
            "generation_error": None,
            "qwen_unload_seconds": None,
            "gemma": {},
        }
        self.browser_result: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.manifest))

    def update(self, **values: Any) -> None:
        with self.lock:
            self.manifest.update(values)
            self._write_locked()

    def add_chunk(self, chunk: dict[str, Any]) -> None:
        with self.lock:
            self.manifest["chunks"].append(chunk)
            self.manifest["generated_audio_seconds"] = round(
                sum(float(item["duration_seconds"]) for item in self.manifest["chunks"]), 4
            )
            self._write_locked()

    def set_browser_result(self, value: dict[str, Any]) -> None:
        with self.lock:
            self.browser_result = value
        self.result_event.set()

    def _write_locked(self) -> None:
        path = self.output_dir / "manifest.json"
        pending = path.with_suffix(".json.pending")
        pending.write_text(json.dumps(self.manifest, indent=2, sort_keys=True), encoding="utf-8")
        for attempt in range(20):
            try:
                pending.replace(path)
                return
            except PermissionError:
                if attempt >= 19:
                    raise
                time.sleep(0.01 * (attempt + 1))


def next_chunk_text(index: int, first: bool = False) -> str:
    cursor = index * 3
    return SENTENCES[cursor % len(SENTENCES)]


def audio_quality(waveform: np.ndarray) -> dict[str, Any]:
    audio = np.asarray(waveform, dtype=np.float32)
    return {
        "rms": round(float(np.sqrt(np.mean(np.square(audio)))), 6),
        "peak": round(float(np.max(np.abs(audio))), 6),
        "clipping_ratio": round(float(np.mean(np.abs(audio) >= 0.999)), 6),
        "silence_ratio": round(float(np.mean(np.abs(audio) < 0.001)), 6),
        "finite": bool(np.isfinite(audio).all()),
    }


def compact_generation_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    chunks = list(manifest.get("chunks") or [])
    style_counts = Counter(str(chunk.get("style_classification") or "normal") for chunk in chunks)
    reference_counts = Counter(str(chunk.get("selected_style_reference") or "normal") for chunk in chunks)
    identity_signatures: dict[str, set[tuple[Any, Any, Any]]] = defaultdict(set)
    for chunk in chunks:
        identity_signatures[str(chunk.get("cache_identity") or "")].add(
            (
                chunk.get("text_hash"),
                chunk.get("style_classification"),
                chunk.get("selected_style_reference"),
            )
        )
    qualities = [chunk.get("quality") or {} for chunk in chunks]
    return {
        "status": manifest.get("status"),
        "minutes": manifest.get("minutes"),
        "target_audio_seconds": manifest.get("target_audio_seconds"),
        "backend": manifest.get("backend"),
        "voice_mode": manifest.get("voice_mode"),
        "automatic_direction": bool(manifest.get("automatic_direction")),
        "generation_complete": bool(manifest.get("generation_complete")),
        "generation_error": manifest.get("generation_error"),
        "chunk_count": len(chunks),
        "generated_audio_seconds": manifest.get("generated_audio_seconds"),
        "model_load_seconds": manifest.get("model_load_seconds"),
        "voice_prompt_creation_seconds": manifest.get("voice_prompt_creation_seconds"),
        "generation_seconds": manifest.get("generation_seconds"),
        "generation_aggregate_rtf": manifest.get("generation_aggregate_rtf"),
        "qwen_unload_seconds": manifest.get("qwen_unload_seconds"),
        "peak_rss_bytes": manifest.get("peak_rss_bytes"),
        "peak_gpu_allocated_bytes": manifest.get("peak_gpu_allocated_bytes"),
        "model_path": manifest.get("model_path"),
        "automatic_style_references": manifest.get("automatic_style_references") or {},
        "style_counts": dict(sorted(style_counts.items())),
        "reference_counts": dict(sorted(reference_counts.items())),
        "style_switch_count": sum(
            1
            for previous, current in zip(chunks, chunks[1:])
            if previous.get("style_classification") != current.get("style_classification")
        ),
        "cache_identity_count": len(identity_signatures),
        "cache_identity_conflict_count": sum(1 for values in identity_signatures.values() if len(values) > 1),
        "all_audio_finite": all(bool(item.get("finite")) for item in qualities),
        "max_clipping_ratio": max((float(item.get("clipping_ratio") or 0.0) for item in qualities), default=0.0),
        "gemma": manifest.get("gemma") or {},
    }


def compact_browser_evidence(browser_result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in browser_result.items() if key != "gaps_ms"}


def lm_studio_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    import lmstudio_qwen_swap_test as swap

    model = swap.model_snapshot()
    runtime = swap.llama_runtime()
    return model, runtime or {}


def unload_gemma(state: SharedState) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    import lmstudio_qwen_swap_test as swap

    model, runtime = lm_studio_snapshot()
    instance = swap.loaded_instance(model)
    if not instance:
        raise RuntimeError("Gemma was not loaded at the start of the cold continuous test.")
    started = time.perf_counter()
    response = swap.api("POST", "/models/unload", {"instance_id": instance["id"]})
    swap.wait_for_loaded(False, timeout=60)
    state.update(
        gemma={
            "initial_model_key": model.get("key"),
            "initial_instance_id": instance.get("id"),
            "initial_config": instance.get("config"),
            "initial_runtime": runtime,
            "reasoning_capability_before": model.get("capabilities", {}).get("reasoning"),
            "unload_seconds": round(time.perf_counter() - started, 4),
            "unload_response": response,
        }
    )
    return model, runtime, instance


def reload_gemma(state: SharedState, initial_model: dict[str, Any], initial_runtime: dict[str, Any], instance: dict[str, Any]) -> None:
    import lmstudio_qwen_swap_test as swap

    started = time.perf_counter()
    reload_probe = swap.direct_generation()
    reloaded_model = swap.wait_for_loaded(True, timeout=180)
    runtime = swap.llama_runtime()
    deadline = time.time() + 30
    while runtime is None and time.time() < deadline:
        time.sleep(1)
        runtime = swap.llama_runtime()
    reloaded_instance = swap.loaded_instance(reloaded_model) or {}
    gemma = state.snapshot().get("gemma") or {}
    gemma.update(
        {
            "reload_seconds": round(time.perf_counter() - started, 4),
            "reload_probe_generation": reload_probe,
            "reloaded_instance_id": reloaded_instance.get("id"),
            "reloaded_config": reloaded_instance.get("config"),
            "reloaded_runtime": runtime,
            "config_exact_match": instance.get("config") == reloaded_instance.get("config"),
            "runtime_exact_match": bool(
                runtime
                and initial_runtime.get("normalized_arguments") == runtime.get("normalized_arguments")
            ),
            "reasoning_capability_after": reloaded_model.get("capabilities", {}).get("reasoning"),
            "reasoning_capability_exact_match": (
                initial_model.get("capabilities", {}).get("reasoning")
                == reloaded_model.get("capabilities", {}).get("reasoning")
            ),
        }
    )
    state.update(gemma=gemma)


def generation_worker(state: SharedState, args: argparse.Namespace) -> None:
    state.start_event.wait()
    state.update(status="unloading_gemma", started_at_epoch_ms=round(time.time() * 1000))
    initial_model = initial_runtime = instance = None
    model = None
    try:
        if args.manage_gemma:
            initial_model, initial_runtime, instance = unload_gemma(state)
        else:
            import lmstudio_qwen_swap_test as swap

            initial_model, initial_runtime = lm_studio_snapshot()
            instance = swap.loaded_instance(initial_model)
            state.update(
                gemma={
                    "management": (
                        "kept_loaded_because_unload_did_not_improve_cpu_qwen"
                        if instance
                        else "no_model_loaded_before_test_and_none_was_loaded_by_test"
                    ),
                    "initial_model_key": initial_model.get("key"),
                    "initial_instance_id": instance.get("id") if instance else None,
                    "initial_config": instance.get("config") if instance else None,
                    "initial_runtime": initial_runtime,
                }
            )
        state.update(status="loading_qwen")
        if args.backend == "hip" and not torch.cuda.is_available():
            raise RuntimeError("HIP backend selected, but the isolated PyTorch runtime exposes no AMD GPU.")
        device = "cuda:0" if args.backend == "hip" else "cpu"
        dtype = torch.float16 if args.backend == "hip" else {
            "float32": torch.float32,
            "bfloat16": torch.bfloat16,
        }[args.dtype]
        if device == "cpu":
            torch.set_num_threads(args.threads)
            torch.set_num_interop_threads(args.interop_threads)
        from qwen_tts import Qwen3TTSModel

        process = psutil.Process()
        load_started = time.perf_counter()
        active_model_path = BASE_MODEL_PATH if args.custom_voice else MODEL_PATH
        model = Qwen3TTSModel.from_pretrained(
            str(active_model_path),
            device_map=device,
            dtype=dtype,
            attn_implementation=args.attention,
            local_files_only=True,
        )
        model_load_seconds = time.perf_counter() - load_started
        clone_prompt = None
        style_prompts: dict[str, Any] = {}
        classify_direction = load_direction_classifier() if args.automatic_direction else None
        prompt_started = time.perf_counter()
        if args.custom_voice:
            reference_path = Path(args.clone_reference)
            if not reference_path.exists():
                raise RuntimeError(f"Custom clone reference is missing: {reference_path}")
            if args.automatic_direction:
                for style, (style_reference, transcript) in STYLE_REFERENCES.items():
                    if not style_reference.exists():
                        raise RuntimeError(f"Automatic {style} reference is missing: {style_reference}")
                    style_prompts[style] = model.create_voice_clone_prompt(
                        ref_audio=str(style_reference),
                        ref_text=transcript,
                        x_vector_only_mode=False,
                    )
            else:
                clone_prompt = model.create_voice_clone_prompt(
                    ref_audio=str(reference_path),
                    ref_text=args.clone_transcript,
                    x_vector_only_mode=False,
                )
        state.update(
            status="generating",
            model_load_seconds=round(model_load_seconds, 4),
            voice_prompt_creation_seconds=(
                round(time.perf_counter() - prompt_started, 4) if args.custom_voice else None
            ),
            model_path=str(active_model_path),
            clone_reference=str(args.clone_reference) if args.custom_voice else None,
            automatic_direction=bool(args.automatic_direction),
            automatic_style_references={style: str(value[0]) for style, value in STYLE_REFERENCES.items()} if args.automatic_direction else {},
            device=device,
            dtype=str(dtype),
        )
        target = args.minutes * 60
        chunk_index = 0
        generation_started = time.perf_counter()
        peak_rss = process.memory_info().rss
        while state.snapshot()["generated_audio_seconds"] < target:
            texts = [next_chunk_text(chunk_index + offset) for offset in range(args.batch_size)]
            directions = [classify_direction(text) for text in texts] if classify_direction else []
            selected_references = [
                STYLE_REFERENCE_FALLBACKS.get(str(direction["style"]), "normal") for direction in directions
            ]
            synth_started = time.perf_counter()
            with torch.inference_mode():
                if args.custom_voice:
                    prompts = (
                        [style_prompts[style][0] for style in selected_references]
                        if args.automatic_direction
                        else clone_prompt
                    )
                    wavs, sample_rate = model.generate_voice_clone(
                        text=texts if len(texts) > 1 else texts[0],
                        language=["English"] * len(texts) if len(texts) > 1 else "English",
                        voice_clone_prompt=prompts,
                        max_new_tokens=args.max_new_tokens,
                    )
                else:
                    wavs, sample_rate = model.generate_custom_voice(
                        text=texts if len(texts) > 1 else texts[0],
                        language=["English"] * len(texts) if len(texts) > 1 else "English",
                        speaker=["Serena"] * len(texts) if len(texts) > 1 else "Serena",
                        max_new_tokens=args.max_new_tokens,
                    )
            batch_seconds = time.perf_counter() - synth_started
            batch_audio_seconds = sum(len(item) / sample_rate for item in wavs)
            peak_rss = max(peak_rss, process.memory_info().rss)
            for offset, (text, waveform) in enumerate(zip(texts, wavs)):
                index = chunk_index + offset
                direction = directions[offset] if directions else None
                selected_reference = selected_references[offset] if selected_references else "normal"
                cache_identity = hashlib.sha256(
                    json.dumps(
                        {
                            "provider": "qwen3_tts",
                            "model": str(active_model_path),
                            "custom_voice_id": "synthetic-continuous-fixture",
                            "custom_voice_revision": 1,
                            "selected_style_reference": selected_reference,
                            "style_classification": direction["style"] if direction else "normal",
                            "intensity_bucket": direction["intensity_bucket"] if direction else "low",
                            "generation_instruction": "",
                            "speed": 1.0,
                            "pronunciation_revision": "continuous-test-v1",
                            "normalized_text": text,
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()[:32]
                duration = len(waveform) / sample_rate
                name = f"chunk_{index:03d}.wav"
                output = state.output_dir / name
                pending = output.with_suffix(".pending.wav")
                sf.write(pending, waveform, sample_rate, subtype="PCM_16")
                pending.replace(output)
                state.add_chunk(
                    {
                        "index": index,
                        "url": f"/audio/{name}",
                        "path": str(output),
                        "duration_seconds": round(duration, 4),
                        "available_at_epoch_ms": round(time.time() * 1000),
                        "batch_synthesis_seconds": round(batch_seconds, 4),
                        "batch_size": len(texts),
                        "batch_aggregate_rtf": round(batch_seconds / max(batch_audio_seconds, 0.001), 4),
                        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                        "text_words": len(text.split()),
                        "style_classification": direction["style"] if direction else "normal",
                        "intensity_bucket": direction["intensity_bucket"] if direction else "low",
                        "source_cue": direction["source_cue"] if direction else "",
                        "selected_style_reference": selected_reference,
                        "cache_identity": cache_identity,
                        "quality": audio_quality(waveform),
                    }
                )
                if state.snapshot()["generated_audio_seconds"] >= target:
                    break
            chunk_index += len(texts)
        state.update(
            status="unloading_qwen",
            generation_seconds=round(time.perf_counter() - generation_started, 4),
            generation_aggregate_rtf=round(
                (time.perf_counter() - generation_started) / max(state.snapshot()["generated_audio_seconds"], 0.001), 4
            ),
            peak_rss_bytes=peak_rss,
            peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(0) if torch.cuda.is_available() else 0,
        )
        unload_started = time.perf_counter()
        model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        state.update(qwen_unload_seconds=round(time.perf_counter() - unload_started, 4))
        if args.manage_gemma and initial_model and initial_runtime is not None and instance:
            state.update(status="reloading_gemma")
            reload_gemma(state, initial_model, initial_runtime, instance)
        elif initial_model and initial_runtime is not None and instance:
            current_model, current_runtime = lm_studio_snapshot()
            current_instance = swap.loaded_instance(current_model) or {}
            gemma = state.snapshot().get("gemma") or {}
            gemma.update(
                {
                    "remained_loaded": bool(current_instance),
                    "final_instance_id": current_instance.get("id"),
                    "config_exact_match": instance.get("config") == current_instance.get("config"),
                    "runtime_exact_match": bool(
                        current_runtime
                        and initial_runtime.get("normalized_arguments")
                        == current_runtime.get("normalized_arguments")
                    ),
                    "reasoning_capability_exact_match": (
                        initial_model.get("capabilities", {}).get("reasoning")
                        == current_model.get("capabilities", {}).get("reasoning")
                    ),
                }
            )
            state.update(gemma=gemma)
        state.update(status="generated", generation_complete=True)
    except Exception as error:
        state.update(status="failed", generation_error=f"{type(error).__name__}: {error}", generation_complete=True)
        if args.manage_gemma and initial_model and instance:
            try:
                reload_gemma(state, initial_model, initial_runtime or {}, instance)
            except Exception as reload_error:
                gemma = state.snapshot().get("gemma") or {}
                gemma["emergency_reload_error"] = str(reload_error)
                state.update(gemma=gemma)


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qwen continuous playback acceptance</title>
<style>body{font:16px system-ui;background:#111;color:#eee;max-width:760px;margin:40px auto;padding:20px}button{font:inherit;padding:12px 18px}pre{white-space:pre-wrap;background:#1c1c1c;padding:14px}</style></head>
<body><h1>Qwen continuous playback acceptance</h1><button id="start">Start real-time test</button><pre id="status">Waiting for user gesture.</pre>
<script>
const statusEl=document.querySelector('#status'); const button=document.querySelector('#start');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let running=false; let pendingGestureAction=null;
async function manifest(){return fetch('/manifest',{cache:'no-store'}).then(r=>r.json())}
async function waitForChunk(index){while(true){const m=await manifest();if(m.generation_error)throw new Error(m.generation_error);if(m.chunks[index])return {m,chunk:m.chunks[index]};if(m.generation_complete)throw new Error('Generation completed before requested chunk existed');await sleep(100)}}
async function loadedAudio(chunk){const a=new Audio(chunk.url+'?v='+chunk.available_at_epoch_ms);a.preload='auto';await new Promise((resolve,reject)=>{a.oncanplaythrough=resolve;a.onerror=()=>reject(new Error('audio decode failed'));a.load()});return a}
async function playWithGestureRecovery(audio){try{await audio.play();return false}catch(error){if(error.name!=='NotAllowedError')throw error;statusEl.textContent='Audio is buffered. Select Resume buffered playback to continue.';button.textContent='Resume buffered playback';button.disabled=false;return new Promise((resolve,reject)=>{pendingGestureAction=async()=>{try{await audio.play();resolve(true)}catch(recoveryError){reject(recoveryError)}finally{pendingGestureAction=null;button.disabled=true}}})}}
async function runTest(){let firstAudible=null,lastEnded=null,longestGap=0,underruns=0,played=0,index=0,gestureRecoveries=0,transitions=[],sixMinuteMilestone=null;try{
 await fetch('/start',{method:'POST'}); statusEl.textContent='Cold load and initial buffer in progress...';
 while(true){const m=await manifest();if(m.generation_error)throw new Error(m.generation_error);const buffered=m.chunks.reduce((s,c)=>s+c.duration_seconds,0);const target=m.initial_buffer_target_seconds||28;statusEl.textContent=`Generated ${buffered.toFixed(1)} seconds. Waiting for ${target}-second initial buffer.`;if(buffered>=target||m.generation_complete)break;await sleep(100)}
 let currentInfo=await waitForChunk(0);let current=await loadedAudio(currentInfo.chunk);let nextPromise=null;
 while(true){const mBefore=await manifest();if(mBefore.chunks[index+1])nextPromise=loadedAudio(mBefore.chunks[index+1]);
   const recovered=await playWithGestureRecovery(current);if(recovered)gestureRecoveries++;await new Promise((resolve,reject)=>{if(!firstAudible)firstAudible=Date.now();const gap=lastEnded?Date.now()-lastEnded:0;if(lastEnded){transitions.push(gap);longestGap=Math.max(longestGap,gap);if(gap>300)underruns++}statusEl.textContent=`Playing chunk ${index+1}; ${played.toFixed(1)} seconds heard.`;current.onended=resolve;current.onerror=()=>reject(new Error('playback error'));});
   lastEnded=Date.now();played+=currentInfo.chunk.duration_seconds;index++;if(!sixMinuteMilestone&&played>=360){sixMinuteMilestone={played_audio_seconds:played,transition_count:transitions.length,longest_gap_ms:longestGap,underrun_count:underruns}}
   const mAfter=await manifest();if(mAfter.generation_complete&&index>=mAfter.chunks.length)break;
   const waitStarted=Date.now();currentInfo=await waitForChunk(index);if(!nextPromise)nextPromise=loadedAudio(currentInfo.chunk);current=await nextPromise;nextPromise=null;
 }
 const finalManifest=await manifest();const result={status:'complete',browser_user_agent:navigator.userAgent,first_audible_seconds:(firstAudible-finalManifest.started_at_epoch_ms)/1000,user_gesture_recovery_count:gestureRecoveries,played_audio_seconds:played,transition_count:transitions.length,longest_gap_ms:longestGap,underrun_count:underruns,six_minute_milestone:sixMinuteMilestone,gaps_ms:transitions,generation_complete:finalManifest.generation_complete,generated_audio_seconds:finalManifest.generated_audio_seconds,completed_at_epoch_ms:Date.now()};
 await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(result)});statusEl.textContent=JSON.stringify(result,null,2);
 }catch(error){const result={status:'failed',error:String(error),completed_at_epoch_ms:Date.now()};await fetch('/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(result)});statusEl.textContent=JSON.stringify(result,null,2)}}
button.onclick=async()=>{if(pendingGestureAction){await pendingGestureAction();return}if(running)return;running=true;button.disabled=true;runTest()};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    state: SharedState

    def send_bytes(self, value: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(value)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/manifest":
            self.send_bytes(json.dumps(self.state.snapshot()).encode("utf-8"), "application/json")
            return
        if path == "/result":
            value = self.state.browser_result or {"status": "pending"}
            self.send_bytes(json.dumps(value).encode("utf-8"), "application/json")
            return
        if path.startswith("/audio/"):
            name = Path(path).name
            candidate = self.state.output_dir / name
            if candidate.exists() and candidate.suffix.lower() == ".wav":
                self.send_bytes(candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "audio/wav")
                return
        self.send_bytes(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        if path == "/start":
            self.state.start_event.set()
            self.send_bytes(b'{"ok":true}', "application/json")
            return
        if path == "/result":
            self.state.set_browser_result(json.loads(body))
            self.send_bytes(b'{"ok":true}', "application/json")
            return
        self.send_bytes(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

    def log_message(self, _: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, choices=(6, 12), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--backend", choices=("cpu", "hip"), required=True)
    parser.add_argument("--dtype", choices=("float32", "bfloat16"), default="bfloat16")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--interop-threads", type=int, default=2)
    parser.add_argument("--attention", choices=("eager", "sdpa"), default="eager")
    parser.add_argument("--batch-size", type=int, choices=(1, 2, 3, 4), default=1)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--manage-gemma", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--custom-voice", action="store_true")
    parser.add_argument("--automatic-direction", action="store_true")
    parser.add_argument("--clone-reference", default=str(DEFAULT_CLONE_REFERENCE))
    parser.add_argument("--clone-transcript", default=DEFAULT_CLONE_TRANSCRIPT)
    args = parser.parse_args()
    if args.automatic_direction and not args.custom_voice:
        parser.error("--automatic-direction requires --custom-voice")
    output_dir = RESULTS / f"continuous_{'custom_' if args.custom_voice else ''}{args.minutes}m"
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("chunk_*.wav"):
        old.unlink()
    state = SharedState(args.minutes, args.backend, output_dir)
    state.update(status="waiting_for_browser")
    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    generation_thread = threading.Thread(target=generation_worker, args=(state, args), daemon=True)
    server_thread.start()
    generation_thread.start()
    print(json.dumps({"event": "server_ready", "url": f"http://127.0.0.1:{args.port}", "minutes": args.minutes}), flush=True)
    if not state.result_event.wait(timeout=10800):
        state.set_browser_result({"status": "failed", "error": "Browser result timed out."})
    generation_thread.join(timeout=600)
    server.shutdown()
    manifest = state.snapshot()
    browser_result = state.browser_result or {"status": "failed", "error": "No browser result."}
    combined = {
        "status": "complete" if browser_result.get("status") == "complete" and not manifest.get("generation_error") else "failed",
        "backend": args.backend,
        "minutes": args.minutes,
        "browser": compact_browser_evidence(browser_result),
        "generation": compact_generation_evidence(manifest),
    }
    result_path = RESULTS / f"qwen_{'custom_' if args.custom_voice else ''}{args.minutes}_minute_continuous_result.json"
    pending = result_path.with_suffix(".json.pending")
    pending.write_text(json.dumps(combined, indent=2, sort_keys=True), encoding="utf-8")
    pending.replace(result_path)
    print(json.dumps(combined, indent=2), flush=True)
    return 0 if combined["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
