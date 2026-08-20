from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.config import DATA_DIR, KOKORO_BASE_URL, QWEN_TTS_BASE_URL
from app.diagnostics.runtime import diagnostic_logging_enabled


ProviderId = str

PROVIDER_HIGH_QUALITY_LOCAL = "high_quality_local"
PROVIDER_KOKORO = "kokoro"
PROVIDER_BROWSER = "browser"
SUPPORTED_PROVIDER_IDS = (PROVIDER_HIGH_QUALITY_LOCAL, PROVIDER_KOKORO, PROVIDER_BROWSER)

BENCHMARK_REPORT_PATH = Path(r"D:\StoryDriver\tts_engines\reports\TTS_PROVIDER_BENCHMARK_REPORT.md")
QWEN_MODEL_PATH = Path(r"D:\StoryDriver\tts_engines\qwen3_tts\models\Qwen3-TTS-12Hz-0.6B-CustomVoice")
QWEN_BENCHMARK_PATH = Path(r"D:\StoryDriver\tts_engines\qwen3_tts\logs\benchmark_female_voices.json")
HIGH_QUALITY_ACCEPTANCE = (
    "Qwen3-TTS 0.6B CustomVoice passed six-minute continuous browser playback with CPU "
    "bfloat16, SDPA, and ordered four-sentence batches. Kokoro remains the immediate fallback."
)

TTS_PROVIDER_INTERFACE_METHODS = (
    "health",
    "list_voices",
    "list_profiles",
    "synthesize_chunk",
    "stream_or_generate",
    "duration_metadata",
    "cancel",
    "unload",
    "diagnostics",
)


class TTSProvider(Protocol):
    provider_id: ProviderId

    async def health(self) -> dict[str, Any]:
        ...

    async def list_voices(self) -> list[dict[str, Any]]:
        ...

    async def list_profiles(self) -> list[dict[str, Any]]:
        ...

    async def synthesize_chunk(self, payload: Any) -> Any:
        ...

    async def stream_or_generate(self, payload: Any) -> Any:
        ...

    async def cancel(self, job_id: str | None = None) -> dict[str, Any]:
        ...

    async def unload(self) -> dict[str, Any]:
        ...

    def diagnostics(self) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class ProviderCapability:
    provider_id: ProviderId
    display_name: str
    role: str
    available: bool
    enabled: bool
    fallback_provider: str | None
    quality_tier: str
    supports_streaming: bool
    supports_chunk_cache: bool
    supports_resume: bool
    supports_pronunciation_aliases: bool
    supports_expression_controls: bool
    supports_cancel: bool
    supports_unload: bool
    unavailable_reason: str = ""
    benchmark_decision: str = ""
    diagnostics: dict[str, Any] | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.provider_id,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "role": self.role,
            "available": self.available,
            "enabled": self.enabled,
            "fallback_provider": self.fallback_provider,
            "quality_tier": self.quality_tier,
            "supports_streaming": self.supports_streaming,
            "supports_chunk_cache": self.supports_chunk_cache,
            "supports_resume": self.supports_resume,
            "supports_pronunciation_aliases": self.supports_pronunciation_aliases,
            "supports_expression_controls": self.supports_expression_controls,
            "supports_cancel": self.supports_cancel,
            "supports_unload": self.supports_unload,
            "unavailable_reason": self.unavailable_reason,
            "benchmark_decision": self.benchmark_decision,
            "diagnostics": self.diagnostics or {},
        }


def high_quality_local_is_eligible() -> bool:
    return QWEN_MODEL_PATH.exists()


def premium_female_narrator_profile() -> dict[str, Any]:
    return {
        "id": "premium_female_narrator",
        "display_name": "Premium Female Narrator",
        "provider": PROVIDER_HIGH_QUALITY_LOCAL,
        "voice_id": "Serena",
        "model": "Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "speed": 1.0,
        "style_notes": "Warm local Serena narration with sentence-batched progressive generation.",
        "recommended_use": "Quality-first scene and chapter narration when a 30-45 second initial preparation is acceptable.",
        "default": False,
        "enabled": True,
        "available": QWEN_MODEL_PATH.exists(),
        "fallback_provider": PROVIDER_KOKORO,
        "quality_mode": "premium",
        "chunking_profile": "natural",
        "narration_pacing": "natural",
        "dialogue_pause_strength": "medium",
        "paragraph_pause_strength": "medium",
        "dialogue_narration_style": "neutral",
        "voice_preferences": [],
        "pronunciation_profile": "global_story_aliases",
        "authorized_reference_metadata": {
            "required_for_voice_cloning": False,
            "supplied": False,
            "policy": "Use only user-owned or explicitly authorized female reference audio.",
        },
        "unavailable_reason": "" if QWEN_MODEL_PATH.exists() else f"Local Qwen model is missing: {QWEN_MODEL_PATH}",
        "benchmark_report": str(BENCHMARK_REPORT_PATH),
    }


