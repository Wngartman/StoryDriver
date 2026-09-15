import { motion } from "framer-motion";
import { ChevronLeft, ChevronRight, Copy, FileText, MessageSquarePlus, Mic2, PenLine, RefreshCw } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { buildTtsFollowPlan, normalizeTtsFollowMode } from "../services/ttsFollowPlan.js";
import { useAppStore } from "../store/useAppStore.js";
import BrandMark from "./BrandMark.jsx";

const EMPTY_NARRATION = Object.freeze({
  currentNarrationSceneId: null,
  currentNarrationVersionId: null,
  duration: 0,
  elapsedTime: 0,
  isNarrating: false,
  narrationCursor: null,
});

function proseParagraphs(text) {
  return text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

function activeFollowUnitIds(plan, narration, mode) {
  if (!plan?.units?.length || mode === "off") return new Set();
  if (mode === "exact_word") {
    const cursor = narration.narrationCursor || {};
    const timestamps = Array.isArray(cursor.wordTimestamps) ? cursor.wordTimestamps : [];
    const chunkElapsed = Number(cursor.chunkElapsed);
    if (!timestamps.length || !Number.isFinite(chunkElapsed)) return new Set();
    const activeWord = timestamps.find((word) => {
      const start = Number(word.start);
      const end = Number(word.end);
      return Number.isFinite(start) && Number.isFinite(end) && chunkElapsed >= start && chunkElapsed <= end;
    });
    if (!activeWord) return -1;
    const absoluteStart = Number(cursor.textStart) + Number(activeWord.textStart || 0);
    const absoluteEnd = Number(cursor.textStart) + Number(activeWord.textEnd || activeWord.textStart || 0);
    const unit = plan.units.find((candidate) => candidate.end > absoluteStart && candidate.start < absoluteEnd);
    return unit ? new Set([unit.id]) : new Set();
  }
  const cursor = narration.narrationCursor || {};
  const chunkStart = Number(cursor.textStart);
  const chunkEnd = Number(cursor.textEnd);
  const hasChunkRange = Number.isFinite(chunkStart) && Number.isFinite(chunkEnd) && chunkEnd > chunkStart;
  let candidateUnits = hasChunkRange
    ? plan.units.filter((unit) => unit.end > chunkStart && unit.start < chunkEnd)
    : plan.units;
  if (!candidateUnits.length) candidateUnits = plan.units;

  if (mode !== "word_estimate" && cursor.rangeIsFollowChunk && hasChunkRange) {
    return new Set(candidateUnits.map((unit) => unit.id));
  }

  let progress = 0;
  const chunkElapsed = Number(cursor.chunkElapsed);
  const chunkDuration = Number(cursor.chunkDuration);
  if (Number.isFinite(chunkElapsed) && Number.isFinite(chunkDuration) && chunkDuration > 0) {
    progress = chunkElapsed / chunkDuration;
  } else if (Number.isFinite(narration.elapsedTime) && Number.isFinite(narration.duration) && narration.duration > 0) {
    progress = narration.elapsedTime / narration.duration;
  } else if (Number.isFinite(cursor.chunkIndex) && Number.isFinite(cursor.chunkCount) && cursor.chunkCount > 0) {
    progress = cursor.chunkIndex / cursor.chunkCount;
  }
  progress = Math.min(0.999, Math.max(0, progress));
  const totalWeight = candidateUnits.reduce((sum, unit) => sum + unit.weight, 0) || candidateUnits.length;
  const target = totalWeight * progress;
  let running = 0;
  for (const unit of candidateUnits) {
    running += unit.weight || 1;
    if (running >= target) return new Set([unit.id]);
  }
  const fallback = candidateUnits.at(-1);
  return fallback ? new Set([fallback.id]) : new Set();
}

function writingLabel(mode) {
  if (mode === "regenerate") return "Writing alternate version...";
  if (mode === "rewrite") return "Rewriting scene...";
  if (mode === "revise") return "Revising scene...";
  return "Writing scene...";
}

function stageLabel(stage, fallback) {
  return (
    {
      preparing_context: "Preparing context",
      preparing_prompt: "Preparing prompt",
      planning_scene: "Planning scene",
      waiting_model: "Waiting for model",
      thinking: "Model is thinking",
      writing_scene: "Writing scene",
      checking_continuity: "Checking continuity",
      refining_scene: "Refining scene",
      saving_scene: "Saving scene",
      failed: "Generation failed",
      ready: "Ready",
    }[stage] ||
    fallback ||
    "Writing scene"
  );
}

const sceneActionButton =
  "sd-action-button inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-line bg-panelSoft px-3 py-1.5 text-sm text-zinc-200 transition hover:border-zinc-600 hover:bg-[#1d2028] disabled:cursor-not-allowed disabled:opacity-45";
const sceneGhostButton =
  "sd-action-button sd-action-button-ghost inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-line bg-transparent px-3 py-1.5 text-sm text-muted transition hover:border-zinc-600 hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-45";

function DirectorNoteBlock({ note, mode = "continue", error = "" }) {
  if (!note?.trim() && !error) return null;
  return (
    <div className={`sd-director-note mb-5 rounded-xl border px-4 py-3 text-sm leading-6 ${
      error ? "border-ember/30 bg-ember/10 text-ember" : "border-line/80 bg-[#0d0e11]/55 text-muted"
    }`}>
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-wide">
        <span className={error ? "text-ember" : "text-tide"}>Director note</span>
        {mode && mode !== "continue" ? (
          <span className="sd-chip rounded-full border border-line px-2 py-0.5 text-[10px] text-muted">
            {mode}
          </span>
        ) : null}
      </div>
      {note?.trim() ? <div className="safe-wrap text-zinc-300">{note}</div> : null}
      {error ? <div className="mt-2 safe-wrap text-xs leading-5">{error}</div> : null}
    </div>
  );
}

function GenerationStatsLine({ stats }) {
  const tps = Number(stats.tokens_per_second);
  if (!Number.isFinite(tps) || tps <= 0) return null;
  return (
    <div className="sd-generation-stats mt-5 border-t border-line/45 pt-3 text-[11px] tabular-nums text-zinc-500">
      {tps.toFixed(1)} tok/s
    </div>
  );
}

function StreamingText({ scene, label = scene.status_message || writingLabel(scene.mode) }) {
  const hasText = Boolean(scene.generated_text?.trim());
  const failed = scene.stage === "failed";
  const displayLabel = hasText ? stageLabel(scene.stage, writingLabel(scene.mode)) : stageLabel(scene.stage, label);
  return (
    <div>
      <div className={`mb-4 flex items-center gap-3 text-sm ${failed ? "text-ember" : "text-moss"}`}>
        <div className={`h-2.5 w-2.5 rounded-full ${failed ? "bg-ember" : "animate-pulse bg-moss"}`} />
        <span>{displayLabel}</span>
        {hasText ? <span className="text-xs text-muted">{scene.generated_text.length.toLocaleString()} chars</span> : null}
      </div>
      {scene.generated_text ? (
        <div className="sd-prose-panel story-prose space-y-5 font-story">
          {proseParagraphs(scene.generated_text).map((paragraph, index) => (
            <p key={`${paragraph.slice(0, 18)}-${index}`}>{paragraph}</p>
          ))}
        </div>
      ) : (
        <div className="sd-empty-state h-28 rounded-lg border border-line bg-[#0d0e11]/65" />
      )}
    </div>
  );
}

function StreamingBlock({ scene }) {
  const generateScene = useAppStore((state) => state.generateScene);
  const failed = scene.stage === "failed";
  return (
    <motion.article
      animate={{ opacity: 1, y: 0 }}
      className={`sd-scene-card rounded-xl border bg-panel/86 p-4 shadow-glow sm:p-5 md:p-6 ${failed ? "border-ember/30" : "border-moss/25"}`}
      initial={{ opacity: 0, y: 12 }}
    >
      <DirectorNoteBlock error={scene.error || ""} mode={scene.mode} note={scene.director_note} />
      <StreamingText scene={scene} />
      {failed ? (
        <button
          className={`${sceneActionButton} mt-4`}
          onClick={() =>
            generateScene({
              sessionId: scene.session_id,
              directorNote: scene.director_note,
              mode: scene.mode || "continue",
              targetSceneId: scene.target_scene_id || null,
            }).catch(() => {})
          }
          type="button"
        >
          <RefreshCw size={15} />
          Retry
        </button>
      ) : null}
    </motion.article>
  );
}

function HighlightedParagraph({ paragraphPlan, activeUnitIds = new Set(), mode = "phrase" }) {
  if (!paragraphPlan?.segments?.length || !activeUnitIds?.size) {
    return <>{paragraphPlan?.paragraph || ""}</>;
  }
  const markClass =
    mode === "word_estimate"
      ? "tts-follow-mark tts-follow-mark-word-estimate"
      : mode === "phrase"
        ? "tts-follow-mark tts-follow-mark-phrase"
        : "tts-follow-mark";
  return (
    <>
      {paragraphPlan.segments.map((segment, index) => {
        const isHighlighted = segment.unitId !== null && activeUnitIds.has(segment.unitId);
        return isHighlighted ? (
          <mark className={markClass} key={`${segment.unitId}-${index}`}>
            {segment.text}
          </mark>
        ) : (
          <span key={`${segment.text.slice(0, 8)}-${index}`}>{segment.text}</span>
        );
      })}
    </>
  );
}

const SceneCard = memo(function SceneCard({ scene, index }) {
  const {
    generateScene,
    addQualityNote,
    isGenerating,
    narrateSceneVersion,
    narration,
    selectScene,
    isSelectedScene,
    selectedVersionId,
    selectedIndexOverride,
    setSceneVersion,
    streamingScene,
    ttsSettings,
  } = useAppStore(useShallow((state) => ({
    generateScene: state.generateScene,
    addQualityNote: state.addQualityNote,
    isGenerating: state.isGenerating,
    narrateSceneVersion: state.narrateSceneVersion,
    narration: state.narration.currentNarrationSceneId === scene.id ? state.narration : EMPTY_NARRATION,
    selectScene: state.selectScene,
    isSelectedScene: state.selectedSceneId === scene.id,
    selectedVersionId: state.selectedSceneId === scene.id ? state.selectedVersionId : null,
    selectedIndexOverride: state.selectedVersions[scene.id],
    setSceneVersion: state.setSceneVersion,
    streamingScene: state.streamingScene?.target_scene_id === scene.id ? state.streamingScene : null,
    ttsSettings: state.ttsSettings,
  })));
  const [inlineMode, setInlineMode] = useState(null);
  const [instruction, setInstruction] = useState("");

  const versions = scene.versions?.length
    ? scene.versions
    : [
        {
          id: scene.active_version_id || scene.id,
          scene_id: scene.id,
          session_id: scene.session_id,
          director_note: scene.director_note,
          generated_text: scene.generated_text,
          mode: scene.mode,
          version_index: 1,
          created_at: scene.created_at,
        },
      ];
  const selectedIndex = selectedIndexOverride || scene.active_version_index || versions.length;
  const visibleVersion = versions.find((version) => version.version_index === selectedIndex) || versions.at(-1);
  const hasVersions = versions.length > 1;
  const isTargetGenerating =
    streamingScene?.session_id === scene.session_id && streamingScene?.target_scene_id === scene.id;
  const isSelectedVersion = isSelectedScene && selectedVersionId === visibleVersion?.id;
  const isNarratingVisibleVersion =
    narration.currentNarrationSceneId === scene.id &&
    narration.currentNarrationVersionId === visibleVersion?.id &&
    narration.isNarrating;
  const followHighlightMode = normalizeTtsFollowMode(ttsSettings);
  const exactWordAvailable = Array.isArray(narration.narrationCursor?.wordTimestamps) && narration.narrationCursor.wordTimestamps.length > 0;
  const shouldFollowHighlight =
    followHighlightMode !== "off" &&
    (followHighlightMode !== "exact_word" || exactWordAvailable) &&
    isNarratingVisibleVersion;
  const proseText = visibleVersion?.generated_text || "";
  const plainParagraphs = useMemo(() => proseParagraphs(proseText), [proseText]);
  const followPlan = useMemo(
    () => (shouldFollowHighlight ? buildTtsFollowPlan(proseText, followHighlightMode === "exact_word" ? "word_estimate" : followHighlightMode) : null),
    [followHighlightMode, proseText, shouldFollowHighlight],
  );
  const activeFollowUnits = useMemo(() => {
    if (!shouldFollowHighlight || !followPlan?.units?.length) return new Set();
    return activeFollowUnitIds(followPlan, narration, followHighlightMode);
  }, [
    followHighlightMode,
    followPlan,
    narration.duration,
    narration.elapsedTime,
    narration.narrationCursor?.chunkDuration,
    narration.narrationCursor?.chunkElapsed,
    narration.narrationCursor?.chunkCount,
    narration.narrationCursor?.chunkIndex,
    narration.narrationCursor?.rangeIsFollowChunk,
    narration.narrationCursor?.textEnd,
    narration.narrationCursor?.textStart,
    narration.narrationCursor?.wordTimestamps,
    shouldFollowHighlight,
  ]);
  const moveVersion = (direction) => {
    const next = Math.min(Math.max(selectedIndex + direction, 1), versions.length);
    setSceneVersion(scene.id, next);
  };

  const submitInline = async () => {
    const trimmed = instruction.trim();
    if (!inlineMode || isGenerating) return;
    await generateScene({
      sessionId: scene.session_id,
      directorNote:
        trimmed ||
        (inlineMode === "rewrite"
          ? "Rewrite this scene while preserving the same core story events."
          : "Revise this scene while preserving continuity."),
      mode: inlineMode,
      targetSceneId: scene.id,
    });
    setInstruction("");
    setInlineMode(null);
  };

  const regenerate = () =>
    generateScene({
      sessionId: scene.session_id,
      directorNote: "",
      mode: "regenerate",
      targetSceneId: scene.id,
    });

  const quickAddSceneNote = async () => {
    const note = window.prompt("Add a local QA note for this scene/version:");
    if (!note?.trim()) return;
    await addQualityNote(scene.session_id, {
      note_type: "writing",
      severity: "medium",
      note_text: note.trim(),
      scene_id: scene.id,
      version_id: visibleVersion?.id || null,
    }).catch(() => {});
  };

  return (
    <>
      <motion.article
        animate={{ opacity: 1, y: 0 }}
        className={`sd-scene-card rounded-xl border p-4 shadow-glow transition sm:p-5 md:p-6 ${
          isNarratingVisibleVersion
              ? "sd-scene-card-selected border-tide/45 bg-panel/95 ring-1 ring-tide/15"
              : isSelectedVersion
            ? "sd-scene-card-selected border-tide/45 bg-panel/95 ring-1 ring-tide/10"
            : "border-line bg-panel/84 hover:border-zinc-700"
        }`}
        id={`scene-${scene.id}`}
        initial={{ opacity: 0, y: 12 }}
        key={scene.id}
        onClick={() => selectScene(scene.id, visibleVersion?.id)}
        transition={{ delay: index * 0.035 }}
      >
        <div className="mb-4 flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted">
          <span className="sd-chip rounded-full border border-line/80 bg-[#0d0e11]/45 px-2 py-0.5 text-zinc-400">
            Scene {index + 1} · V{selectedIndex}/{versions.length}
          </span>
          {isSelectedVersion ? (
            <span className="sd-chip sd-chip-selected rounded-full border border-tide/30 bg-tide/10 px-2 py-0.5 text-tide/90">
              Selected
            </span>
          ) : null}
          {isSelectedScene && !isSelectedVersion ? (
            <span className="sd-chip sd-chip-selected rounded-full border border-tide/20 bg-tide/5 px-2 py-0.5 text-tide/80">
              Selected scene
            </span>
          ) : null}
          {isNarratingVisibleVersion ? (
            <span className="sd-chip sd-chip-selected rounded-full border border-tide/25 bg-tide/10 px-2 py-0.5 text-tide/90">
              Narration target
            </span>
          ) : null}
        </div>

        <DirectorNoteBlock
          error={isTargetGenerating && streamingScene?.stage === "failed" ? streamingScene.error || "" : ""}
          mode={isTargetGenerating ? streamingScene?.mode : visibleVersion.mode}
          note={isTargetGenerating ? streamingScene?.director_note : visibleVersion.director_note}
        />

        {isTargetGenerating ? (
          <>
            <StreamingText scene={streamingScene} />
            {streamingScene.stage === "failed" ? (
              <button
                className={`${sceneActionButton} mt-4`}
                onClick={() =>
                  generateScene({
                    sessionId: streamingScene.session_id,
                    directorNote: streamingScene.director_note,
                    mode: streamingScene.mode || "revise",
                    targetSceneId: streamingScene.target_scene_id || scene.id,
                  }).catch(() => {})
                }
                type="button"
              >
                <RefreshCw size={15} />
                Retry
              </button>
            ) : null}
          </>
        ) : (
          <>
            <div className="sd-prose-panel story-prose space-y-5 font-story">
              {followPlan
                ? followPlan.paragraphs.map((paragraphPlan, paragraphIndex) => (
                    <p key={`${visibleVersion.id}-${paragraphIndex}`}>
                      <HighlightedParagraph
                        activeUnitIds={activeFollowUnits}
                        mode={followHighlightMode}
                        paragraphPlan={paragraphPlan}
                      />
                    </p>
                  ))
                : plainParagraphs.map((paragraph, paragraphIndex) => (
                    <p key={`${visibleVersion.id}-${paragraphIndex}`}>{paragraph}</p>
                  ))}
            </div>
            <GenerationStatsLine stats={visibleVersion.generation_stats || scene.generation_stats} />
          </>
        )}

        <div
          className="sd-action-row mt-6 flex flex-col gap-3 border-t border-line/70 pt-4 sm:flex-row sm:items-center sm:justify-between"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex flex-wrap items-center gap-2">
            <button
              className={sceneActionButton}
              disabled={isGenerating}
              onClick={regenerate}
              type="button"
            >
              <RefreshCw className={isTargetGenerating ? "animate-spin" : ""} size={15} />
              {isTargetGenerating ? "Regenerating..." : "Regenerate"}
            </button>
            <button
              className={sceneGhostButton}
              disabled={isGenerating}
              onClick={() => setInlineMode(inlineMode === "rewrite" ? null : "rewrite")}
              type="button"
            >
              <RefreshCw size={15} />
              Rewrite
            </button>
            <button
              className={sceneGhostButton}
              disabled={isGenerating}
              onClick={() => setInlineMode(inlineMode === "revise" ? null : "revise")}
              type="button"
            >
              <PenLine size={15} />
              Revise
            </button>
            <button
              className={`sd-action-button inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-45 ${
                isNarratingVisibleVersion
                  ? "border-moss/35 bg-moss/10 text-moss"
                  : "border-line bg-transparent text-muted"
              }`}
              disabled={isGenerating || isTargetGenerating || isNarratingVisibleVersion}
              onClick={() => narrateSceneVersion(scene.id, visibleVersion?.id)}
              title="Narrate this scene version"
              type="button"
            >
              <Mic2 size={15} />
              {isNarratingVisibleVersion ? "Narrating" : "Narrate"}
            </button>
            <button
              className={sceneGhostButton}
              disabled={isTargetGenerating || !visibleVersion.generated_text?.trim()}
              onClick={() => navigator.clipboard?.writeText(visibleVersion.generated_text)}
              title="Copy scene version"
              type="button"
            >
              <Copy size={15} />
              Copy
            </button>
            <button
              className={sceneGhostButton}
              disabled={!visibleVersion?.id}
              onClick={quickAddSceneNote}
              title="Add local QA note for this scene"
              type="button"
            >
              <MessageSquarePlus size={15} />
              Note
            </button>
          </div>

          {hasVersions ? (
            <div className="flex shrink-0 items-center gap-2 text-xs text-muted">
              <button
                aria-label="Previous version"
                className="sd-icon-button grid h-9 w-9 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200 disabled:opacity-40"
                disabled={isGenerating || selectedIndex <= 1}
                onClick={() => moveVersion(-1)}
                type="button"
              >
                <ChevronLeft size={16} />
              </button>
              <span className="min-w-24 text-center tabular-nums">
                Version {selectedIndex} of {versions.length}
              </span>
              <button
                aria-label="Next version"
                className="sd-icon-button grid h-9 w-9 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200 disabled:opacity-40"
                disabled={isGenerating || selectedIndex >= versions.length}
                onClick={() => moveVersion(1)}
                type="button"
              >
                <ChevronRight size={16} />
              </button>
            </div>
          ) : null}
        </div>

        {inlineMode ? (
          <div className="sd-side-panel-card mt-4 rounded-lg border border-line bg-[#0d0e11] p-2" onClick={(event) => event.stopPropagation()}>
            <textarea
              className="sd-composer-input min-h-20 w-full resize-none rounded-lg bg-transparent px-3 py-2 text-base leading-6 text-zinc-100 placeholder:text-muted sm:text-sm"
              onChange={(event) => setInstruction(event.target.value)}
              placeholder={inlineMode === "rewrite" ? "Rewrite instruction..." : "Revision instruction..."}
              value={instruction}
            />
            <div className="flex justify-end gap-2 border-t border-line pt-2">
              <button
                className="sd-action-button sd-action-button-ghost rounded-lg border border-line px-3 py-2 text-sm text-muted"
                onClick={() => {
                  setInlineMode(null);
                  setInstruction("");
                }}
                type="button"
              >
                Cancel
              </button>
              <button
                className="sd-button-primary rounded-lg bg-zinc-100 px-3 py-2 text-sm font-semibold text-zinc-950"
                disabled={isGenerating}
                onClick={submitInline}
                type="button"
              >
                {inlineMode === "rewrite" ? "Rewrite" : "Revise"}
              </button>
            </div>
          </div>
        ) : null}

      </motion.article>

    </>
  );
});

function StoryFeed({ activeSession, scenes }) {
  const selectedSceneId = useAppStore((state) => state.selectedSceneId);
  const selectScene = useAppStore((state) => state.selectScene);
  const streamingScene = useAppStore((state) => (
    state.streamingScene?.session_id === activeSession?.id && !state.streamingScene?.target_scene_id
      ? state.streamingScene
      : null
  ));
  const endRef = useRef(null);
  const streamingTextLength = streamingScene?.generated_text?.length || 0;

  useEffect(() => {
    if (!activeSession) return;
    const timeout = window.setTimeout(() => {
      const selectedElement = selectedSceneId ? document.getElementById(`scene-${selectedSceneId}`) : null;
      (selectedElement || endRef.current)?.scrollIntoView({
        behavior: streamingTextLength ? "auto" : "smooth",
        block: "end",
      });
    }, 80);
    return () => window.clearTimeout(timeout);
  }, [activeSession?.id, scenes.length, selectedSceneId, streamingTextLength]);

  useEffect(() => {
    if (!activeSession || !scenes.length || selectedSceneId) return;
    const latest = scenes.at(-1);
    selectScene(latest.id, latest.versions?.at(-1)?.id || latest.active_version_id);
  }, [activeSession, scenes, selectScene, selectedSceneId]);

  const continueStreaming = useMemo(
    () => streamingScene && streamingScene.session_id === activeSession?.id && !streamingScene.target_scene_id,
    [activeSession?.id, streamingScene],
  );

  return (
    <div className="story-scrollbar min-h-0 flex-1 overflow-y-auto">
      <div className="sd-feed-inner mx-auto flex w-full max-w-4xl flex-col gap-5 px-4 py-5 sm:py-6 md:px-8">
        {!activeSession ? (
          <motion.div
            animate={{ opacity: 1, y: 0 }}
            className="sd-empty-state mt-12 py-7"
            initial={{ opacity: 0, y: 12 }}
          >
            <BrandMark className="mb-5" label="StoryDriver emblem" size="lg" />
            <h2 className="text-2xl font-semibold text-zinc-100">StoryDriver</h2>
          </motion.div>
        ) : null}

        {activeSession && scenes.length === 0 && !continueStreaming ? (
          <motion.div
            animate={{ opacity: 1, y: 0 }}
            className="sd-empty-state mt-6 py-6"
            initial={{ opacity: 0, y: 12 }}
          >
            <div className="mb-5 flex items-center gap-3">
              <div className="sd-chip-selected grid h-10 w-10 place-items-center rounded-lg border border-line bg-panelSoft text-moss">
                <FileText size={18} />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-zinc-100">No scenes yet.</h2>
                <p className="text-sm text-muted">Write the opening director note below, then press Continue.</p>
              </div>
            </div>
          </motion.div>
        ) : null}

        {scenes.map((scene, index) => (
          <SceneCard index={index} key={scene.id} scene={scene} />
        ))}

        {continueStreaming ? <StreamingBlock scene={streamingScene} /> : null}
        <div ref={endRef} />
      </div>
    </div>
  );
}

export default memo(StoryFeed);
