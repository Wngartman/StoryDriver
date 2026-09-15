import asyncio
from contextlib import aclosing
import json
from typing import Any, AsyncIterator

import httpx

from app.config import LM_STUDIO_BASE_URL
from app.services.thinking_stream import (
    GenerationAccumulator,
    StreamDebugCapture,
    build_finalization_messages,
    clean_finalization_parameters,
    normalize_native_payload,
    normalize_openai_payload,
)


class LMStudioError(RuntimeError):
    pass


class LMStudioOfflineError(LMStudioError):
    pass


LAST_NATIVE_CHAT_STATUS: dict[str, Any] = {
    "checked_at": None,
    "reachable": None,
    "last_error": None,
    "reasoning_requested": None,
    "reasoning_accepted": None,
    "fallback_used": False,
    "stats": {},
}


def get_last_native_chat_status() -> dict[str, Any]:
    return dict(LAST_NATIVE_CHAT_STATUS)


def _set_native_status(**patch: Any) -> None:
    from datetime import datetime, timezone

    LAST_NATIVE_CHAT_STATUS.update(
        {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            **patch,
        }
    )


def native_base_url_from_openai_base(base_url: str) -> str:
    clean = (base_url or LM_STUDIO_BASE_URL).rstrip("/")
    if clean.endswith("/api/v1"):
        return clean
    if clean.endswith("/v1"):
        return f"{clean[:-3]}/api/v1"
    return f"{clean}/api/v1"


def openai_body(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    parameters: dict[str, Any],
    stream: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": stream,
    }
    body.update({key: value for key, value in parameters.items() if value is not None and value != ""})
    return body


def native_chat_body(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    parameters: dict[str, Any],
    stream: bool,
    reasoning_mode: str = "auto",
    context_length: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "system_prompt": system_prompt or "",
        "input": user_prompt or "",
        "stream": stream,
    }
    if parameters.get("temperature") is not None:
        body["temperature"] = parameters.get("temperature")
    if parameters.get("top_p") is not None:
        body["top_p"] = parameters.get("top_p")
    if parameters.get("top_k") is not None:
        body["top_k"] = parameters.get("top_k")
    if parameters.get("min_p") is not None:
        body["min_p"] = parameters.get("min_p")
    if parameters.get("repeat_penalty") is not None:
        body["repeat_penalty"] = parameters.get("repeat_penalty")
    if parameters.get("seed") is not None:
        body["seed"] = parameters.get("seed")
    max_tokens = parameters.get("max_tokens")
    if max_tokens is not None:
        body["max_output_tokens"] = max_tokens
    if context_length:
        body["context_length"] = int(context_length)
    if reasoning_mode and reasoning_mode != "auto":
        body["reasoning"] = reasoning_mode
    return {key: value for key, value in body.items() if value is not None and value != ""}


def _http_error_detail(exc: httpx.HTTPStatusError) -> str:
    try:
        return exc.response.text[:800]
    except Exception:
        return ""