def provider_capabilities(
    kokoro: dict[str, Any] | None = None,
    qwen: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    kokoro = kokoro or {}
    qwen = qwen or {}
    kokoro_reachable = bool(kokoro.get("reachable"))
    capabilities = [
        ProviderCapability(
            provider_id=PROVIDER_HIGH_QUALITY_LOCAL,
            display_name="Qwen3-TTS 0.6B",
            role="premium_on_demand",
            available=QWEN_MODEL_PATH.exists(),
            enabled=high_quality_local_is_eligible(),
            fallback_provider=PROVIDER_KOKORO,
            quality_tier="premium_cpu",
            supports_streaming=False,
            supports_chunk_cache=True,
            supports_resume=True,
            supports_pronunciation_aliases=True,
            supports_expression_controls=True,
            supports_cancel=True,
            supports_unload=True,
            unavailable_reason="" if QWEN_MODEL_PATH.exists() else f"Local Qwen model is missing: {QWEN_MODEL_PATH}",
            benchmark_decision="Passed the six-minute continuous gate; twelve-minute validation is recorded in the acceleration report.",
            diagnostics={
                "benchmark_report": str(BENCHMARK_REPORT_PATH),
                "eligible": high_quality_local_is_eligible(),
                "engine": "Qwen3-TTS 0.6B CustomVoice",
                "model_path": str(QWEN_MODEL_PATH),
                "benchmark_path": str(QWEN_BENCHMARK_PATH),
                "base_url": QWEN_TTS_BASE_URL,
                "reachable": bool(qwen.get("reachable")),
                "loaded": bool(qwen.get("loaded")),
                "device": "cpu",
                "measured_aggregate_rtf": 0.7257,
                "measured_first_audible_seconds": 43.548,
                "measured_longest_gap_ms": 14,
                "backend": "PyTorch CPU bfloat16 SDPA batch4",
                "expression_scope": "Cue-routed normal/soft/whisper/heightened reference prompts for cloned voices; built-in 0.6B voices remain neutral.",
            },
        ),
        ProviderCapability(
            provider_id=PROVIDER_KOKORO,
            display_name="Kokoro",
            role="default_fast_fallback",
            available=kokoro_reachable,
            enabled=True,
            fallback_provider=PROVIDER_BROWSER,
            quality_tier="fast_local",
            supports_streaming=False,
            supports_chunk_cache=True,
            supports_resume=True,
            supports_pronunciation_aliases=True,
            supports_expression_controls=bool(kokoro.get("supported_options")),
            supports_cancel=True,
            supports_unload=False,
            benchmark_decision="Best practical local provider from the benchmark.",
            diagnostics={
                "base_url": kokoro.get("base_url") or KOKORO_BASE_URL,
                "speech_endpoint": kokoro.get("speech_endpoint"),
                "reachable": kokoro_reachable,
                "voice_count": kokoro.get("voice_count"),
            },
        ),
        ProviderCapability(
            provider_id=PROVIDER_BROWSER,
            display_name="Browser fallback",
            role="last_resort",
            available=True,
            enabled=True,
            fallback_provider=None,
            quality_tier="browser_fallback",
            supports_streaming=True,
            supports_chunk_cache=False,
            supports_resume=True,
            supports_pronunciation_aliases=False,
            supports_expression_controls=False,
            supports_cancel=True,
            supports_unload=False,
            benchmark_decision="Fallback only; not a high-quality narrator.",
        ),
    ]
    return [capability.model_dump() for capability in capabilities]


def provider_snapshot(
    *,
    active_provider: str,
    kokoro: dict[str, Any] | None = None,
    qwen: dict[str, Any] | None = None,
    browser_fallback_enabled: bool = False,
) -> dict[str, Any]:
    capabilities = provider_capabilities(kokoro, qwen)
    return {
        "interface": list(TTS_PROVIDER_INTERFACE_METHODS),
        "providers": capabilities,
        "active_provider": active_provider,
        "effective_primary_provider": active_provider,
        "requested_primary_provider": active_provider,
        "high_quality_local": capabilities[0],
        "kokoro": capabilities[1],
        "browser": capabilities[2],
        "browser_fallback_enabled": browser_fallback_enabled,
        "selection_gate": {
            "passed": high_quality_local_is_eligible(),
            "reason": HIGH_QUALITY_ACCEPTANCE,
            "report_path": str(BENCHMARK_REPORT_PATH),
        },
    }


def fallback_for_requested_provider(provider: str | None) -> tuple[str, str | None]:
    if provider == PROVIDER_HIGH_QUALITY_LOCAL:
        if high_quality_local_is_eligible():
            return PROVIDER_HIGH_QUALITY_LOCAL, None
        return PROVIDER_KOKORO, f"Local Qwen model is missing: {QWEN_MODEL_PATH}"
    if provider in {PROVIDER_KOKORO, PROVIDER_BROWSER}:
        return provider, None
    return PROVIDER_KOKORO, f"Unsupported TTS provider '{provider}'. Falling back to Kokoro."


def append_high_quality_integration_report(title: str, details: dict[str, Any]) -> None:
    if not diagnostic_logging_enabled():
        return
    path = DATA_DIR / "logs" / "HIGH_QUALITY_TTS_INTEGRATION_REPORT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# High Quality TTS Integration Report\n\n"
            "This report records the provider-gating integration pass. It intentionally does not store story text.\n",
            encoding="utf-8",
        )
    lines = [f"\n## {title}"]
    for key, value in details.items():
        lines.append(f"- {key}: {value}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
