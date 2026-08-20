from pathlib import Path


ROOT = Path(r"D:\StoryDriver")
TOOLS = (ROOT / "frontend" / "src" / "components" / "NarrationSettingsTools.jsx").read_text(encoding="utf-8")
SETTINGS = (ROOT / "frontend" / "src" / "components" / "SettingsDrawer.jsx").read_text(encoding="utf-8")
PLAYER = (ROOT / "frontend" / "src" / "components" / "NarrationMiniPlayer.jsx").read_text(encoding="utf-8")


def require(source: str, marker: str, description: str) -> None:
    if marker not in source:
        raise AssertionError(f"Missing {description}: {marker}")


for marker, description in (
    ("VoiceComparisonLab", "blind comparison component"),
    ("premium_female_narrator", "Qwen Premium comparison profile"),
    ("natural_female_narrator", "Kokoro comparison profile"),
    ("api.previewTTS", "local cached preview request"),
    ("Sample ${String.fromCharCode", "randomized blind labels"),
    ("storydriver_tts_comparison_ratings_v1", "compact local rating storage"),
    ("ratingsComplete", "rating-before-reveal gate"),
    ("Use profile", "explicit post-reveal profile selection"),
    ("NarrationDeviceDiagnostics", "physical-device diagnostics"),
    ("storydriver_tts_cursor:", "saved cursor diagnostics"),
    ("audioContext", "audio-context diagnostics"),
    ("mediaSession", "media-session diagnostics"),
    ("serviceWorker", "service-worker diagnostics"),
    ("Copy diagnostic summary", "copyable diagnostic summary"),
):
    require(TOOLS, marker, description)

for marker in ("VoiceComparisonLab", "NarrationDeviceDiagnostics"):
    require(SETTINGS, marker, f"Settings integration for {marker}")

for marker, description in (
    ("bufferedStartTime", "generated-range start"),
    ("bufferedEndTime", "generated-range end"),
    ("seekNarration", "buffered seek"),
    ("skipNarration(-10)", "rewind control"),
    ("needsUserResume", "mobile user-gesture recovery"),
):
    require(PLAYER, marker, description)

for forbidden in ("generated_text", "director_note", "selectedVersion"):
    if forbidden in TOOLS:
        raise AssertionError(f"Device diagnostic/comparison source must not read private prose: {forbidden}")

print("PASS: mobile narration UI, blind comparison, and private diagnostics contracts")
