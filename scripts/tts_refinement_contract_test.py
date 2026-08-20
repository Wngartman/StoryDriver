from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(r"D:\StoryDriver")
CONTROLLER = ROOT / "frontend" / "src" / "services" / "ttsController.js"
MINI_PLAYER = ROOT / "frontend" / "src" / "components" / "NarrationMiniPlayer.jsx"
MODEL_SETTINGS = ROOT / "frontend" / "src" / "components" / "ModelSettingsModal.jsx"
RESULTS = ROOT / "tts_engines" / "qwen3_tts" / "benchmarks" / "results"
ACCEPTANCE = RESULTS / "qwen_custom_voice_acceptance_result.json"
BASE_MODEL = ROOT / "tts_engines" / "qwen3_tts" / "models" / "Qwen3-TTS-12Hz-0.6B-Base"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> dict:
    require(path.exists(), f"Required evidence is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def gap_transition() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    require("const activeAudio = preload?.element || ensureAudioElement();" in source, "Preloaded audio is not adopted.")
    require("if (!preload && audio.src !== audioUrl)" in source, "Playback can reload an already preloaded source.")
    require("preload.canPlayAt = performance.now();" in source, "Preload readiness is not measured.")
    require(
        'provider: response.effective_provider || response.provider || "kokoro"' in source,
        "Playback provider is not derived from the effective synthesis response.",
    )
    require(
        'lastProviderUsed: response.effective_provider || response.provider || "kokoro"' in source,
        "Last provider is not derived from the effective synthesis response.",
    )


def effective_voice() -> None:
    gap_transition()
    player = MINI_PLAYER.read_text(encoding="utf-8")
    require("Effective voice" in player, "Active player does not label the effective voice.")
    require("{effectiveVoice}" in player, "Active player does not show the computed effective voice.")
    require("Kokoro narration voice" not in player, "Active player still has a false Kokoro label.")
    require('aria-label="Narration voice"' not in player, "Active player still contains a voice dropdown.")
    require("setVoice" not in player, "Active player can still change voices.")
    result = load_json(ACCEPTANCE)
    require(bool(result.get("checks", {}).get("chunk_truth_persisted")), "Effective custom voice truth was not persisted.")


def gapless() -> None:
    for minutes in (6, 12):
        result = load_json(RESULTS / f"qwen_{minutes}_minute_continuous_result.json")
        browser = result.get("browser") or {}
        require(result.get("status") == "complete", f"{minutes}-minute playback did not complete.")
        require(int(browser.get("underrun_count", 1)) == 0, f"{minutes}-minute playback had an underrun.")
        require(float(browser.get("longest_gap_ms", 9999)) < 100, f"{minutes}-minute loaded gap exceeded 100 ms.")


def adaptive_buffer() -> None:
    completed = subprocess.run(
        ["node", str(ROOT / "scripts" / "tests" / "qwen_premium_plan_test.mjs")],
        cwd=ROOT,
        check=False,
    )
    require(completed.returncode == 0, "Adaptive reserve/chunk planning contract failed.")


def base_install() -> None:
    require(BASE_MODEL.exists(), "Qwen Base model directory is missing.")
    require((BASE_MODEL / "config.json").exists(), "Qwen Base config is missing.")
    require((BASE_MODEL / "model.safetensors").exists(), "Qwen Base weights are missing.")
    require(sum(path.stat().st_size for path in BASE_MODEL.rglob("*") if path.is_file()) > 2_000_000_000, "Qwen Base install is incomplete.")
    worker = (ROOT / "tts_engines" / "qwen3_tts" / "service" / "app.py").read_text(encoding="utf-8")
    require('BASE_MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"' in worker, "Base revision is not pinned.")


def prompt_cache() -> None:
    checks = load_json(ACCEPTANCE).get("checks") or {}
    for key in ("prompt_ready", "four_style_prompts", "fresh_outputs", "distinct_cache_keys", "cache_reuse"):
        require(bool(checks.get(key)), f"Custom voice acceptance check failed: {key}")


def voice_delete() -> None:
    result = load_json(ACCEPTANCE)
    require(bool(result.get("checks", {}).get("voice_cleanup")), "Disposable custom voice was not removed.")


def custom_continuous() -> None:
    result = load_json(RESULTS / "qwen_custom_12_minute_continuous_result.json")
    browser = result.get("browser") or {}
    milestone = browser.get("six_minute_milestone") or {}
    require(result.get("status") == "complete", "Custom-voice 12-minute playback did not complete.")
    require(float(browser.get("played_audio_seconds", 0)) >= 720, "Custom voice did not play for twelve minutes.")
    require(float(milestone.get("played_audio_seconds", 0)) >= 360, "Custom voice lacks a six-minute milestone.")
    require(int(browser.get("underrun_count", 1)) == 0, "Custom-voice playback had an underrun.")
    require(float(browser.get("longest_gap_ms", 9999)) < 100, "Custom-voice loaded gap exceeded 100 ms.")
    generation = result.get("generation") or {}
    styles = set((generation.get("style_counts") or {}).keys())
    references = set((generation.get("reference_counts") or {}).keys())
    require(bool(generation.get("automatic_direction")), "Continuous playback did not use automatic direction.")
    require(
        {"normal", "soft", "whisper", "heightened", "distressed", "intimate", "somber", "tense"}.issubset(styles),
        f"Continuous playback missed automatic styles: {sorted(styles)}",
    )
    require({"normal", "soft", "whisper", "heightened"}.issubset(references), "Style references did not switch.")
    require(
        int(generation.get("cache_identity_conflict_count") or 0) == 0,
        "Continuous narration cache identity was reused across different direction metadata.",
    )
    require(
        int(generation.get("style_switch_count") or 0) >= 8,
        "Automatic style switching did not occur repeatedly.",
    )


def prosody() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.tts.prosody import narration_direction

    cases = {
        '"Do not move," Mara whispered.': "whisper",
        '"I can hear you," Mara said softly.': "soft",
        '"Get back!" Mara shouted.': "heightened",
        'The quiet kitchen held three empty cups.': "normal",
        '"The kitchen is quiet," Mara said.': "normal",
    }
    for text, expected in cases.items():
        direction = narration_direction(text)
        require(direction["style"] == expected, f"Prosody mismatch for {text!r}: {direction}")
        require(direction["audible_text_unchanged"] is True, "Prosody routing may alter audible prose.")


def model_settings() -> None:
    source = MODEL_SETTINGS.read_text(encoding="utf-8")
    for title in ("Preset", "System Prompt", "Per-task model overrides", "Writing Length", "Prose Prompt Preview", "Core Settings"):
        require(f'<Section defaultOpen={{false}} title="{title}">' in source, f"{title} is not collapsed by default.")
    require('<Section title="Model Connection">' in source, "Model Connection is not visible by default.")
    require('<Section title="Writing Engine">' in source, "Writing Engine is not visible by default.")
    for summary in ("All writing tasks use:", "Structured scene planning:", "Quality review:", "Story State extraction:"):
        require(summary in source, f"Writing Engine summary is missing: {summary}")
    require("Use global model for every task" in source, "Global-model routing action is missing.")
    require("Clear all overrides" in source, "Clear-all-overrides action is missing.")
    for obsolete in (
        "Use Gemma for all tasks",
        "Use Gemma for utility tasks",
        "Use current model for all tasks",
        "Reset all tasks to global",
        "Use Native Chat reasoning off for prose",
        "Use Native reasoning off for writing tasks",
    ):
        require(obsolete not in source, f"Redundant model shortcut remains: {obsolete}")


def mobile_ui() -> None:
    effective_voice()
    settings = (ROOT / "frontend" / "src" / "components" / "CustomVoiceLibrary.jsx").read_text(encoding="utf-8")
    for upload_type in (".wav", ".flac", ".mp3", "audio/wav", "audio/flac", "audio/mpeg"):
        require(upload_type in settings, f"Mobile upload picker lacks {upload_type}.")
    require("min-w-0" in settings, "Custom voice controls lack mobile width containment.")


CASES = {
    "gap-transition": gap_transition,
    "effective-voice": effective_voice,
    "gapless": gapless,
    "adaptive-buffer": adaptive_buffer,
    "base-install": base_install,
    "prompt-cache": prompt_cache,
    "voice-delete": voice_delete,
    "custom-continuous": custom_continuous,
    "prosody": prosody,
    "model-settings": model_settings,
    "mobile-ui": mobile_ui,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=sorted(CASES))
    args = parser.parse_args()
    try:
        CASES[args.case]()
    except Exception as error:
        print(f"FAIL [{args.case}]: {error}")
        return 1
    print(f"PASS [{args.case}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
