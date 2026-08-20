from __future__ import annotations

import hashlib
import re
from typing import Any


NORMALIZATION_VERSION = "tts-realism-v1"
PREVIEW_SAMPLE_TEXT = (
    "The lantern guttered in the cold draft. Elara lowered her voice. "
    "'Wait until the ridge goes dark,' she said. Beyond the barn, the valley held its breath."
)

VOICE_PROFILE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "premium_female_narrator",
        "display_name": "Premium Female Narrator",
        "provider": "high_quality_local",
        "voice_preferences": [],
        "voice_id": "Serena",
        "model": "Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "speed": 1.0,
        "style_notes": "Warm local Serena narration with progressive sentence batching; built-in 0.6B delivery remains neutral.",
        "recommended_use": "Quality-first scene and chapter narration with a measured initial preparation window.",
        "default": False,
        "enabled": True,
        "available": True,
        "fallback_provider": "kokoro",
        "quality_mode": "premium",
        "chunking_profile": "natural",
        "narration_pacing": "natural",
        "dialogue_pause_strength": "medium",
        "paragraph_pause_strength": "medium",
        "dialogue_narration_style": "neutral",
        "pronunciation_profile": "global_story_aliases",
        "authorized_reference_metadata": {
            "required_for_voice_cloning": False,
            "supplied": False,
            "policy": "Use only user-owned or explicitly authorized female reference audio.",
        },
        "unavailable_reason": "",
        "benchmark_report": r"D:\StoryDriver\tts_engines\reports\TTS_PROVIDER_BENCHMARK_REPORT.md",
    },
    {
        "id": "natural_female_narrator",
        "display_name": "Natural Female Narrator",
        "provider": "kokoro",
        "voice_preferences": ["af_aoede", "af_bella", "af_heart", "af_nicole", "af_sarah", "af_sky"],
        "speed": 0.95,
        "style_notes": "Balanced long-form narration with gentle pacing and clear dialogue.",
        "recommended_use": "Default prose scenes and chapters.",
        "default": True,
        "enabled": True,
        "chunking_profile": "natural",
        "narration_pacing": "natural",
        "dialogue_pause_strength": "medium",
        "paragraph_pause_strength": "medium",
        "dialogue_narration_style": "neutral",
    },
    {
        "id": "soft_female_narrator",
        "display_name": "Soft Female Narrator",
        "provider": "kokoro",
        "voice_preferences": ["af_nicole", "af_sarah", "af_bella", "af_heart", "bf_emma"],
        "speed": 0.9,
        "style_notes": "Softer and slower delivery for intimate or emotional scenes.",
        "recommended_use": "Quiet scenes, reflective chapters, gentle dialogue.",
        "default": False,
        "enabled": True,
        "chunking_profile": "natural",
        "narration_pacing": "slow",
        "dialogue_pause_strength": "medium",
        "paragraph_pause_strength": "high",
        "dialogue_narration_style": "minimal",
    },
    {
        "id": "dark_fantasy_female_narrator",
        "display_name": "Dark Fiction Female Narrator",
        "provider": "kokoro",
        "voice_preferences": ["af_aoede", "af_jadzia", "af_bella", "af_heart", "af_v0irulan"],
        "speed": 0.95,
        "style_notes": "Weightier delivery for candlelit fantasy, folklore, and grim adventure.",
        "recommended_use": "Dark fantasy, medieval journeys, tense atmospheric scenes.",
        "default": False,
        "enabled": True,
        "chunking_profile": "audiobook",
        "narration_pacing": "natural",
        "dialogue_pause_strength": "high",
        "paragraph_pause_strength": "medium",
        "dialogue_narration_style": "slightly_dramatic",
    },
    {
        "id": "clear_female_read_aloud",
        "display_name": "Clear Female Read-Aloud",
        "provider": "kokoro",
        "voice_preferences": ["af_heart", "af_sky", "af_nova", "af_aoede"],
        "speed": 1.0,
        "style_notes": "Clean, direct delivery with reliable clarity.",
        "recommended_use": "Fast review, edits, and plain read-back.",
        "default": False,
        "enabled": True,
        "chunking_profile": "fast",
        "narration_pacing": "fast",
        "dialogue_pause_strength": "low",
        "paragraph_pause_strength": "medium",
        "dialogue_narration_style": "neutral",
    },
    {
        "id": "neutral_female",
        "display_name": "Neutral Female",
        "provider": "kokoro",
        "voice_preferences": ["af_heart", "af_alloy", "af_nova", "af_sky", "bf_alice"],
        "speed": 1.0,
        "style_notes": "Neutral fallback female-coded voice profile.",
        "recommended_use": "General local narration when preferred voices are unavailable.",
        "default": False,
        "enabled": True,
        "chunking_profile": "natural",
        "narration_pacing": "natural",
        "dialogue_pause_strength": "medium",
        "paragraph_pause_strength": "medium",
        "dialogue_narration_style": "neutral",
    },
]

