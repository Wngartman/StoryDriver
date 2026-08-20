from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class GenerationConflictError(RuntimeError):
    pass


_state_lock = asyncio.Lock()
_state = {
    "story_generation_active": False,
    "image_generation_active": False,
    "image_generation_blocks_story": False,
    "image_resource_mode": None,
}


def _mode_label(mode: str | None) -> str:
    return (mode or "balanced").replace("_", " ")


async def _begin_story_generation() -> None:
    async with _state_lock:
        if _state["story_generation_active"]:
            raise GenerationConflictError("Another scene is already being written.")
        if _state["image_generation_active"] and _state["image_generation_blocks_story"]:
            raise GenerationConflictError(
                f"Image generation is active in {_mode_label(_state['image_resource_mode'])} mode. Wait for it to finish before writing another scene."
            )
        _state["story_generation_active"] = True


async def _finish_story_generation() -> None:
    async with _state_lock:
        _state["story_generation_active"] = False


async def _begin_image_generation(mode: str, *, blocks_story: bool) -> None:
    async with _state_lock:
        if _state["image_generation_active"]:
            raise GenerationConflictError("An image generation is already running.")
        if _state["story_generation_active"]:
            raise GenerationConflictError("A scene is being written. Wait for it to finish before generating an image.")
        _state["image_generation_active"] = True
        _state["image_generation_blocks_story"] = blocks_story
        _state["image_resource_mode"] = mode


async def _finish_image_generation() -> None:
    async with _state_lock:
        _state["image_generation_active"] = False
        _state["image_generation_blocks_story"] = False
        _state["image_resource_mode"] = None


@asynccontextmanager
async def story_generation_job() -> AsyncIterator[None]:
    await _begin_story_generation()
    try:
        yield
    finally:
        await _finish_story_generation()


@asynccontextmanager
async def image_generation_job(mode: str, *, blocks_story: bool) -> AsyncIterator[None]:
    await _begin_image_generation(mode, blocks_story=blocks_story)
    try:
        yield
    finally:
        await _finish_image_generation()


def current_generation_state() -> dict[str, bool | str | None]:
    return dict(_state)
