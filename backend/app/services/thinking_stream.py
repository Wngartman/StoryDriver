from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import DATA_DIR


THINKING_DEBUG_REQUEST = DATA_DIR / "logs" / "last_thinking_generation_request.json"
THINKING_DEBUG_STREAM = DATA_DIR / "logs" / "last_thinking_generation_stream.ndjson"
THINKING_DEBUG_SUMMARY = DATA_DIR / "logs" / "last_thinking_generation_summary.json"

REASONING_CLOSE_MARKERS = (
    "</think>",
    "<|end_of_thought|>",
    "<end_of_thought>",
    "<|channel|>final",
    "/final",
)


@dataclass
class NormalizedStreamEvent:
    type: str
    text: str = ""
    finish_reason: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    payload_type: str | None = None
    payload: dict[str, Any] | None = None
    error: str | None = None
    response_id: str | None = None


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(_content_to_text(item.get("text") or item.get("content")))
        return "".join(parts)
    if isinstance(value, dict):
        return _content_to_text(value.get("text") or value.get("content"))
    return str(value)


def _payload_text(payload: dict[str, Any], *, reasoning: bool) -> str:
    payload_type = str(payload.get("type") or payload.get("event") or "").lower()
    if reasoning != ("reasoning" in payload_type):
        return ""
    for key in ("content", "text", "delta", "output_text"):
        text = _content_to_text(payload.get(key))
        if text:
            return text
    message = payload.get("message")
    if isinstance(message, dict):
        return _content_to_text(message.get("content") or message.get("text"))
    return ""


def _output_texts(output: Any) -> tuple[str, str]:
    visible_parts: list[str] = []
    reasoning_parts: list[str] = []
    if not isinstance(output, list):
        return "", ""
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or item.get("role") or "").lower()
        text = _content_to_text(item.get("content") or item.get("text") or item.get("delta"))
        if not text:
            continue
        if "reasoning" in item_type:
            reasoning_parts.append(text)
        else:
            visible_parts.append(text)
    return "".join(visible_parts), "".join(reasoning_parts)


