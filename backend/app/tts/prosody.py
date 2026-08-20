from __future__ import annotations

import re
from typing import Any, Literal


NarrationStyle = Literal[
    "normal",
    "soft",
    "whisper",
    "heightened",
    "distressed",
    "intimate",
    "somber",
    "tense",
]
NarrationEmotion = Literal["neutral", "soft", "tense", "urgent", "distressed", "intimate", "somber"]
IntensityBucket = Literal["low", "medium", "high"]
BreathingMode = Literal["off", "natural", "cinematic"]
BreathEvent = Literal[
    "soft_inhale",
    "normal_inhale",
    "shaky_inhale",
    "quiet_exhale",
    "recovering_breath",
    "gasp",
    "none",
]

QUOTE_PATTERN = re.compile(r"(?:[\"\u201c][^\"\u201d\n]+[\"\u201d])", flags=re.DOTALL)

# Order matters. Explicit low-volume and high-volume speech verbs outrank
# nearby emotional language. Broad scene words deliberately do not appear.
CUE_PATTERNS: tuple[tuple[NarrationStyle, tuple[str, ...]], ...] = (
    (
        "whisper",
        (
            r"\bwhisper(?:s|ed|ing)?\b",
            r"\bunder (?:her|his|their) breath\b",
            r"\bbarely audible\b",
            r"\bbreathed the words\b",
            r"\bvoice scarcely above a breath\b",
            r"\bhissed quietly\b",
        ),
    ),
    (
        "heightened",
        (
            r"\bshout(?:s|ed|ing)?\b",
            r"\byell(?:s|ed|ing)?\b",
            r"\bscream(?:s|ed|ing)?\b",
            r"\bcried out\b",
            r"\bcalled over the noise\b",
            r"\braised (?:her|his|their) voice\b",
            r"\bbarked the order\b",
        ),
    ),
    (
        "distressed",
        (
            r"\bsob(?:s|bed|bing)?\b",
            r"\b(?:her|his|their) voice broke\b",
            r"\bchoked out\b",
            r"\b(?:in|with) (?:a )?trembling voice\b",
            r"\bfought back tears\b",
            r"\bpanicked(?:ly)?\b",
        ),
    ),
    (
        "intimate",
        (
            r"\bspoke close to (?:her|him|them)\b",
            r"\bvoice warm and private\b",
            r"\bmurmured against (?:her|his|their) ear\b",
        ),
    ),
    (
        "somber",
        (
            r"\b(?:in|with) (?:a )?mournful voice\b",
            r"\bgrief-stricken\b",
            r"\b(?:in|with) (?:a )?hollow voice\b",
            r"\bwith exhausted sadness\b",
        ),
    ),
    (
        "tense",
        (
            r"\b(?:in|with) (?:a )?clipped voice\b",
            r"\b(?:said|asked|answered|replied|spoke) (?:in a )?strained(?: voice)?\b",
            r"\btightly controlled\b",
            r"\burgent but quiet\b",
            r"\bfear held in check\b",
        ),
    ),
    (
        "soft",
        (
            r"\b(?:said|asked|answered|replied|spoke) softly\b",
            r"\bsoftly (?:said|asked|answered|replied|spoke|replied)\b",
            r"\bmurmured\b",
            r"\b(?:in|with) (?:a )?(?:gentle|quiet|low) voice\b",
            r"\btenderly\b",
        ),
    ),
)

ASSOCIATED_SOFT_CUES = (
    r"\bleaned closer\b",
    r"\bdrew closer\b",
    r"\bspoke near (?:her|him|them)\b",
)
NARRATION_EMOTION_PATTERNS: tuple[tuple[NarrationEmotion, tuple[str, ...]], ...] = (
    ("distressed", (r"\bfought back tears\b", r"\bgrief-stricken\b")),
    ("somber", (r"\bmournful\b", r"\bexhausted sadness\b", r"\bhollow silence\b")),
    ("tense", (r"\bfear held in check\b", r"\btightly controlled\b", r"\bstrained silence\b")),
)
PHYSICAL_BEAT_PATTERNS = (
    r"\bcaught (?:her|his|their) breath\b",
    r"\bhesitat(?:ed|ing)\b",
    r"\bpaus(?:ed|ing)\b",
    r"\bswallowed\b",
    r"\btook a breath\b",
)

