from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

import httpx

from app.config import COMFYUI_BASE_URL


class ComfyUIError(RuntimeError):
    pass


class ComfyUIOfflineError(ComfyUIError):
    pass


class ComfyUITimeoutError(ComfyUIError):
    pass


class ComfyUINoOutputError(ComfyUIError):
    pass


@dataclass
class ComfyUIImage:
    content: bytes
    content_type: str
    filename: str
    node_id: str | None = None


class ComfyUIClient:
    def __init__(self, base_url: str = COMFYUI_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = str(uuid4())

    async def is_reachable(self) -> tuple[bool, str | None]:
        async with httpx.AsyncClient(timeout=4.0) as client:
            for path in ("/system_stats", "/"):
                try:
                    response = await client.get(f"{self.base_url}{path}")
                    if response.status_code < 500:
                        return True, None
                except httpx.HTTPError as error:
                    last_error = str(error)
                else:
                    last_error = f"HTTP {response.status_code}"
        return False, last_error or f"ComfyUI did not respond at {self.base_url}."

    async def queue_prompt(self, workflow: dict[str, Any]) -> str:
        body = {"prompt": workflow, "client_id": self.client_id}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{self.base_url}/prompt", json=body)
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ComfyUIOfflineError(f"ComfyUI is not reachable at {self.base_url}.") from exc
        except httpx.TimeoutException as exc:
            raise ComfyUIOfflineError(f"ComfyUI timed out while queueing the workflow at {self.base_url}.") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:800] if exc.response is not None else ""
            raise ComfyUIError(f"ComfyUI rejected the workflow. HTTP {exc.response.status_code}. {detail}") from exc
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI queue request failed: {exc}") from exc

        data = response.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError("ComfyUI did not return a prompt_id.")
        return str(prompt_id)

    async def history(self, prompt_id: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ComfyUIOfflineError(f"ComfyUI is not reachable at {self.base_url}.") from exc
        except httpx.TimeoutException as exc:
            raise ComfyUIError("ComfyUI timed out while reading generation history.") from exc
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI history request failed: {exc}") from exc
        return response.json()

    async def queue(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self.base_url}/queue")
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ComfyUIOfflineError(f"ComfyUI is not reachable at {self.base_url}.") from exc
        except httpx.TimeoutException as exc:
            raise ComfyUIError("ComfyUI timed out while reading queue status.") from exc
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI queue status request failed: {exc}") from exc
        return response.json()

    @staticmethod
    def _queue_has_prompt(entries: Any, prompt_id: str) -> bool:
        if not isinstance(entries, list):
            return False
        needle = str(prompt_id)
        for entry in entries:
            if isinstance(entry, str) and entry == needle:
                return True
            if isinstance(entry, dict):
                values = entry.values()
            elif isinstance(entry, (list, tuple)):
                values = entry
            else:
                values = [entry]
            if any(str(value) == needle for value in values):
                return True
        return False

    async def prompt_queue_stage(self, prompt_id: str) -> str:
        data = await self.queue()
        running = data.get("queue_running")
        pending = data.get("queue_pending")
        if self._queue_has_prompt(running, prompt_id):
            return "generating"
        if self._queue_has_prompt(pending, prompt_id):
            return "queued"
        return "waiting"

    async def system_stats(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self.base_url}/system_stats")
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ComfyUIOfflineError(f"ComfyUI is not reachable at {self.base_url}.") from exc
        except httpx.TimeoutException as exc:
            raise ComfyUIError("ComfyUI timed out while reading system stats.") from exc
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI system stats request failed: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise ComfyUIError("ComfyUI returned invalid system stats JSON.") from exc
        return data if isinstance(data, dict) else {"data": data}

    async def free_memory(self, *, unload_models: bool = True, free_memory: bool = True) -> dict[str, Any]:
        body = {"unload_models": unload_models, "free_memory": free_memory}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{self.base_url}/free", json=body)
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ComfyUIOfflineError(f"ComfyUI is not reachable at {self.base_url}.") from exc
        except httpx.TimeoutException as exc:
            raise ComfyUIError("ComfyUI timed out while freeing memory.") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else ""
            raise ComfyUIError(f"ComfyUI /free failed. HTTP {exc.response.status_code}. {detail}") from exc
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI free-memory request failed: {exc}") from exc
        if not response.content:
            return {"ok": True}
        try:
            payload = response.json()
        except ValueError:
            return {"ok": True, "raw": response.text[:500]}
        return payload if isinstance(payload, dict) else {"ok": True, "data": payload}

    async def wait_for_history(
        self,
        prompt_id: str,
        timeout_seconds: int = 300,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        last_queue_check = 0.0
        last_stage: str | None = None
        while asyncio.get_event_loop().time() < deadline:
            data = await self.history(prompt_id)
            record = data.get(prompt_id) if isinstance(data, dict) else None
            if record:
                status = record.get("status", {}) if isinstance(record, dict) else {}
                status_text = str(status.get("status_str") or "").lower()
                if status_text in {"error", "failed"}:
                    raise ComfyUIError(f"ComfyUI generation failed: {status}")
                if record.get("outputs"):
                    if progress_callback:
                        progress_callback({"stage": "completed", "percent": 85, "prompt_id": prompt_id})
                    return record
            if progress_callback:
                stage = last_stage or "generating"
                now = asyncio.get_event_loop().time()
                if now - last_queue_check >= 2.0:
                    last_queue_check = now
                    try:
                        stage = await self.prompt_queue_stage(prompt_id)
                    except ComfyUIError:
                        stage = last_stage or "generating"
                    last_stage = stage
                progress_callback({"stage": stage, "percent": None, "prompt_id": prompt_id})
            await asyncio.sleep(1.0)
        raise ComfyUITimeoutError("ComfyUI image generation timed out.")

    def find_image_reference(self, history_record: dict[str, Any], output_node_id: str | None = None) -> tuple[str, dict[str, Any]]:
        outputs = history_record.get("outputs", {})
        if not isinstance(outputs, dict) or not outputs:
            raise ComfyUINoOutputError("ComfyUI completed, but no outputs were returned.")

        candidates: list[tuple[str, dict[str, Any]]] = []
        if output_node_id and output_node_id in outputs:
            candidates.append((output_node_id, outputs[output_node_id]))
        candidates.extend((node_id, output) for node_id, output in outputs.items() if node_id != output_node_id)

        for node_id, output in candidates:
            if not isinstance(output, dict):
                continue
            images = output.get("images") or []
            if isinstance(images, list) and images:
                image = next((item for item in images if isinstance(item, dict)), None)
                if image:
                    return node_id, image
        raise ComfyUINoOutputError("No image output was found in ComfyUI history.")

    async def retrieve_image(self, image: dict[str, Any], node_id: str | None = None) -> ComfyUIImage:
        filename = image.get("filename")
        if not filename:
            raise ComfyUINoOutputError("ComfyUI image output did not include a filename.")
        params = {
            "filename": filename,
            "subfolder": image.get("subfolder", ""),
            "type": image.get("type", "output"),
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{self.base_url}/view", params=params)
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ComfyUIOfflineError(f"ComfyUI is not reachable at {self.base_url}.") from exc
        except httpx.TimeoutException as exc:
            raise ComfyUIError("ComfyUI timed out while serving the generated image.") from exc
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"ComfyUI image retrieval failed: {exc}") from exc

        content_type = response.headers.get("content-type", "image/png").split(";")[0]
        return ComfyUIImage(
            content=response.content,
            content_type=content_type,
            filename=str(filename),
            node_id=node_id,
        )

    async def generate_image(
        self,
        workflow: dict[str, Any],
        *,
        output_node_id: str | None = None,
        timeout_seconds: int = 300,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ComfyUIImage:
        if progress_callback:
            progress_callback({"stage": "queueing", "percent": 20})
        prompt_id = await self.queue_prompt(workflow)
        if progress_callback:
            progress_callback({"stage": "queued", "percent": 25, "prompt_id": prompt_id})
        record = await self.wait_for_history(
            prompt_id,
            timeout_seconds=timeout_seconds,
            progress_callback=progress_callback,
        )
        node_id, image_ref = self.find_image_reference(record, output_node_id=output_node_id or None)
        if progress_callback:
            progress_callback({"stage": "retrieving", "percent": 88, "prompt_id": prompt_id})
        return await self.retrieve_image(image_ref, node_id=node_id)
