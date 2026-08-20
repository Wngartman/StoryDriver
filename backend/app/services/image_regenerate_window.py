from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Awaitable, Callable


CleanupCallback = Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]

_WINDOW_LOCK = asyncio.Lock()
_CLEANUP_LOCK = asyncio.Lock()
_WINDOW_TASK: asyncio.Task | None = None
_WINDOW_STATE: dict[str, Any] = {
    "active": False,
    "cleanup_running": False,
    "last_cleanup": None,
}
_WINDOW_CONTEXT: dict[str, Any] | None = None
_WINDOW_CLEANUP: CleanupCallback | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_state() -> dict[str, Any]:
    state = dict(_WINDOW_STATE)
    state.pop("started_monotonic", None)
    return state


def image_regenerate_window_status() -> dict[str, Any]:
    return _public_state()


def image_regenerate_window_active() -> bool:
    return bool(_WINDOW_STATE.get("active"))


async def cancel_image_regenerate_window(reason: str = "cancelled") -> dict[str, Any]:
    global _WINDOW_TASK, _WINDOW_CONTEXT, _WINDOW_CLEANUP
    async with _WINDOW_LOCK:
        if _WINDOW_TASK and not _WINDOW_TASK.done():
            _WINDOW_TASK.cancel()
        _WINDOW_TASK = None
        _WINDOW_CONTEXT = None
        _WINDOW_CLEANUP = None
        if _WINDOW_STATE.get("active") or _WINDOW_STATE.get("cleanup_running"):
            _WINDOW_STATE.update(
                {
                    "active": False,
                    "cleanup_running": False,
                    "ended_at": utc_now(),
                    "end_reason": reason,
                    "message": "Fast regenerate window cancelled.",
                }
            )
    return _public_state()


async def begin_image_regenerate_window(
    *,
    delay_seconds: float,
    context: dict[str, Any],
    cleanup: CleanupCallback,
) -> dict[str, Any]:
    global _WINDOW_TASK, _WINDOW_CONTEXT, _WINDOW_CLEANUP
    await cancel_image_regenerate_window("replaced_by_new_image_window")
    if delay_seconds <= 0:
        return _public_state()

    started = perf_counter()
    async with _WINDOW_LOCK:
        last_cleanup = _WINDOW_STATE.get("last_cleanup")
        _WINDOW_CONTEXT = context
        _WINDOW_CLEANUP = cleanup
        _WINDOW_STATE.clear()
        _WINDOW_STATE.update(
            {
                "active": True,
                "cleanup_running": False,
                "started_at": utc_now(),
                "started_monotonic": started,
                "expires_at": None,
                "delay_seconds": round(delay_seconds, 3),
                "session_id": context.get("session_id"),
                "scene_id": context.get("scene_id"),
                "version_id": context.get("version_id"),
                "image_id": context.get("image_id"),
                "workflow_id": context.get("workflow_id"),
                "workflow_name": context.get("workflow_name"),
                "resource_mode": context.get("resource_mode"),
                "free_after_window": bool(context.get("free_after_window")),
                "reload_lm_after_window": bool(context.get("reload_lm_after_window")),
                "reload_policy": context.get("lm_reload_policy"),
                "message": "ComfyUI warm for quick regenerate.",
                "last_cleanup": last_cleanup,
            }
        )

        async def runner() -> None:
            try:
                await asyncio.sleep(delay_seconds)
                await finish_image_regenerate_window("idle_timeout")
            except asyncio.CancelledError:
                return

        loop = asyncio.get_running_loop()
        _WINDOW_TASK = loop.create_task(runner())

        def consume_exception(completed: asyncio.Task) -> None:
            try:
                completed.exception()
            except asyncio.CancelledError:
                return

        _WINDOW_TASK.add_done_callback(consume_exception)
    state = _public_state()
    return state


async def finish_image_regenerate_window(reason: str = "manual") -> dict[str, Any]:
    global _WINDOW_TASK, _WINDOW_CONTEXT, _WINDOW_CLEANUP
    async with _CLEANUP_LOCK:
        async with _WINDOW_LOCK:
            if not _WINDOW_STATE.get("active") or _WINDOW_STATE.get("cleanup_running"):
                return _public_state()
            current_task = asyncio.current_task()
            if _WINDOW_TASK and _WINDOW_TASK is not current_task and not _WINDOW_TASK.done():
                _WINDOW_TASK.cancel()
            context = dict(_WINDOW_CONTEXT or {})
            cleanup = _WINDOW_CLEANUP
            _WINDOW_STATE.update(
                {
                    "active": False,
                    "cleanup_running": True,
                    "cleanup_started_at": utc_now(),
                    "cleanup_reason": reason,
                    "message": "Freeing ComfyUI memory and preparing LM Studio.",
                }
            )

        started = perf_counter()
        result: dict[str, Any] = {"attempted": False, "success": False, "reason": reason}
        if cleanup:
            try:
                result = await cleanup(context, reason)
                result.setdefault("attempted", True)
                result.setdefault("success", True)
            except Exception as error:  # pragma: no cover - defensive background guard
                result = {"attempted": True, "success": False, "reason": reason, "error": str(error)}
        else:
            result = {"attempted": False, "success": False, "reason": reason, "error": "No cleanup handler registered."}

        result["duration_seconds"] = round(perf_counter() - started, 3)
        async with _WINDOW_LOCK:
            _WINDOW_TASK = None
            _WINDOW_CONTEXT = None
            _WINDOW_CLEANUP = None
            _WINDOW_STATE.update(
                {
                    "active": False,
                    "cleanup_running": False,
                    "cleanup_completed_at": utc_now(),
                    "ended_at": utc_now(),
                    "end_reason": reason,
                    "message": "Image system ready.",
                    "last_cleanup": result,
                }
            )
    return _public_state()