FEMALE_VOICE_RE = re.compile(r"^[a-z]f_", re.IGNORECASE)
PRONUNCIATION_ENTRY_RE = re.compile(r"\s*(?P<written>.+?)\s*(?:=>|->|=)\s*(?P<spoken>.+?)\s*$")


def is_female_voice(voice_id: str) -> bool:
    return bool(FEMALE_VOICE_RE.match(str(voice_id or "")))


def female_voice_ids(voices: list[str] | tuple[str, ...] | None) -> list[str]:
    return [voice for voice in sorted(set(voices or [])) if is_female_voice(voice)]


def choose_profile_voice(profile: dict[str, Any], available_voices: list[str] | tuple[str, ...] | None = None) -> str:
    available = sorted(set(available_voices or []))
    available_set = set(available)
    for voice in profile.get("voice_preferences", []):
        if not available_set or voice in available_set:
            return voice
    female = female_voice_ids(available)
    if female:
        return female[0]
    if "af_heart" in available_set or not available:
        return "af_heart"
    return available[0]


def build_voice_profiles(available_voices: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for definition in VOICE_PROFILE_DEFINITIONS:
        profile = {
            key: value
            for key, value in definition.items()
            if key != "voice_preferences"
        }
        if definition.get("provider") == "high_quality_local":
            profile["voice_id"] = definition.get("voice_id")
        else:
            profile["voice_id"] = choose_profile_voice(definition, available_voices)
        profile["voice_preferences"] = list(definition.get("voice_preferences", []))
        profiles.append(profile)
    return profiles


def profile_by_id(profile_id: str | None, available_voices: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    profiles = build_voice_profiles(available_voices)
    if profile_id:
        for profile in profiles:
            if profile.get("id") == profile_id:
                return profile
    for profile in profiles:
        if profile.get("default"):
            return profile
    return profiles[0]


def parse_pronunciation_text(text: str | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = PRONUNCIATION_ENTRY_RE.match(stripped)
        if not match:
            continue
        written = match.group("written").strip()
        spoken = match.group("spoken").strip()
        if written and spoken:
            entries.append(
                {
                    "id": hashlib.sha1(f"{written}->{spoken}".encode("utf-8")).hexdigest()[:12],
                    "written_form": written,
                    "spoken_form": spoken,
                    "scope": "global",
                    "story_id": None,
                    "enabled": True,
                }
            )
    return entries


def pronunciation_entries_to_text(entries: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for entry in entries or []:
        if not entry or entry.get("enabled") is False:
            continue
        written = str(entry.get("written_form") or "").strip()
        spoken = str(entry.get("spoken_form") or "").strip()
        if written and spoken:
            lines.append(f"{written} => {spoken}")
    return "\n".join(lines)


def matching_pronunciation_entries(
    entries: list[dict[str, Any]] | None,
    story_id: str | None = None,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    active_story_id = str(story_id or "").strip()
    for entry in entries or []:
        if not entry or entry.get("enabled") is False:
            continue
        written = str(entry.get("written_form") or "").strip()
        spoken = str(entry.get("spoken_form") or "").strip()
        if not written or not spoken:
            continue
        scope = str(entry.get("scope") or "global").strip().lower()
        if scope == "story":
            entry_story_id = str(entry.get("story_id") or "").strip()
            if not active_story_id or entry_story_id != active_story_id:
                continue
        matched.append(entry)
    return matched


def pronunciation_dictionary_version(entries: list[dict[str, Any]] | None, story_id: str | None = None) -> str:
    normalized: list[dict[str, Any]] = []
    for entry in matching_pronunciation_entries(entries, story_id=story_id):
        written = str(entry.get("written_form") or "").strip()
        spoken = str(entry.get("spoken_form") or "").strip()
        if not written or not spoken:
            continue
        normalized.append(
            {
                "written_form": written.casefold(),
                "spoken_form": spoken,
                "scope": str(entry.get("scope") or "global"),
                "story_id": str(entry.get("story_id") or ""),
            }
        )
    normalized.sort(key=lambda item: (item["scope"], item["story_id"], item["written_form"], item["spoken_form"]))
    raw = repr(normalized).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


UI_ONLY_LINE_PATTERNS = [
    re.compile(r"^\s*(scene|version|director note|generation stats|model|backend|prompt estimate|raw stream|reasoning chars)\s*:", re.I),
    re.compile(r"^\s*(image generation|images paused|image settings|comfyui|lm studio|kokoro|tts diagnostics)\s*:", re.I),
    re.compile(r"^\s*(regenerate|rewrite|revise|narrate|copy|note|qa checklist|generate image|images paused)\s*$", re.I),
    re.compile(r"^\s*(visible t/s|first token|tokens|chunk|cache hit)\s*:", re.I),
]


def strip_ui_artifacts(text: str) -> tuple[str, int]:
    kept: list[str] = []
    removed = 0
    in_code_block = False
    for line in text.replace("\r", "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            removed += 1
            continue
        if in_code_block:
            removed += 1
            continue
        if any(pattern.match(stripped) for pattern in UI_ONLY_LINE_PATTERNS):
            removed += 1
            continue
        stripped = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        stripped = re.sub(r"^\s*[-*+]\s+(?=\S)", "", stripped)
        stripped = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
        stripped = re.sub(r"`([^`]+)`", r"\1", stripped)
        stripped = stripped.replace("**", "").replace("__", "").replace("*", "")
        kept.append(stripped.rstrip())
    return "\n".join(kept), removed


def apply_pronunciations(text: str, entries: list[dict[str, Any]] | None, story_id: str | None = None) -> tuple[str, int]:
    count = 0
    result = text
    enabled_entries = matching_pronunciation_entries(entries, story_id=story_id)
    enabled_entries.sort(key=lambda entry: len(str(entry.get("written_form") or "")), reverse=True)
    for entry in enabled_entries:
        written = str(entry.get("written_form") or "").strip()
        spoken = str(entry.get("spoken_form") or "").strip()
        if not written or not spoken:
            continue
        pattern = re.compile(rf"(?<![\w']){re.escape(written)}(?![\w'])", re.IGNORECASE)
        result, replacements = pattern.subn(spoken, result)
        count += replacements
    return result, count


def normalize_tts_text(
    text: str,
    settings: Any,
    profile: dict[str, Any] | None = None,
    story_id: str | None = None,
) -> dict[str, Any]:
    started = text or ""
    stripped, removed_lines = strip_ui_artifacts(started)
    normalized = stripped.replace("\u00a0", " ")
    normalized = re.sub(r"\s*[\u2014\u2013]\s*", ", ", normalized)
    normalized = re.sub(r"\s*&\s*", " and ", normalized)
    normalized = normalized.replace("...", "... ")
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)

    dialogue_style = getattr(settings, "dialogue_narration_style", None) or (profile or {}).get("dialogue_narration_style") or "neutral"
    dialogue_pause = getattr(settings, "dialogue_pause_strength", None) or (profile or {}).get("dialogue_pause_strength") or "medium"
    paragraph_pause = getattr(settings, "paragraph_pause_strength", None) or (profile or {}).get("paragraph_pause_strength") or "medium"

    if dialogue_style == "slightly_dramatic" or dialogue_pause == "high":
        normalized = re.sub(r"([.!?])\s+(['\"])", r"\1  \2", normalized)
        normalized = re.sub(r"(['\"])\s+([A-Z])", r"\1  \2", normalized)
    elif dialogue_style == "minimal" or dialogue_pause == "low":
        normalized = re.sub(r"\s+(['\"])", r" \1", normalized)

    if paragraph_pause == "high":
        normalized = re.sub(r"\n\n+", "\n\n\n", normalized)
    elif paragraph_pause == "low":
        normalized = re.sub(r"\n\n+", "\n", normalized)

    entries = getattr(settings, "pronunciation_entries", None) or []
    normalized, pronunciation_replacements = apply_pronunciations(normalized, entries, story_id=story_id)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized).strip()
    return {
        "text": normalized,
        "normalization_version": NORMALIZATION_VERSION,
        "removed_ui_lines": removed_lines,
        "pronunciation_replacements": pronunciation_replacements,
        "pronunciation_dictionary_version": pronunciation_dictionary_version(entries, story_id=story_id),
    }