# Breath classification is intentionally narrower than delivery classification.
# Broad mood, romance, suspense, and adult-content words never appear here.
BREATH_CUE_PATTERNS: tuple[tuple[BreathEvent, tuple[str, ...], float], ...] = (
    (
        "gasp",
        (
            r"\bgasp(?:s|ed|ing)?\b",
            r"\bsharp intake of breath\b",
            r"\bsucked in a startled breath\b",
        ),
        0.99,
    ),
    (
        "recovering_breath",
        (
            r"\bpant(?:s|ed|ing)?\b",
            r"\bbreathless from running\b",
            r"\bdoubled over for air\b",
            r"\bchest heaving\b",
            r"\brecovering after exertion\b",
            r"\btrying to catch (?:her|his|their) breath\b",
        ),
        0.98,
    ),
    (
        "shaky_inhale",
        (
            r"\b(?:(?:her|his|their|[a-z][a-z'-]*['\u2019]s)\s+)?breath hitched\b",
            r"\bcaught (?:her|his|their) breath\b",
            r"\bbreathing trembled\b",
            r"\bstruggled for breath\b",
            r"\bfought to steady (?:her|his|their) breathing\b",
            r"\bvoice broke after an inhale\b",
        ),
        0.98,
    ),
    (
        "soft_inhale",
        (
            r"\bdrew a slow breath\b",
            r"\btook a slow breath\b",
            r"\bdrew a quiet breath\b",
            r"\btook a quiet breath\b",
        ),
        0.97,
    ),
    (
        "normal_inhale",
        (
            r"\binhal(?:e|es|ed|ing)\b",
            r"\bdrew a(?: deep)? breath\b",
            r"\btook a(?: deep)? breath\b",
            r"\bbreathed in\b",
            r"\bfilled (?:her|his|their) lungs\b",
            r"\bsteadied (?:her|his|their) breathing\b",
        ),
        0.96,
    ),
    (
        "quiet_exhale",
        (
            r"\bbreath warm against (?:an?|her|his|their) ear\b",
            r"\bspoke barely above a breath\b",
            r"\bwhispered close to (?:her|him|them)\b",
        ),
        0.92,
    ),
)

CINEMATIC_BREATH_CUE_PATTERNS: tuple[tuple[BreathEvent, tuple[str, ...], float], ...] = (
    (
        "shaky_inhale",
        (
            r"\b(?:her|his|their) voice broke\b",
            r"\b(?:her|his|their) breathing went ragged\b",
        ),
        0.86,
    ),
    (
        "quiet_exhale",
        (
            r"\bmurmured against (?:her|his|their) ear\b",
            r"\bvoice warm and private\b",
        ),
        0.84,
    ),
)


def classify_breath_performance(text: str, mode: str = "natural") -> dict[str, Any]:
    normalized_mode: BreathingMode = mode if mode in {"off", "natural", "cinematic"} else "natural"  # type: ignore[assignment]
    empty = {
        "breathing_mode": normalized_mode,
        "breath_before": None,
        "breath_after": None,
        "breath_confidence": 0.0,
        "breath_source_cue": "",
        "breath_explicit": False,
    }
    if normalized_mode == "off":
        return empty

    value = text or ""
    for event, patterns, confidence in BREATH_CUE_PATTERNS:
        cue = _first_match(value, patterns)
        if not cue:
            continue
        # Quiet proximity is allowed only with the explicit breath/proximity
        # wording above. Cinematic changes restraint, not the factual trigger.
        threshold = 0.9 if normalized_mode == "natural" else 0.82
        if confidence < threshold:
            return empty
        placement = "breath_after" if event == "quiet_exhale" else "breath_before"
        return {
            **empty,
            placement: event,
            "breath_confidence": confidence,
            "breath_source_cue": cue,
            "breath_explicit": True,
        }
    if normalized_mode == "cinematic":
        for event, patterns, confidence in CINEMATIC_BREATH_CUE_PATTERNS:
            cue = _first_match(value, patterns)
            if not cue:
                continue
            placement = "breath_after" if event == "quiet_exhale" else "breath_before"
            return {
                **empty,
                placement: event,
                "breath_confidence": confidence,
                "breath_source_cue": cue,
                "breath_explicit": False,
            }
    return empty

STYLE_EMOTION: dict[NarrationStyle, NarrationEmotion] = {
    "normal": "neutral",
    "soft": "soft",
    "whisper": "tense",
    "heightened": "urgent",
    "distressed": "distressed",
    "intimate": "intimate",
    "somber": "somber",
    "tense": "tense",
}
STYLE_INTENSITY: dict[NarrationStyle, float] = {
    "normal": 0.25,
    "soft": 0.4,
    "whisper": 0.75,
    "heightened": 0.82,
    "distressed": 0.7,
    "intimate": 0.45,
    "somber": 0.5,
    "tense": 0.58,
}
STYLE_INSTRUCTIONS: dict[NarrationStyle, str] = {
    "normal": "",
    "soft": "Use a restrained soft delivery while preserving the speaker identity.",
    "whisper": "Use a restrained whisper while preserving the speaker identity.",
    "heightened": "Use controlled urgency without changing the speaker identity.",
    "distressed": "Use restrained distress without melodrama or a voice-identity shift.",
    "intimate": "Use a warm private delivery without theatrical exaggeration.",
    "somber": "Use a subdued somber delivery while preserving the speaker identity.",
    "tense": "Use a tightly controlled tense delivery without raising pitch.",
}