def _native_visible_text_from_payload(payload: dict[str, Any]) -> str:
    payload_type = str(payload.get("type") or "").lower()
    if "reasoning" in payload_type:
        return ""
    for key in ("content", "text", "delta", "output_text"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content") or message.get("text")
        if isinstance(content, str) and content:
            return content
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").lower()
            if "reasoning" in item_type:
                continue
            content = item.get("content") or item.get("text")
            if isinstance(content, str) and content:
                parts.append(content)
        return "".join(parts)
    return ""


def _native_reasoning_text_from_payload(payload: dict[str, Any]) -> str:
    payload_type = str(payload.get("type") or "").lower()
    if "reasoning" in payload_type:
        for key in ("content", "text", "delta"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").lower()
            if "reasoning" not in item_type:
                continue
            content = item.get("content") or item.get("text")
            if isinstance(content, str) and content:
                parts.append(content)
        return "".join(parts)
    return ""


def _native_error_message(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code")
        if message:
            return str(message)
    payload_type = str(payload.get("type") or "").lower()
    if "error" in payload_type:
        return str(payload.get("message") or payload.get("detail") or payload)
    return None


def _openai_response_accumulator(payload: dict[str, Any]) -> GenerationAccumulator:
    accumulator = GenerationAccumulator(route="openai_compatible", streaming=False)
    for event in normalize_openai_payload(payload):
        accumulator.apply(event)
    return accumulator


def _empty_generation_message(classification: str) -> str:
    if classification == "reasoning_only_output":
        return "The model finished thinking but did not produce visible prose."
    if classification == "reasoning_channel_unclosed":
        return "The model stayed in its thinking channel and did not produce visible prose."
    if classification == "output_budget_exhausted":
        return "The model exhausted its output budget before writing the scene."
    if classification == "final_message_missing":
        return "LM Studio finished without a visible final message."
    if classification == "parser_dropped_final_event":
        return "StoryDriver could not reconcile the final LM Studio event."
    if classification == "native_final_aggregate_missing":
        return "LM Studio Native Chat ended without a final aggregate message."
    if classification == "transport_error":
        return "LM Studio generation failed during transport."
    return "LM Studio returned an empty scene."


class LMStudioClient:
    def __init__(self, base_url: str = LM_STUDIO_BASE_URL, provider_id: str = "lm_studio") -> None:
        self.base_url = base_url.rstrip("/")
        self.provider_id = provider_id or "lm_studio"
        if self.provider_id == "llama_cpp":
            self.base_url = "http://127.0.0.1:12345/v1"
        self.provider_label = {
            "llama_cpp": "Built-in llama.cpp",
            "openai_compatible": "Local OpenAI-compatible provider",
            "lm_studio": "LM Studio",
        }.get(self.provider_id, "Local model provider")
        self.native_base_url = native_base_url_from_openai_base(self.base_url)

    async def _ensure_local_model(self, model: str) -> None:
        if self.provider_id != "llama_cpp":
            return
        from app.generation.provider_runtime import provider_registry
        from app.settings.store import load_model_settings

        selected = load_model_settings(resolve_active_preset=True)
        options = {
            key: getattr(selected, f"llama_{key}", None)
            for key in ("context_length", "gpu_layers", "threads", "batch_size", "parallel_slots", "flash_attention")
        }
        try:
            await provider_registry.llama.ensure_loaded(model, options)
        except (ValueError, RuntimeError, OSError, TimeoutError) as error:
            raise LMStudioOfflineError(str(error)) from error

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self.base_url}/models")
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise LMStudioOfflineError(
                f"LM Studio server is not reachable. Start LM Studio Server at {self.base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LMStudioOfflineError(
                f"LM Studio server timed out. Check that LM Studio Server is running at {self.base_url}."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LMStudioError(f"LM Studio returned HTTP {exc.response.status_code} while listing models.") from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"Could not reach LM Studio: {exc}") from exc

        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            raise LMStudioError("LM Studio returned an invalid /models response.")
        return data

    async def generate_scene(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
    ) -> str:
        result = await self.generate_scene_openai_compatible(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=timeout,
        )
        return result["text"]

    async def generate_scene_openai_compatible(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
        allow_finalization_recovery: bool = True,
    ) -> dict[str, Any]:
        await self._ensure_local_model(model)
        if self.provider_id == "llama_cpp":
            # Streaming transport lets the owned server observe a planner timeout immediately.
            result = {}
            try:
                async with asyncio.timeout(timeout):
                    async with aclosing(self.stream_scene_events(
                        model=model, system_prompt=system_prompt, user_prompt=user_prompt,
                        parameters=parameters, timeout=timeout,
                    )) as events:
                        async for event in events:
                            if event.get("type") == "final_result":
                                result = event
            except TimeoutError as error:
                raise LMStudioError("The local generation task reached its time limit.") from error
            if not str(result.get("text") or "").strip():
                raise LMStudioError(_empty_generation_message(result.get("classification", "empty_output")))
            return {**result, "backend": "openai_compatible", "requested_backend": "openai_compatible",
                    "fallback_used": False, "warnings": [], "usage": result.get("stats", {}),
                    "empty_classification": result.get("classification", "ok"), "finalization_recovery": None}
        body = openai_body(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            stream=False,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=body)
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise LMStudioOfflineError(
                f"LM Studio server is not reachable. Start LM Studio Server at {self.base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LMStudioError("LM Studio generation timed out. Try a shorter scene or fewer max tokens.") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise LMStudioError(
                f"LM Studio returned HTTP {exc.response.status_code} during generation. {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio generation failed: {exc}") from exc

        payload = response.json()
        accumulator = _openai_response_accumulator(payload)
        if not payload.get("choices"):
            raise LMStudioError("LM Studio returned an invalid generation response.")
        text = accumulator.visible_text.strip()
        finalization_result: dict[str, Any] | None = None
        if not text and allow_finalization_recovery and accumulator.should_run_finalizer():
            finalization_result = await self.generate_finalization_recovery(
                model=model,
                original_system_prompt=system_prompt,
                original_user_prompt=user_prompt,
                parameters=parameters,
                timeout=timeout,
            )
            text = str(finalization_result.get("text") or "").strip()
        if not text:
            raise LMStudioError(_empty_generation_message(accumulator.classification()))
        return {
            "text": text,
            "stats": accumulator.stats,
            "backend": "openai_compatible",
            "requested_backend": "openai_compatible",
            "fallback_used": False,
            "warnings": [],
            "reasoning_chars": len(accumulator.reasoning_text),
            "finish_reason": accumulator.finish_reason,
            "empty_classification": accumulator.classification(),
            "usage": accumulator.stats,
            "finalization_recovery": finalization_result,
        }

    async def generate_finalization_recovery(
        self,
        *,
        model: str,
        original_system_prompt: str,
        original_user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        system_prompt, user_prompt = build_finalization_messages(
            original_system_prompt=original_system_prompt,
            original_user_prompt=original_user_prompt,
        )
        body = openai_body(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=clean_finalization_parameters(parameters),
            stream=False,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", json=body)
                response.raise_for_status()
        except httpx.ConnectError as exc:
            raise LMStudioOfflineError(
                f"LM Studio server is not reachable. Start LM Studio Server at {self.base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LMStudioError("LM Studio finalization timed out. Retry the scene or use a shorter target.") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise LMStudioError(
                f"LM Studio returned HTTP {exc.response.status_code} during finalization. {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio finalization failed: {exc}") from exc
        payload = response.json()
        accumulator = _openai_response_accumulator(payload)
        text = accumulator.visible_text.strip()
        if not text:
            raise LMStudioError(_empty_generation_message(accumulator.classification()))
        return {
            "used": True,
            "backend": "openai_compatible_finalizer",
            "text": text,
            "reasoning_chars": len(accumulator.reasoning_text),
            "finish_reason": accumulator.finish_reason,
            "classification": accumulator.classification(),
            "usage": accumulator.stats,
        }

    async def stream_scene_events(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
    ) -> AsyncIterator[dict[str, str]]:
        await self._ensure_local_model(model)
        body = openai_body(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            stream=True,
        )
        accumulator = GenerationAccumulator(route="openai_compatible", streaming=True)
        debug = StreamDebugCapture(
            route="openai_compatible",
            url=f"{self.base_url}/chat/completions",
            request_body=body,
            metadata={"model": model},
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", json=body) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            debug.record_raw(line)
                            break
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError as exc:
                            accumulator.parser_errors += 1
                            debug.record_raw(line, parser_error=str(exc))
                            continue
                        debug.record_raw(line, payload=payload)
                        for normalized in normalize_openai_payload(payload):
                            accumulator.apply(normalized)
                            if normalized.type == "message_delta":
                                yield {"type": "content", "text": normalized.text}
                            elif normalized.type == "reasoning_delta":
                                yield {"type": "reasoning", "text": normalized.text}
                            elif normalized.type == "stats":
                                yield {"type": "stats", "stats": normalized.stats}
                            elif normalized.type == "finish_reason":
                                yield {"type": "finish", "finish_reason": normalized.finish_reason}
                    debug.close(accumulator)
                    yield {
                        "type": "final_result",
                        "text": accumulator.visible_text,
                        "reasoning_chars": len(accumulator.reasoning_text),
                        "finish_reason": accumulator.finish_reason,
                        "stats": accumulator.stats,
                        "classification": accumulator.classification(),
                    }
        except httpx.ConnectError as exc:
            raise LMStudioOfflineError(
                f"LM Studio server is not reachable. Start LM Studio Server at {self.base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LMStudioError("LM Studio generation timed out. Try a shorter scene or fewer max tokens.") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else ""
            raise LMStudioError(
                f"LM Studio returned HTTP {exc.response.status_code} during generation. {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio generation failed: {exc}") from exc

    async def generate_scene_native_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
        reasoning_mode: str = "auto",
        context_length: int | None = None,
    ) -> dict[str, Any]:
        body = native_chat_body(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            stream=False,
            reasoning_mode=reasoning_mode,
            context_length=context_length,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.native_base_url}/chat", json=body)
                response.raise_for_status()
        except httpx.ConnectError as exc:
            _set_native_status(
                reachable=False,
                last_error=f"LM Studio Native Chat is not reachable at {self.native_base_url}.",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=None,
                fallback_used=False,
            )
            raise LMStudioOfflineError(
                f"LM Studio Native Chat is not reachable. Start LM Studio Server at {self.native_base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            _set_native_status(
                reachable=True,
                last_error="LM Studio Native Chat generation timed out.",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=None,
                fallback_used=False,
            )
            raise LMStudioError("LM Studio Native Chat generation timed out. Try fewer max tokens.") from exc
        except httpx.HTTPStatusError as exc:
            detail = _http_error_detail(exc)
            _set_native_status(
                reachable=True,
                last_error=f"HTTP {exc.response.status_code}: {detail}",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=False if reasoning_mode != "auto" and "reasoning" in detail.lower() else None,
                fallback_used=False,
            )
            raise LMStudioError(
                f"LM Studio Native Chat returned HTTP {exc.response.status_code}. {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            _set_native_status(
                reachable=None,
                last_error=f"LM Studio Native Chat failed: {exc}",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=None,
                fallback_used=False,
            )
            raise LMStudioError(f"LM Studio Native Chat failed: {exc}") from exc

        payload = response.json()
        error_message = _native_error_message(payload)
        if error_message:
            _set_native_status(
                reachable=True,
                last_error=error_message,
                reasoning_requested=reasoning_mode,
                reasoning_accepted=False if reasoning_mode != "auto" and "reasoning" in error_message.lower() else None,
                fallback_used=False,
            )
            raise LMStudioError(f"LM Studio Native Chat failed: {error_message}")
        accumulator = GenerationAccumulator(route="native_rest", streaming=False)
        for normalized in normalize_native_payload(payload):
            accumulator.apply(normalized)
        text = accumulator.visible_text.strip()
        stats = accumulator.stats or (payload.get("stats") if isinstance(payload.get("stats"), dict) else {})
        finalization_result: dict[str, Any] | None = None
        if not text and accumulator.should_run_finalizer():
            finalization_result = await self.generate_finalization_recovery(
                model=model,
                original_system_prompt=system_prompt,
                original_user_prompt=user_prompt,
                parameters=parameters,
                timeout=timeout,
            )
            text = str(finalization_result.get("text") or "").strip()
        _set_native_status(
            reachable=True,
            last_error=None,
            reasoning_requested=reasoning_mode,
            reasoning_accepted=(reasoning_mode != "auto") if reasoning_mode != "auto" else None,
            fallback_used=False,
            stats=stats,
        )
        if not text:
            raise LMStudioError(_empty_generation_message(accumulator.classification()))
        return {
            "text": text,
            "stats": stats,
            "reasoning_chars": len(accumulator.reasoning_text),
            "backend": "native_rest",
            "fallback_used": False,
            "warnings": [],
            "finish_reason": accumulator.finish_reason,
            "empty_classification": accumulator.classification(),
            "finalization_recovery": finalization_result,
        }

    async def stream_scene_events_native_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
        reasoning_mode: str = "auto",
        context_length: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        body = native_chat_body(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            stream=True,
            reasoning_mode=reasoning_mode,
            context_length=context_length,
        )
        stats: dict[str, Any] = {}
        accumulator = GenerationAccumulator(route="native_rest", streaming=True)
        debug = StreamDebugCapture(
            route="native_rest",
            url=f"{self.native_base_url}/chat",
            request_body=body,
            metadata={"model": model, "reasoning_mode": reasoning_mode, "context_length": context_length},
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.native_base_url}/chat", json=body) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        clean = line.strip()
                        if not clean or not clean.startswith("data:"):
                            continue
                        data = clean.removeprefix("data:").strip()
                        if data == "[DONE]":
                            debug.record_raw(clean)
                            break
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError as exc:
                            accumulator.parser_errors += 1
                            debug.record_raw(clean, parser_error=str(exc))
                            continue
                        debug.record_raw(clean, payload=payload)
                        for normalized in normalize_native_payload(payload):
                            accumulator.apply(normalized)
                            if normalized.type == "error":
                                raise LMStudioError(f"LM Studio Native Chat failed: {normalized.error}")
                            if normalized.type == "stats":
                                stats = normalized.stats
                                yield {"type": "stats", "stats": stats}
                            elif normalized.type == "reasoning_delta":
                                yield {"type": "reasoning", "text": normalized.text}
                            elif normalized.type == "message_delta":
                                yield {"type": "content", "text": normalized.text}
                            elif normalized.type == "finish_reason":
                                yield {"type": "finish", "finish_reason": normalized.finish_reason}
                    _set_native_status(
                        reachable=True,
                        last_error=None,
                        reasoning_requested=reasoning_mode,
                        reasoning_accepted=(reasoning_mode != "auto") if reasoning_mode != "auto" else None,
                        fallback_used=False,
                        stats=stats,
                    )
                    debug.close(accumulator)
                    yield {
                        "type": "final_result",
                        "text": accumulator.visible_text,
                        "reasoning_chars": len(accumulator.reasoning_text),
                        "finish_reason": accumulator.finish_reason,
                        "stats": accumulator.stats or stats,
                        "classification": accumulator.classification(),
                        "response_id": accumulator.response_id,
                    }
        except httpx.ConnectError as exc:
            _set_native_status(
                reachable=False,
                last_error=f"LM Studio Native Chat is not reachable at {self.native_base_url}.",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=None,
                fallback_used=False,
            )
            raise LMStudioOfflineError(
                f"LM Studio Native Chat is not reachable. Start LM Studio Server at {self.native_base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            _set_native_status(
                reachable=True,
                last_error="LM Studio Native Chat generation timed out.",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=None,
                fallback_used=False,
            )
            raise LMStudioError("LM Studio Native Chat generation timed out. Try fewer max tokens.") from exc
        except httpx.HTTPStatusError as exc:
            detail = _http_error_detail(exc)
            _set_native_status(
                reachable=True,
                last_error=f"HTTP {exc.response.status_code}: {detail}",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=False if reasoning_mode != "auto" and "reasoning" in detail.lower() else None,
                fallback_used=False,
            )
            raise LMStudioError(
                f"LM Studio Native Chat returned HTTP {exc.response.status_code}. {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            _set_native_status(
                reachable=None,
                last_error=f"LM Studio Native Chat failed: {exc}",
                reasoning_requested=reasoning_mode,
                reasoning_accepted=None,
                fallback_used=False,
            )
            raise LMStudioError(f"LM Studio Native Chat failed: {exc}") from exc

    async def generate_scene_routed(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
        inference_backend: str = "openai_compatible",
        reasoning_mode: str = "auto",
        context_length: int | None = None,
        fallback_to_openai_compatible: bool = True,
    ) -> dict[str, Any]:
        if inference_backend == "native_rest" and self.provider_id == "lm_studio":
            try:
                return await self.generate_scene_native_chat(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    parameters=parameters,
                    timeout=timeout,
                    reasoning_mode=reasoning_mode,
                    context_length=context_length,
                )
            except LMStudioError as exc:
                if not fallback_to_openai_compatible:
                    raise
                warning = f"Native LM Studio Chat failed; fell back to OpenAI-compatible generation. {exc}"
                _set_native_status(fallback_used=True, last_error=str(exc))
                result = await self.generate_scene_openai_compatible(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    parameters=parameters,
                    timeout=timeout,
                )
                result["warnings"] = [warning, *(result.get("warnings") or [])]
                result["requested_backend"] = "native_rest"
                result["fallback_used"] = True
                return {
                    **result,
                    "backend": "openai_compatible",
                }
        result = await self.generate_scene_openai_compatible(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=timeout,
        )
        return {
            **result,
            "backend": "openai_compatible",
            "requested_backend": "openai_compatible",
            "fallback_used": False,
            "warnings": result.get("warnings") or [],
        }

    async def stream_scene_events_routed(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
        inference_backend: str = "openai_compatible",
        reasoning_mode: str = "auto",
        context_length: int | None = None,
        fallback_to_openai_compatible: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        if inference_backend == "native_rest" and self.provider_id == "lm_studio":
            try:
                async for event in self.stream_scene_events_native_chat(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    parameters=parameters,
                    timeout=timeout,
                    reasoning_mode=reasoning_mode,
                    context_length=context_length,
                ):
                    yield event
                return
            except LMStudioError as exc:
                if not fallback_to_openai_compatible:
                    raise
                warning = f"Native LM Studio Chat failed; fell back to OpenAI-compatible generation. {exc}"
                _set_native_status(fallback_used=True, last_error=str(exc))
                yield {"type": "warning", "message": warning, "fallback_used": True}
        async for event in self.stream_scene_events(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=timeout,
        ):
            yield event

    async def stream_finalization_recovery_events(
        self,
        *,
        model: str,
        original_system_prompt: str,
        original_user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
    ) -> AsyncIterator[dict[str, Any]]:
        system_prompt, user_prompt = build_finalization_messages(
            original_system_prompt=original_system_prompt,
            original_user_prompt=original_user_prompt,
        )
        async for event in self.stream_scene_events(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=clean_finalization_parameters(parameters),
            timeout=timeout,
        ):
            yield {**event, "finalization_recovery": True}

    async def stream_scene_routed(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
        inference_backend: str = "openai_compatible",
        reasoning_mode: str = "auto",
        context_length: int | None = None,
        fallback_to_openai_compatible: bool = True,
    ) -> AsyncIterator[str]:
        async for event in self.stream_scene_events_routed(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=timeout,
            inference_backend=inference_backend,
            reasoning_mode=reasoning_mode,
            context_length=context_length,
            fallback_to_openai_compatible=fallback_to_openai_compatible,
        ):
            if event.get("type") == "content":
                yield str(event.get("text", ""))

    async def stream_scene(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        parameters: dict[str, Any],
        timeout: float = 120.0,
    ) -> AsyncIterator[str]:
        async for event in self.stream_scene_events(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            parameters=parameters,
            timeout=timeout,
        ):
            if event.get("type") == "content":
                yield event.get("text", "")


def model_client_for_settings(model_settings: Any) -> LMStudioClient:
    """Build the compatibility client for the selected local provider."""
    provider_id = str(getattr(model_settings, "provider", "lm_studio") or "lm_studio")
    provider_url = str(
        getattr(model_settings, "provider_url", "")
        or getattr(model_settings, "lm_studio_url", "")
        or LM_STUDIO_BASE_URL
    )
    return LMStudioClient(provider_url, provider_id=provider_id)