def _error_message(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code")
        if message:
            return str(message)
    payload_type = str(payload.get("type") or payload.get("event") or "").lower()
    if "error" in payload_type:
        return str(payload.get("message") or payload.get("detail") or payload)
    return None


def normalize_openai_payload(payload: dict[str, Any]) -> list[NormalizedStreamEvent]:
    events: list[NormalizedStreamEvent] = []
    choices = payload.get("choices")
    if not isinstance(choices, list):
        choices = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        reasoning = _content_to_text(
            delta.get("reasoning_content")
            or delta.get("reasoning")
            or message.get("reasoning_content")
            or message.get("reasoning")
        )
        content = _content_to_text(delta.get("content"))
        if reasoning:
            events.append(NormalizedStreamEvent("reasoning_delta", text=reasoning, payload=payload))
        if content:
            events.append(NormalizedStreamEvent("message_delta", text=content, payload=payload))
        final_content = _content_to_text(message.get("content"))
        if final_content or (message and not delta):
            events.append(
                NormalizedStreamEvent(
                    "final_result",
                    text=final_content,
                    stats=payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
                    payload=payload,
                )
            )
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            events.append(NormalizedStreamEvent("finish_reason", finish_reason=str(finish_reason), payload=payload))
    if isinstance(payload.get("usage"), dict):
        events.append(NormalizedStreamEvent("stats", stats=payload["usage"], payload=payload))
    return events


def normalize_native_payload(payload: dict[str, Any]) -> list[NormalizedStreamEvent]:
    events: list[NormalizedStreamEvent] = []
    payload_type = str(payload.get("type") or payload.get("event") or "").lower()
    error = _error_message(payload)
    if error:
        events.append(NormalizedStreamEvent("error", error=error, payload_type=payload_type, payload=payload))
        return events
    if isinstance(payload.get("stats"), dict):
        events.append(NormalizedStreamEvent("stats", stats=payload["stats"], payload_type=payload_type, payload=payload))
    if payload_type in {"reasoning.end", "reasoning.complete"}:
        events.append(NormalizedStreamEvent("reasoning_complete", payload_type=payload_type, payload=payload))
    if payload_type in {"message.end", "message.complete"}:
        events.append(NormalizedStreamEvent("message_complete", payload_type=payload_type, payload=payload))

    reasoning = _payload_text(payload, reasoning=True)
    visible = _payload_text(payload, reasoning=False)
    if reasoning:
        events.append(NormalizedStreamEvent("reasoning_delta", text=reasoning, payload_type=payload_type, payload=payload))
    if visible and payload_type not in {"chat.start", "prompt_processing.start", "prompt_processing.progress", "prompt_processing.end"}:
        events.append(NormalizedStreamEvent("message_delta", text=visible, payload_type=payload_type, payload=payload))

    output = payload.get("output")
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if output is None and isinstance(result, dict):
        output = result.get("output")
    final_visible, final_reasoning = _output_texts(output)
    if payload_type == "chat.end" or final_visible or final_reasoning:
        stats = {}
        if isinstance(payload.get("stats"), dict):
            stats = payload["stats"]
        elif isinstance(result.get("stats"), dict):
            stats = result["stats"]
        events.append(
            NormalizedStreamEvent(
                "final_result",
                text=final_visible,
                stats=stats,
                payload_type=payload_type,
                payload=payload,
                response_id=str(payload.get("response_id") or result.get("response_id") or "") or None,
            )
        )
        if final_reasoning:
            events.append(NormalizedStreamEvent("reasoning_delta", text=final_reasoning, payload_type=payload_type, payload=payload))
    return events


@dataclass
class GenerationAccumulator:
    route: str
    streaming: bool = True
    reasoning_parts: list[str] = field(default_factory=list)
    message_parts: list[str] = field(default_factory=list)
    final_message_parts: list[str] = field(default_factory=list)
    raw_output_parts: list[str] = field(default_factory=list)
    event_counts: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    message_started: bool = False
    message_ended: bool = False
    reasoning_started: bool = False
    reasoning_ended: bool = False
    final_aggregate_received: bool = False
    stream_error: str | None = None
    parser_errors: int = 0
    response_id: str | None = None

    def apply(self, event: NormalizedStreamEvent) -> None:
        self.event_counts[event.type] = self.event_counts.get(event.type, 0) + 1
        if event.type == "reasoning_delta":
            self.reasoning_started = True
            if event.text:
                self.reasoning_parts.append(event.text)
                self.raw_output_parts.append(event.text)
        elif event.type == "message_delta":
            self.message_started = True
            if event.text:
                self.message_parts.append(event.text)
                self.raw_output_parts.append(event.text)
        elif event.type == "reasoning_complete":
            self.reasoning_ended = True
        elif event.type == "message_complete":
            self.message_ended = True
        elif event.type == "final_result":
            self.final_aggregate_received = True
            if event.text:
                self.final_message_parts.append(event.text)
            if event.stats:
                self.stats.update(event.stats)
            if event.response_id:
                self.response_id = event.response_id
        elif event.type == "finish_reason":
            self.finish_reason = event.finish_reason
        elif event.type == "stats" and event.stats:
            self.stats.update(event.stats)
        elif event.type == "error":
            self.stream_error = event.error or "model_error"

    @property
    def reasoning_text(self) -> str:
        return "".join(self.reasoning_parts)

    @property
    def streamed_message_text(self) -> str:
        return "".join(self.message_parts)

    @property
    def final_message_text(self) -> str:
        return "".join(self.final_message_parts)

    @property
    def visible_text(self) -> str:
        streamed = self.streamed_message_text.strip()
        if streamed:
            return streamed
        final = self.final_message_text.strip()
        if final:
            return final
        recovered = self.visible_text_after_reasoning_close().strip()
        return recovered

    def visible_text_after_reasoning_close(self) -> str:
        reasoning = self.reasoning_text
        lowered = reasoning.lower()
        best_index = -1
        best_marker = ""
        for marker in REASONING_CLOSE_MARKERS:
            index = lowered.rfind(marker.lower())
            if index > best_index:
                best_index = index
                best_marker = marker
        if best_index < 0:
            return ""
        tail = reasoning[best_index + len(best_marker) :].strip()
        if len(tail.split()) < 12:
            return ""
        return tail

    def classification(self) -> str:
        if self.stream_error:
            return "transport_error"
        if self.visible_text:
            return "ok"
        if self.reasoning_started and not self.reasoning_ended and self.route == "native_rest":
            return "reasoning_channel_unclosed"
        if self.finish_reason == "length" and self.reasoning_text:
            return "output_budget_exhausted"
        if self.reasoning_text:
            return "reasoning_only_output"
        if self.final_aggregate_received and not self.final_message_text:
            return "final_message_missing"
        if self.streaming and not self.final_aggregate_received and self.route == "native_rest":
            return "native_final_aggregate_missing"
        return "model_returned_truly_empty"

    def should_run_finalizer(self) -> bool:
        return not self.visible_text and bool(self.reasoning_text) and not self.stream_error

    def summary(self) -> dict[str, Any]:
        reasoning_chars = len(self.reasoning_text)
        visible_chars = len(self.visible_text)
        usage = self.stats or {}
        reasoning_tokens = None
        completion_details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
        if isinstance(completion_details, dict):
            reasoning_tokens = completion_details.get("reasoning_tokens")
        if reasoning_tokens is None and isinstance(usage, dict):
            reasoning_tokens = usage.get("reasoning_output_tokens")
        visible_tokens = None
        if isinstance(usage, dict):
            total_output = usage.get("total_output_tokens") or usage.get("completion_tokens")
            if total_output is not None and reasoning_tokens is not None:
                try:
                    visible_tokens = max(0, int(total_output) - int(reasoning_tokens))
                except (TypeError, ValueError):
                    visible_tokens = None
        return {
            "route": self.route,
            "streaming": self.streaming,
            "event_counts": dict(self.event_counts),
            "message_started": self.message_started,
            "message_ended": self.message_ended,
            "reasoning_started": self.reasoning_started,
            "reasoning_ended": self.reasoning_ended,
            "final_aggregate_received": self.final_aggregate_received,
            "finish_reason": self.finish_reason,
            "stream_error": self.stream_error,
            "parser_errors": self.parser_errors,
            "reasoning_chars": reasoning_chars,
            "visible_message_chars": visible_chars,
            "reasoning_tokens": reasoning_tokens,
            "visible_message_tokens": visible_tokens,
            "response_id": self.response_id,
            "classification": self.classification(),
        }


class StreamDebugCapture:
    def __init__(self, *, route: str, url: str, request_body: dict[str, Any], metadata: dict[str, Any] | None = None) -> None:
        self.enabled = str(os.environ.get("LM_STREAM_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}
        self.route = route
        self.url = url
        self.index = 0
        self.stream_handle = None
        if not self.enabled:
            return
        THINKING_DEBUG_REQUEST.parent.mkdir(parents=True, exist_ok=True)
        THINKING_DEBUG_REQUEST.write_text(
            json.dumps(
                {
                    "route": route,
                    "url": url,
                    "metadata": metadata or {},
                    "body": request_body,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.stream_handle = THINKING_DEBUG_STREAM.open("w", encoding="utf-8")

    def record_raw(self, raw: str, *, payload: dict[str, Any] | None = None, parser_error: str | None = None) -> None:
        if not self.enabled or self.stream_handle is None:
            return
        self.index += 1
        record: dict[str, Any] = {"index": self.index, "raw": raw}
        if payload is not None:
            record["payload"] = payload
        if parser_error:
            record["parser_error"] = parser_error
        self.stream_handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self, accumulator: GenerationAccumulator, *, extra: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        if self.stream_handle is not None:
            self.stream_handle.close()
            self.stream_handle = None
        summary = accumulator.summary()
        if extra:
            summary.update(extra)
        THINKING_DEBUG_SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def clean_finalization_parameters(parameters: dict[str, Any], *, minimum_tokens: int = 2000) -> dict[str, Any]:
    params = dict(parameters)
    try:
        current = int(params.get("max_tokens") or 0)
    except (TypeError, ValueError):
        current = 0
    params["max_tokens"] = max(current, minimum_tokens)
    return params


def build_finalization_messages(*, original_system_prompt: str, original_user_prompt: str) -> tuple[str, str]:
    system = (
        "You are StoryDriver finalization recovery. The private reasoning pass already completed. "
        "Render only the final fiction prose as visible assistant content. Do not reveal or mention reasoning. "
        "Do not plan. Do not add headings, labels, summaries, or commentary. "
        "Honor the original requested length and pacing. If the original request is a Chapter, write a full chapter "
        "with multiple developed beats instead of a compressed summary."
    )
    user = (
        "Preserve the following StoryDriver system/style instructions and original request, but do not reopen a thinking channel. "
        "Output the final requested prose only.\n\n"
        "[ORIGINAL SYSTEM / STYLE CONTRACT]\n"
        f"{original_system_prompt or ''}\n\n"
        "[ORIGINAL REQUEST]\n"
        f"{original_user_prompt or ''}"
    )
    return system, user
