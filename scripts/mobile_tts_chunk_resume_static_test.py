from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TTS_CONTROLLER = ROOT / "frontend" / "src" / "services" / "ttsController.js"
MINI_PLAYER = ROOT / "frontend" / "src" / "components" / "NarrationMiniPlayer.jsx"
STORE = ROOT / "frontend" / "src" / "store" / "useAppStore.js"


def require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> None:
    controller = TTS_CONTROLLER.read_text(encoding="utf-8")
    mini_player = MINI_PLAYER.read_text(encoding="utf-8")
    store = STORE.read_text(encoding="utf-8")

    require(controller, "ensureAudioElement", "persistent audio element helper")
    require(controller, "requestAudioPlay", "awaited audio.play helper")
    require(controller, "Tap play to continue narration", "mobile user-resume message")
    require(controller, "startPlaybackWatchdog", "silent stuck playback watchdog")
    require(controller, "audio.playsInline = true", "iOS inline playback hint")
    require(store, "needsUserResume", "store user-resume state")
    require(store, "await get().resumeNarration()", "resume instead of restart path")
    require(mini_player, "Hide TTS options", "mobile quick settings collapse button")
    require(mini_player, 'aria-controls="narration-mini-player-options"', "mobile TTS options toggle")
    require(mini_player, "aria-expanded={optionsOpen}", "controlled mobile TTS options state")
    require(mini_player, 'id="narration-mini-player-options"', "inline mobile TTS options panel")

    print("mobile_tts_chunk_resume_static_test passed")


if __name__ == "__main__":
    main()
