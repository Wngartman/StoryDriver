from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from app.services.comfyui_client import ComfyUIClient, ComfyUIError
from app.settings.store import image_generation_is_paused


LAST_COMFYUI_IDLE_FREE_EVENT: dict[str, Any] = {
    "attempted": False,
    "success": False,
    "time": None,
    "duration_seconds": None,
    "skipped": True,
    "skip_reason": "not attempted",
    "error": None,
    "result": None,
    "queue": None,
}
_LAST_FREE_MONOTONIC: float | None = None
_FREE_LOCK = asyncio.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def idle_free_status() -> dict[str, Any]:
    return dict(LAST_COMFYUI_IDLE_FREE_EVENT)


def _queue_is_busy(queue: dict[str, Any]) -> bool:
    running = queue.get("queue_running")
    pending = queue.get("queue_pending")
    return bool((isinstance(running, list) and running) or (isinstance(pending, list) and pending))


async def maybe_free_comfyui_before_writing(
    image_settings: Any,
    *,
    reason: str = "before_prose_generation",
) -> dict[str, Any]:
    """Free idle ComfyUI memory before prose only when explicitly enabled.

    ComfyUI stays alive. This helper is deliberately conservative: it skips while
    ComfyUI has queue work, throttles repeated calls, and treats failures as
    warnings rather than blockers for writing.
    """

    global _LAST_FREE_MONOTONIC

    mode = getattr(image_settings, "comfyui_idle_cleanup_mode", "after_image") or "after_image"
    free_before_writing = bool(getattr(image_settings, "free_comfyui_before_writing", False))
    throttle_seconds = int(getattr(image_settings, "comfyui_free_throttle_seconds", 120) or 120)
    enabled = free_before_writing or mode == "idle_delay"
    event: dict[str, Any] = {
        "attempted": False,
        "success": False,
        "time": utc_now(),
        "duration_seconds": None,
        "skipped": True,
        "skip_reason": None,
        "error": None,
        "result": None,
        "mode": mode,
        "reason": reason,
        "queue": None,
    }

    if image_generation_is_paused(image_settings):
        event["skip_reason"] = "image_generation_paused"
        LAST_COMFYUI_IDLE_FREE_EVENT.update(event)
        return event

    if not enabled or mode in {"off", "manual"}:
        event["skip_reason"] = "disabled"
        LAST_COMFYUI_IDLE_FREE_EVENT.update(event)
        return event

    now = perf_counter()
    if _LAST_FREE_MONOTONIC is not None and now - _LAST_FREE_MONOTONIC < throttle_seconds:
        event["skip_reason"] = f"throttled for {throttle_seconds}s"
        LAST_COMFYUI_IDLE_FREE_EVENT.update(event)
        return event

    async with _FREE_LOCK:
        now = perf_counter()
        if _LAST_FREE_MONOTONIC is not None and now - _LAST_FREE_MONOTONIC < throttle_seconds:
            event["skip_reason"] = f"throttled for {throttle_seconds}s"
            LAST_COMFYUI_IDLE_FREE_EVENT.update(event)
            return event

        client = ComfyUIClient(getattr(image_settings, "comfyui_base_url", "http://localhost:8188"))
        try:
            queue = await client.queue()
            event["queue"] = queue
            if _queue_is_busy(queue):
                event["skip_reason"] = "comfyui_queue_busy"
                LAST_COMFYUI_IDLE_FREE_EVENT.update(event)
                return event

            event["attempted"] = True
            event["skipped"] = False
            started = perf_counter()
            result = await client.free_memory(unload_models=True, free_memory=True)
            event["duration_seconds"] = round(perf_counter() - started, 3)
            event["success"] = True
            event["result"] = result
            _LAST_FREE_MONOTONIC = perf_counter()
        except ComfyUIError as error:
            event["attempted"] = bool(event["attempted"])
            event["skipped"] = not event["attempted"]
            event["skip_reason"] = event["skip_reason"] or "error"
            event["duration_seconds"] = (
                round(perf_counter() - started, 3) if "started" in locals() else None
            )
            event["error"] = str(error)
        LAST_COMFYUI_IDLE_FREE_EVENT.update(event)
        return event


def schedule_comfyui_idle_free(
    image_settings: Any,
    *,
    delay_seconds: float,
    reason: str = "after_image_idle_delay",
) -> None:
    """Schedule a delayed ComfyUI /free without stopping the backend.

    The actual free call still goes through maybe_free_comfyui_before_writing so
    queue-busy and throttle safeguards stay in one place.
    """

    mode = getattr(image_settings, "comfyui_idle_cleanup_mode", "after_image") or "after_image"
    if image_generation_is_paused(image_settings):
        return
    if mode != "idle_delay" or delay_seconds < 0:
        return

    async def runner() -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        await maybe_free_comfyui_before_writing(image_settings, reason=reason)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(runner())

    def consume_exception(completed: asyncio.Task) -> None:
        try:
            completed.exception()
        except asyncio.CancelledError:
            return

    task.add_done_callback(consume_exception)
