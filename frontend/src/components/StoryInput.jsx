import { motion } from "framer-motion";
import { Send } from "lucide-react";
import { memo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useAppStore } from "../store/useAppStore.js";

function StoryInput({ activeSession, onCreateSession }) {
  const [note, setNote] = useState("");
  const textareaRef = useRef(null);
  const {
    generateScene,
    isGenerating,
  } = useAppStore(useShallow((state) => ({
    generateScene: state.generateScene,
    isGenerating: state.isGenerating,
  })));
  const disabled = !activeSession || isGenerating;
  const hasDirectorNote = Boolean(note.trim());
  const inputPlaceholder = !activeSession
    ? "Create or choose a story first..."
    : "Direct the next scene...";
  const sessionHint = !activeSession
    ? "Create a story to start a local writing session."
    : isGenerating
      ? "Writing scene..."
      : "";

  const submitGeneration = async () => {
    if (!activeSession || isGenerating) return;
    const directorNote = note.trim();
    if (!directorNote) return;

    setNote("");
    const shouldCollapseMobile =
      typeof window !== "undefined" && window.matchMedia?.("(max-width: 767px)").matches;
    if (shouldCollapseMobile) {
      textareaRef.current?.blur();
    } else {
      window.setTimeout(() => textareaRef.current?.focus(), 0);
    }
    try {
      await generateScene({ sessionId: activeSession.id, directorNote, mode: "continue" });
    } catch {
      // The failed director note remains in the feed with a retry action.
    }
  };

  const handleKeyDown = (event) => {
    if (event.nativeEvent?.isComposing || event.isComposing) return;
    if (event.key !== "Enter") return;
    if (event.shiftKey) return;
    event.preventDefault();
    if (!note.trim() || disabled) return;
    submitGeneration();
  };

  return (
    <div
      className="sd-composer-strip shrink-0 border-t border-line/60 bg-ink/92 px-3 py-2.5 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] backdrop-blur md:px-6 md:pb-3"
    >
      <div className="sd-composer-inner mx-auto w-full">
        <div className="sd-composer-shell rounded-xl border border-line/75 bg-panel/95 p-2 shadow-[0_16px_42px_rgba(0,0,0,0.30)] sm:p-3">
          <textarea
            ref={textareaRef}
            className="sd-composer-input max-h-48 min-h-16 w-full resize-none rounded-lg border border-transparent bg-transparent px-3 py-3 text-base leading-6 text-zinc-100 placeholder:text-muted outline-none sm:min-h-20 sm:text-sm"
            disabled={!activeSession}
            onKeyDown={handleKeyDown}
            onChange={(event) => setNote(event.target.value)}
            placeholder={inputPlaceholder}
            rows={1}
            value={note}
          />

          <div className="sd-action-row flex items-center justify-between gap-2 border-t border-line/70 px-2 py-2">
            <span className="hidden text-xs text-muted sm:inline">Enter to continue, Shift+Enter for new line</span>
            {activeSession ? (
              <motion.button
                aria-label="Continue story"
                className="sd-send-button inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!hasDirectorNote || disabled}
                onClick={submitGeneration}
                type="button"
                whileTap={{ scale: 0.97 }}
              >
                <Send size={17} />
                <span className="sd-send-label">{isGenerating ? "Writing..." : "Continue"}</span>
              </motion.button>
            ) : (
              <motion.button
                className="sd-button-primary inline-flex h-10 items-center justify-center rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white"
                onClick={onCreateSession}
                type="button"
                whileHover={{ y: -1 }}
                whileTap={{ scale: 0.97 }}
              >
                New Story
              </motion.button>
            )}
          </div>
          {sessionHint ? <p className="sd-composer-hint px-2 pb-1 text-xs leading-5 text-muted">{sessionHint}</p> : null}
        </div>
      </div>
    </div>
  );
}

export default memo(StoryInput);
