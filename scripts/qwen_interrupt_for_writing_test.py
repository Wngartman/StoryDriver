from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(r"D:\StoryDriver")
BACKEND = "http://127.0.0.1:8001"
QWEN = "http://127.0.0.1:8891"
RESULT = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results" / "qwen_interrupt_for_writing_result.json"
sys.path.insert(0, str(ROOT / "scripts"))


def request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: Any = None,
    timeout: float = 30,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def generate_scene(session_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    note = (
        "Write a compact grounded opening scene in a modern apartment. Elena hands Priya a blue notebook, "
        "and they decide whether to call Doctor Imani. Keep everyone in the room, avoid time skips, and end "
        "after the decision. Use about 250 words."
    )
    request = urllib.request.Request(
        f"{BACKEND}/sessions/{session_id}/generate-stream",
        data=json.dumps({"director_note": note, "mode": "continue"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    events: list[dict[str, Any]] = []
    scene: dict[str, Any] | None = None
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=900) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = json.loads(line)
            events.append(event)
            if event.get("type") == "scene":
                scene = event.get("scene") or event
            if event.get("type") == "error":
                raise RuntimeError(event.get("detail") or "Writing stream failed.")
    if not scene:
        raise RuntimeError("Writing completed without a scene event.")
    return scene, events, time.perf_counter() - started


def delete_session(session_id: str) -> None:
    result = request_json(BACKEND, f"/sessions/{session_id}?permanent=true", method="DELETE", timeout=60)
    job_id = result.get("job_id") or result.get("id")
    if not job_id:
        return
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = request_json(BACKEND, f"/sessions/delete-jobs/{job_id}", timeout=15)
        if job.get("status") == "completed":
            return
        if job.get("status") in {"failed", "cancelled"}:
            raise RuntimeError(f"Disposable session deletion {job.get('status')}: {job.get('error')}")
        time.sleep(0.5)
    raise TimeoutError("Disposable session deletion timed out.")


def start_qwen() -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    log_dir = ROOT / "tts_engines" / "qwen3_tts" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "interrupt_test_stdout.log").open("ab") as stdout, (
        log_dir / "interrupt_test_stderr.log"
    ).open("ab") as stderr:
        process = subprocess.Popen(
            [
                str(ROOT / "tts_engines" / "qwen3_tts" / "venv" / "Scripts" / "python.exe"),
                "-m",
                "uvicorn",
                "app:app",
                "--app-dir",
                str(ROOT / "tts_engines" / "qwen3_tts" / "service"),
                "--host",
                "127.0.0.1",
                "--port",
                "8891",
            ],
            cwd=str(ROOT / "tts_engines" / "qwen3_tts" / "service"),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Qwen worker exited with code {process.returncode}.")
        try:
            request_json(QWEN, "/health", timeout=2)
            request_json(
                QWEN,
                "/load",
                method="POST",
                payload={"threads": 4, "interop_threads": 4},
                timeout=150,
            )
            return process
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise TimeoutError("Qwen worker did not start within 30 seconds.")


def main() -> int:
    import lmstudio_qwen_swap_test as swap

    initial_model = swap.model_snapshot()
    initial_runtime = swap.llama_runtime() or {}
    initial_instance = swap.loaded_instance(initial_model) or {}
    session_id: str | None = None
    qwen_process: subprocess.Popen[bytes] | None = None
    synthesis_outcome: dict[str, Any] = {}
    try:
        qwen_process = start_qwen()
        long_texts = [
            (
                "At seven fifteen, Elena checked the brass latch while rain crossed the apartment windows. "
                "Priya placed the blue notebook beside the lamp, kept both hands visible, and waited without "
                "softening the question she had carried upstairs."
            )
        ] * 4

        def run_synthesis() -> None:
            try:
                synthesis_outcome["response"] = request_json(
                    QWEN,
                    "/synthesize-batch",
                    method="POST",
                    payload={
                        "requests": [
                            {
                                "text": text,
                                "voice": "Serena",
                                "language": "English",
                                "speed": 1.0,
                                "max_new_tokens": 384,
                                "request_id": f"interrupt-smoke:{index}",
                            }
                            for index, text in enumerate(long_texts)
                        ]
                    },
                    timeout=180,
                )
            except Exception as error:
                synthesis_outcome["error"] = f"{type(error).__name__}: {error}"

        synthesis_thread = threading.Thread(target=run_synthesis, daemon=True)
        synthesis_thread.start()
        deadline = time.monotonic() + 30
        active_before_note = False
        while time.monotonic() < deadline:
            health = request_json(QWEN, "/health", timeout=5)
            if health.get("active_request_id"):
                active_before_note = True
                break
            time.sleep(0.2)
        if not active_before_note:
            raise RuntimeError("Qwen synthesis did not become active before the interruption test.")

        session = request_json(
            BACKEND,
            "/sessions",
            method="POST",
            payload={"title": "DISPOSABLE Qwen Interrupt Writing Test"},
            timeout=20,
        )
        session_id = str(session["id"])
        scene, events, generation_seconds = generate_scene(session_id)
        synthesis_thread.join(timeout=15)
        try:
            qwen_health = {"reachable": True, **request_json(QWEN, "/health", timeout=0.5)}
        except Exception:
            qwen_health = {"reachable": False}
        final_model = swap.model_snapshot()
        final_runtime = swap.llama_runtime() or {}
        final_instance = swap.loaded_instance(final_model) or {}
        scenes = request_json(BACKEND, f"/sessions/{session_id}/scenes", timeout=30)
        text = str(scene.get("generated_text") or "")
        result = {
            "status": "complete",
            "active_qwen_before_note": active_before_note,
            "qwen_reachable_after_writing": bool(qwen_health.get("reachable")),
            "qwen_interrupted_response": synthesis_outcome,
            "scene_count": len(scenes or []),
            "scene_id": scene.get("id"),
            "scene_nonempty": bool(text.strip()),
            "scene_chars": len(text),
            "generation_seconds": round(generation_seconds, 3),
            "stream_delta_events": sum(1 for event in events if event.get("type") == "delta"),
            "model_id_exact": initial_model.get("key") == final_model.get("key"),
            "instance_config_exact": initial_instance.get("config") == final_instance.get("config"),
            "runtime_exact": initial_runtime.get("normalized_arguments") == final_runtime.get("normalized_arguments"),
            "reasoning_exact": initial_model.get("capabilities", {}).get("reasoning")
            == final_model.get("capabilities", {}).get("reasoning"),
            "thinking_enabled": bool(final_model.get("capabilities", {}).get("reasoning")),
        }
        result["passed"] = all(
            [
                result["active_qwen_before_note"],
                not result["qwen_reachable_after_writing"],
                result["scene_count"] == 1,
                result["scene_nonempty"],
                result["model_id_exact"],
                result["instance_config_exact"],
                result["runtime_exact"],
                result["reasoning_exact"],
                result["thinking_enabled"],
            ]
        )
        return_code = 0 if result["passed"] else 1
    except Exception as error:
        result = {"status": "failed", "passed": False, "error": f"{type(error).__name__}: {error}"}
        return_code = 1
    finally:
        try:
            request_json(QWEN, "/shutdown", method="POST", payload={}, timeout=5)
        except Exception:
            pass
        if qwen_process is not None and qwen_process.poll() is None:
            try:
                qwen_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                qwen_process.terminate()
        if session_id:
            try:
                delete_session(session_id)
                result["disposable_session_deleted"] = True
            except Exception as error:
                result["disposable_session_deleted"] = False
                result["cleanup_error"] = str(error)
                return_code = 1
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        pending = RESULT.with_suffix(".json.pending")
        pending.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        pending.replace(RESULT)
        print(json.dumps(result, indent=2))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