def _first_match(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip().lower()
    return ""


def _contains_dialogue(text: str) -> bool:
    return bool(QUOTE_PATTERN.search(text or ""))


def _detected_style(text: str, contains_dialogue: bool) -> tuple[NarrationStyle, str, float]:
    if not contains_dialogue:
        return "normal", "", 0.0
    for style, patterns in CUE_PATTERNS:
        cue = _first_match(text, patterns)
        if cue:
            return style, cue, 0.98
    cue = _first_match(text, ASSOCIATED_SOFT_CUES)
    if cue:
        return "soft", cue, 0.62
    return "normal", "", 0.2


def classify_narration_style(text: str, requested: str | None = None) -> NarrationStyle:
    if requested in STYLE_INTENSITY:
        return requested  # type: ignore[return-value]
    style, _cue, _confidence = _detected_style(text or "", _contains_dialogue(text or ""))
    return style


def _narration_emotion(text: str, style: NarrationStyle, contains_dialogue: bool) -> NarrationEmotion:
    if style != "normal":
        return STYLE_EMOTION[style]
    for emotion, patterns in NARRATION_EMOTION_PATTERNS:
        if _first_match(text, patterns):
            return emotion
    # Punctuation alone never changes delivery or emotion classification.
    return "neutral" if contains_dialogue else "neutral"


def _intensity_bucket(value: float) -> IntensityBucket:
    if value >= 0.7:
        return "high"
    if value >= 0.4:
        return "medium"
    return "low"


def _pause_after(
    text: str,
    style: NarrationStyle,
    *,
    paragraph_break_after: bool,
    scene_break_after: bool,
    speaker_change_after: bool,
) -> int:
    if scene_break_after:
        return 900
    if paragraph_break_after:
        return 380
    pause_ms = 0
    stripped = text.rstrip()
    if re.search(r"(?:\.{3}|\u2026)[\"'\u201d\u2019)]?$", stripped):
        pause_ms = max(pause_ms, 220)
    elif re.search(r"(?:--|\u2014)[\"'\u201d\u2019)]?$", stripped):
        pause_ms = max(pause_ms, 180)
    elif re.search(r"[.!?][\"'\u201d\u2019)]?$", stripped):
        pause_ms = max(pause_ms, 120)
    if speaker_change_after:
        pause_ms = max(pause_ms, 140)
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in PHYSICAL_BEAT_PATTERNS):
        pause_ms = max(pause_ms, 220)
    if style == "whisper":
        pause_ms = max(pause_ms, 250)
    elif style != "normal":
        pause_ms = max(pause_ms, 160)
    return min(pause_ms, 1200)


def narration_direction(
    text: str,
    requested: str | None = None,
    *,
    paragraph_break_after: bool = False,
    scene_break_after: bool = False,
    speaker_change_after: bool = False,
    breathing_mode: str = "natural",
) -> dict[str, Any]:
    value = text or ""
    contains_dialogue = _contains_dialogue(value)
    detected_style, source_cue, confidence = _detected_style(value, contains_dialogue)
    style = classify_narration_style(value, requested)
    if requested in STYLE_INTENSITY:
        source_cue = "internal override"
        confidence = 1.0
    emotion = _narration_emotion(value, style, contains_dialogue)
    intensity = STYLE_INTENSITY[style]
    if style == "normal" and emotion != "neutral":
        intensity = 0.3
    breath = classify_breath_performance(value, breathing_mode)
    return {
        "style": style,
        "emotion": emotion,
        "intensity": intensity,
        "intensity_bucket": _intensity_bucket(intensity),
        "pause_before_ms": 0,
        "pause_after_ms": _pause_after(
            value,
            style,
            paragraph_break_after=paragraph_break_after,
            scene_break_after=scene_break_after,
            speaker_change_after=speaker_change_after,
        ),
        "source_cue": source_cue,
        "contains_dialogue": contains_dialogue,
        "explicit_cue": detected_style != "normal" and confidence >= 0.9,
        "confidence": confidence,
        "recommended_generation_instruction": STYLE_INSTRUCTIONS[style],
        "audible_text_unchanged": True,
        **breath,
    }
