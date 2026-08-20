import { motion } from "framer-motion";
import { ChevronUp, FastForward, Pause, Play, Rewind, SlidersHorizontal, Square } from "lucide-react";
import { useState } from "react";
import { ttsController } from "../services/ttsController.js";
import { useAppStore } from "../store/useAppStore.js";

function formatTime(seconds = 0) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

const transportButton =
  "sd-transport-button grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line/60 bg-[#101216]/82 text-zinc-300 transition hover:border-zinc-600 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-45";

const menuField =
  "sd-menu-field h-8 min-w-0 rounded-md border border-line/70 bg-[#0d0e11] px-2 text-xs text-zinc-200 outline-none transition focus:border-tide";

function speedLabel(speed) {
  return Number(speed) === 1 ? "1.0x" : `${speed}x`;
}

function providerLabel(provider) {
  if (provider === "high_quality_local") return "Qwen3-TTS 0.6B";
  if (provider === "kokoro") return "Kokoro";
  return "Browser";
}

function voiceLabel(voice) {
  const raw = String(voice || "");
  if (raw === "af_aoede") return "Aoede";
  if (raw === "af_heart") return "Heart";
  return raw || "Default voice";
}

function qwenModelLabel(model) {
  if (String(model || "").toLowerCase().includes("customvoice")) return "Qwen3-TTS 0.6B CustomVoice";
  if (String(model || "").toLowerCase().includes("base")) return "Qwen3-TTS 0.6B Base";
  return "Qwen3-TTS 0.6B";
}

function followModeLabel(settings = {}) {
  const raw = settings.tts_follow_mode || settings.tts_follow_highlight || (settings.highlight_narration ? "sentence" : "off");
  const mode =
    {
      subtle: "sentence",
      strong: "phrase",
      word: "word_estimate",
      word_estimated: "word_estimate",
      exact: "exact_word",
    }[raw] || raw || "sentence";
  if (mode === "off") return "";
  if (mode === "exact_word") return "Exact word unavailable";
  if (mode === "word_estimate") return "Word estimate";
  return `Following ${mode}`;
}

