import { Mic2, Volume2 } from "lucide-react";
import { useAppStore } from "../store/useAppStore.js";

function formatTime(seconds = 0) {
  const safeSeconds = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function providerLabel(provider) {
  if (provider === "high_quality_local") return "Qwen3-TTS 0.6B";
  if (provider === "kokoro") return "Kokoro";
  return "Browser";
}

export default function TTSControls() {
  const {
    getSelectedSceneVersion,
    narration,
    ttsSettings,
  } = useAppStore();
  const selectedVersion = getSelectedSceneVersion();
  const hasSelectedText = Boolean(selectedVersion?.generated_text?.trim());
  const activeProvider = narration.isNarrating ? narration.provider : ttsSettings.tts_provider;
  const voiceLabel =
    narration.voice || ttsSettings.tts_voice || (activeProvider === "browser" ? "Default Browser Voice" : "af_heart");
  const statusLabel = narration.needsUserResume
    ? "Tap play to continue narration"
    : narration.isBuffering
      ? "Buffering narration..."
      : narration.isNarrating
        ? narration.isPaused
          ? "Playback paused"
          : "Narration playing"
        : narration.isOpen
          ? "Narration ready"
          : hasSelectedText
            ? "Use a scene Narrate button to start playback"
            : "Select a scene to prepare narration";

  return (
    <section className="sd-side-panel-card rounded-xl border border-line/75 bg-panel/70 p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Mic2 size={17} className="text-tide" />
            Narration
          </div>
          <p className="mt-1 text-xs text-muted">
            {statusLabel}
          </p>
        </div>
        <span className="sd-chip rounded-full border border-line/80 bg-[#0d0e11]/60 px-2 py-0.5 text-xs text-muted">
          {activeProvider === "browser" ? "Browser fallback" : `${providerLabel(activeProvider)} active`}
        </span>
      </div>
      {activeProvider === "browser" ? (
        <p className="mb-3 rounded-lg border border-ember/25 bg-ember/10 px-3 py-2 text-xs leading-5 text-ember">
          Browser TTS active. Browser voices may sound robotic; use Kokoro for better local narration.
        </p>
      ) : null}
      <div className="rounded-lg border border-line/70 bg-[#0d0e11]/55 px-3 py-2 text-xs text-muted">
        Effective voice: <span className="text-zinc-300">{voiceLabel}</span>
      </div>
      <div className="sd-chip mt-3 rounded-lg border border-line/70 bg-[#0d0e11]/55 px-3 py-2 text-xs leading-5 text-muted">
        <div className="flex items-center gap-2 text-zinc-300">
          <Volume2 size={13} className="text-tide" />
          Scene buttons start narration. The bottom player handles playback.
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 text-xs text-muted">
        <span className="truncate">
          Voice: {voiceLabel}
        </span>
        {narration.isOpen ? (
          <span className="shrink-0 tabular-nums">
          {formatTime(narration.elapsedTime)}
          {narration.duration ? ` / ${formatTime(narration.duration)}` : ""}
          </span>
      ) : null}
      </div>
    </section>
  );
}
