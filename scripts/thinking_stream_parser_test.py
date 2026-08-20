from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.thinking_stream import (  # noqa: E402
    GenerationAccumulator,
    normalize_native_payload,
    normalize_openai_payload,
)


def apply_openai(payloads: list[dict]) -> GenerationAccumulator:
    accumulator = GenerationAccumulator(route="openai_compatible", streaming=True)
    for payload in payloads:
        for event in normalize_openai_payload(payload):
            accumulator.apply(event)
    return accumulator


def apply_native(payloads: list[dict]) -> GenerationAccumulator:
    accumulator = GenerationAccumulator(route="native_rest", streaming=True)
    for payload in payloads:
        for event in normalize_native_payload(payload):
            accumulator.apply(event)
    return accumulator


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value, label: str) -> None:
    if not value:
        raise AssertionError(label)


def test_openai_reasoning_then_content() -> None:
    acc = apply_openai(
        [
            {"choices": [{"delta": {"reasoning_content": "private plan "}}]},
            {"choices": [{"delta": {"content": "Visible prose."}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
    )
    assert_equal(acc.visible_text, "Visible prose.", "A visible prose")
    assert_true("private plan" in acc.reasoning_text, "A reasoning recorded separately")
    assert_equal(acc.classification(), "ok", "A classification")
    assert_true(not acc.should_run_finalizer(), "A finalizer skipped")


def test_openai_reasoning_then_final_message() -> None:
    acc = apply_openai(
        [
            {"choices": [{"delta": {"reasoning_content": "private plan "}}]},
            {"choices": [{"message": {"role": "assistant", "content": "Final visible prose."}, "finish_reason": "stop"}]},
        ]
    )
    assert_equal(acc.visible_text, "Final visible prose.", "B final message recovered")
    assert_true("private plan" not in acc.visible_text, "B reasoning not saved as prose")
    assert_true(not acc.should_run_finalizer(), "B finalizer skipped")


def test_openai_final_event_without_newline() -> None:
    payload = {"choices": [{"delta": {"content": "Last partial event survives."}, "finish_reason": "stop"}]}
    acc = apply_openai([payload])
    assert_equal(acc.visible_text, "Last partial event survives.", "C final event parsed")
    assert_equal(acc.finish_reason, "stop", "C finish reason")


def test_native_reasoning_message_and_chat_end() -> None:
    acc = apply_native(
        [
            {"type": "chat.start"},
            {"type": "reasoning.start"},
            {"type": "reasoning.delta", "delta": "private plan "},
            {"type": "reasoning.end"},
            {"type": "message.start"},
            {"type": "message.delta", "delta": "Visible "},
            {"type": "message.delta", "delta": "prose."},
            {"type": "message.end"},
            {
                "type": "chat.end",
                "output": [
                    {"type": "reasoning", "content": "private final plan"},
                    {"type": "message", "content": "Final aggregate prose."},
                ],
                "stats": {"reasoning_output_tokens": 4, "total_output_tokens": 9},
            },
        ]
    )
    assert_equal(acc.visible_text, "Visible prose.", "D streamed message wins")
    assert_true("private" in acc.reasoning_text, "D reasoning tracked")
    assert_true(not acc.should_run_finalizer(), "D finalizer skipped")


def test_native_reasoning_only_chat_end() -> None:
    acc = apply_native(
        [
            {"type": "reasoning.start"},
            {"type": "reasoning.delta", "delta": "private plan only"},
            {"type": "reasoning.end"},
            {"type": "chat.end", "output": [{"type": "reasoning", "content": "private end"}]},
        ]
    )
    assert_equal(acc.visible_text, "", "E no visible prose")
    assert_equal(acc.classification(), "reasoning_only_output", "E classification")
    assert_true(acc.should_run_finalizer(), "E finalizer runs")


def test_unterminated_reasoning_marker() -> None:
    acc = apply_native(
        [
            {"type": "reasoning.start"},
            {"type": "reasoning.delta", "delta": "<think> private plan without end"},
            {"type": "chat.end"},
        ]
    )
    assert_equal(acc.visible_text, "", "F no marker recovery")
    assert_equal(acc.classification(), "reasoning_channel_unclosed", "F classification")
    assert_true(acc.should_run_finalizer(), "F finalizer runs once")


def test_output_budget_finish_reason() -> None:
    acc = apply_openai(
        [
            {"choices": [{"delta": {"reasoning_content": "private plan"}}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        ]
    )
    assert_equal(acc.visible_text, "", "G no visible prose")
    assert_equal(acc.classification(), "output_budget_exhausted", "G classification")
    assert_true(acc.should_run_finalizer(), "G finalizer runs")


def test_truly_empty_response() -> None:
    acc = apply_openai([{"choices": [{"delta": {}, "finish_reason": "stop"}]}])
    assert_equal(acc.visible_text, "", "H no visible prose")
    assert_equal(acc.reasoning_text, "", "H no reasoning")
    assert_equal(acc.classification(), "model_returned_truly_empty", "H classification")
    assert_true(not acc.should_run_finalizer(), "H finalizer skipped")


def main() -> int:
    tests = [
        test_openai_reasoning_then_content,
        test_openai_reasoning_then_final_message,
        test_openai_final_event_without_newline,
        test_native_reasoning_message_and_chat_end,
        test_native_reasoning_only_chat_end,
        test_unterminated_reasoning_marker,
        test_output_budget_finish_reason,
        test_truly_empty_response,
    ]
    for test in tests:
        test()
    print(f"thinking_stream_parser_test: OK ({len(tests)} fixtures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