export default function NarrationMiniPlayer() {
  const [optionsOpen, setOptionsOpen] = useState(false);
  const {
    activeSessionId,
    getSceneAndVersion,
    getSelectedSceneVersion,
    narration,
    pauseNarration,
    playNarration,
    scenesBySession,
    sessions,
    setNarrationSpeed,
    seekNarration,
    skipNarration,
    stopNarration,
    ttsSettings,
  } = useAppStore();

  const selectedVersion = getSelectedSceneVersion();
  const target = narration.currentNarrationSceneId
    ? getSceneAndVersion(
        narration.currentNarrationSceneId,
        narration.currentNarrationVersionId,
        narration.currentNarrationSessionId,
      )
    : { scene: null, version: selectedVersion };
  const narrationSessionId = narration.currentNarrationSessionId || activeSessionId;
  const narrationScenes = narrationSessionId ? scenesBySession[narrationSessionId] || [] : [];
  const sceneNumber = target.scene ? narrationScenes.findIndex((scene) => scene.id === target.scene.id) + 1 : 0;
  const versionNumber = target.version?.version_index || target.scene?.active_version_index || null;
  const requestedProvider = narration.provider || ttsSettings.tts_provider;
  const activeProvider = narration.lastProviderUsed || requestedProvider;
  const needsUserResume = Boolean(narration.needsUserResume);
  const isBuffering = Boolean(narration.isBuffering);
  const isPlaying = narration.isNarrating && !narration.isPaused && !needsUserResume && !isBuffering;
  const isPaused = narration.isNarrating && (narration.isPaused || needsUserResume);
  const canReplay = Boolean(narration.currentNarrationSceneId || selectedVersion?.generated_text?.trim());
  const canPlay = isPlaying || isPaused || needsUserResume || canReplay;
  const timeLabel = `${formatTime(narration.elapsedTime)} / ${narration.duration ? formatTime(narration.duration) : "--:--"}`;
  const bufferedStart = Math.max(0, Number(narration.bufferedStartTime) || 0);
  const bufferedEnd = Math.max(bufferedStart, Number(narration.bufferedEndTime) || 0);
  const canSeek = bufferedEnd > bufferedStart;
  const effectiveVoice = voiceLabel(
    narration.lastVoiceUsed || narration.voiceDisplayName || narration.voice || ttsSettings.tts_voice || (activeProvider === "browser" ? "Default Browser Voice" : "af_heart"),
  );
  const engineLabel = activeProvider === "high_quality_local"
    ? qwenModelLabel(narration.lastModelUsed)
    : requestedProvider === "high_quality_local" && activeProvider === "kokoro"
      ? "Kokoro fallback"
      : providerLabel(activeProvider);
  const effectiveLabel = `${engineLabel} · ${effectiveVoice}`;
  const narrationStory = sessions.find((session) => session.id === narrationSessionId);
  const storyPrefix = narrationSessionId && narrationSessionId !== activeSessionId && narrationStory
    ? `${narrationStory.title} · `
    : "";
  const targetLabel = sceneNumber
    ? `${storyPrefix}Scene ${sceneNumber}${versionNumber ? ` - Version ${versionNumber}` : ""}`
    : "Narrating selected scene";
  const statusLabel = (() => {
    if (needsUserResume) return "Tap play to continue narration";
    if (isBuffering) return "Buffering...";
    if (narration.statusMessage?.toLowerCase().includes("preparing") && requestedProvider === "high_quality_local") {
      return narration.statusMessage || "Preparing Qwen3-TTS 0.6B";
    }
    if (narration.statusMessage?.toLowerCase().includes("preparing")) return narration.statusMessage;
    if (narration.statusMessage?.toLowerCase().includes("cached")) return "Using cached narration...";
    if (isPaused) return "Paused";
    if (isPlaying) return "Narrating";
    return narration.statusMessage || "Ready";
  })();
  const followLabel = followModeLabel(ttsSettings);

  if (!narration.isOpen) {
    return null;
  }

  const speedControl = (
    <select
      aria-label="Narration speed"
      className={`${menuField} w-full sm:w-20`}
      onChange={(event) => setNarrationSpeed(event.target.value)}
      value={narration.speed}
    >
      {ttsController.SPEED_OPTIONS.map((speed) => (
        <option key={speed} value={speed}>
          {speedLabel(speed)}
        </option>
      ))}
    </select>
  );

  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      className="sd-mini-player-strip shrink-0 bg-ink/88 px-3 pb-1.5 pt-2 backdrop-blur md:px-6"
      exit={{ opacity: 0, y: 12 }}
      initial={{ opacity: 0, y: 12 }}
    >
      <div className="sd-mini-player-inner mx-auto w-full">
        <div className="sd-mini-player rounded-xl border border-line/70 bg-panel/94 px-3 py-2 shadow-[0_12px_36px_rgba(0,0,0,0.28)]">
          <div className="flex min-w-0 items-center gap-2 md:grid md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:gap-3">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-zinc-100">{targetLabel}</div>
              <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-muted">
                <span
                  aria-hidden="true"
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    isPlaying ? "bg-moss" : isPaused || needsUserResume ? "bg-zinc-500" : isBuffering ? "bg-tide animate-pulse" : "bg-tide"
                  }`}
                />
                <span className="truncate">{statusLabel}</span>
                {followLabel ? (
                  <>
                    <span className="hidden h-1 w-1 shrink-0 rounded-full bg-zinc-700 sm:inline" />
                    <span className="hidden truncate text-tide/90 sm:inline">{followLabel}</span>
                  </>
                ) : null}
                <span className="hidden h-1 w-1 shrink-0 rounded-full bg-zinc-700 sm:inline" />
                <span className="hidden truncate sm:inline" title={effectiveLabel}>{effectiveLabel}</span>
              </div>
            </div>

            <div className="flex shrink-0 items-center justify-center gap-1">
              <button
                aria-label="Back 10 seconds"
                className={transportButton}
                disabled={!narration.isNarrating || needsUserResume}
                onClick={() => skipNarration(-10)}
                title="Back 10 seconds"
                type="button"
              >
                <Rewind size={15} />
              </button>
              <motion.button
                aria-label={isPlaying ? "Pause narration" : "Play narration"}
                className="sd-button-primary grid h-10 w-10 shrink-0 place-items-center rounded-full bg-zinc-100 text-zinc-950 shadow-sm transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!canPlay}
                onClick={isPlaying ? pauseNarration : playNarration}
                title={isPlaying ? "Pause narration" : "Play narration"}
                type="button"
                whileTap={{ scale: 0.96 }}
              >
                {isPlaying ? <Pause size={17} /> : <Play size={17} className="ml-0.5" />}
              </motion.button>
              <button
                aria-label="Stop narration"
                className={`${transportButton} border-transparent bg-transparent text-zinc-500 hover:border-line/80 hover:bg-[#101216]/90 hover:text-zinc-200`}
                disabled={!narration.isNarrating && !narration.isPaused && !narration.isOpen}
                onClick={stopNarration}
                title="Stop narration"
                type="button"
              >
                <Square size={14} />
              </button>
              <button
                aria-label="Forward 10 seconds"
                className={transportButton}
                disabled={!narration.isNarrating || needsUserResume}
                onClick={() => skipNarration(10)}
                title="Forward 10 seconds"
                type="button"
              >
                <FastForward size={15} />
              </button>
            </div>

            <div className="hidden min-w-0 items-center justify-end gap-2 md:flex">
              <span className="shrink-0 text-xs tabular-nums text-zinc-300">{timeLabel}</span>
              {speedControl}
              <span className="max-w-52 truncate text-xs text-muted" title={`Effective narration: ${effectiveLabel}`}>{effectiveLabel}</span>
            </div>

            <button
              aria-controls="narration-mini-player-options"
              aria-expanded={optionsOpen}
              aria-label={optionsOpen ? "Hide TTS options" : "Narration options"}
              className="sd-transport-button grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line/70 bg-[#101216]/90 text-zinc-300 md:hidden"
              onClick={() => setOptionsOpen((open) => !open)}
              title={optionsOpen ? "Hide TTS options" : "Narration options"}
              type="button"
            >
              {optionsOpen ? <ChevronUp size={15} /> : <SlidersHorizontal size={15} />}
            </button>
          </div>

          <div className="mt-2 flex min-w-0 items-center gap-2">
            <input
              aria-label="Narration position"
              className="sd-narration-scrubber min-w-0 flex-1 accent-tide"
              disabled={!canSeek}
              max={canSeek ? bufferedEnd : 1}
              min={canSeek ? bufferedStart : 0}
              onChange={(event) => seekNarration(Number(event.target.value))}
              step="0.05"
              title={canSeek ? `Seek through ${formatTime(bufferedEnd)} of generated audio` : "Audio is still being generated"}
              type="range"
              value={canSeek ? Math.min(bufferedEnd, Math.max(bufferedStart, narration.elapsedTime || 0)) : 0}
            />
            <span className="shrink-0 text-xs tabular-nums text-zinc-300 md:hidden">{timeLabel}</span>
          </div>

          <div className="mt-1 hidden items-center justify-between gap-3 text-[11px] text-muted md:flex">
            <span>{formatTime(bufferedEnd)} generated</span>
            <span>{narration.chunkCount ? `${narration.generatedChunkCount || 0}/${narration.chunkCount} chunks` : "Preparing audio"}</span>
          </div>

          {optionsOpen ? (
            <div
              className="sd-modal mt-2 grid min-w-0 gap-2 rounded-lg border border-line bg-panel/98 p-3 shadow-glow md:hidden"
              id="narration-mini-player-options"
            >
              <button
                className="sd-transport-button flex min-h-11 w-full items-center justify-between rounded-lg border border-line/70 bg-[#101216]/90 px-3 text-left text-xs font-semibold text-zinc-200"
                onClick={() => setOptionsOpen(false)}
                type="button"
              >
                Hide TTS options
                <ChevronUp size={15} />
              </button>
              <label className="grid min-w-0 gap-1 text-[11px] uppercase tracking-[0.14em] text-muted">
                Speed
                {speedControl}
              </label>
              <label className="grid min-w-0 gap-1 text-[11px] uppercase tracking-[0.14em] text-muted">
                Effective voice
                <span className="safe-wrap text-xs normal-case text-zinc-200">{effectiveVoice}</span>
              </label>
            </div>
          ) : null}
        </div>
      </div>
    </motion.div>
  );
}
