import { motion } from "framer-motion";
import {
  BookOpenText,
  Check,
  Globe2,
  LoaderCircle,
  Menu,
  Pencil,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  Trash2,
  UsersRound,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useAppStore } from "../store/useAppStore.js";

export default function TopBar({
  activeSession,
  activeCharactersCount = 0,
  health,
  ttsStatus,
  onMenu,
  onDeleteSession,
  onModelSettings,
  onRetryAutoTitle,
  onRenameSession,
  onSettings,
  onStoryDetails,
  worldNotesSet = false,
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [isRetryingTitle, setIsRetryingTitle] = useState(false);
  const [title, setTitle] = useState(activeSession?.title || "");
  const [showConstellation, setShowConstellation] = useState(false);
  const titleClickTimesRef = useRef([]);
  const constellationTimerRef = useRef(null);
  const easterEggsEnabled = useAppStore((state) => state.uiSettings.easter_eggs_enabled !== false);
  const normalizedTitle = (activeSession?.title || "").trim().toLowerCase();
  const hasGenericTitle =
    ["", "untitled", "untitled story", "new story", "blank story"].includes(normalizedTitle) ||
    /^new story(?:\s+\d+)?$/.test(normalizedTitle) ||
    /^untitled(?:\s+\d+)?$/.test(normalizedTitle);
  const titleStatus = activeSession?.auto_title_status || "skipped";
  const kokoroReady = Boolean(ttsStatus?.kokoro?.reachable);
  const titleIsUserSet = activeSession?.title_source === "user_set" || titleStatus === "user_set";
  const showTitling = Boolean(activeSession && hasGenericTitle && titleStatus === "pending" && !titleIsUserSet);
  const canRetryTitle = Boolean(
    activeSession &&
      hasGenericTitle &&
      !titleIsUserSet &&
      ["failed", "skipped"].includes(titleStatus) &&
      onRetryAutoTitle,
  );

  useEffect(() => {
    setTitle(activeSession?.title || "");
    setIsEditing(false);
    setShowConstellation(false);
    titleClickTimesRef.current = [];
  }, [activeSession?.id, activeSession?.title]);

  useEffect(() => () => {
    if (constellationTimerRef.current) window.clearTimeout(constellationTimerRef.current);
  }, []);

  const handleTitleConstellation = () => {
    if (!activeSession || !easterEggsEnabled) return;
    const now = Date.now();
    titleClickTimesRef.current = [...titleClickTimesRef.current.filter((value) => now - value < 20000), now];
    if (titleClickTimesRef.current.length < 5) return;
    titleClickTimesRef.current = [];
    setShowConstellation(true);
    if (constellationTimerRef.current) window.clearTimeout(constellationTimerRef.current);
    constellationTimerRef.current = window.setTimeout(() => setShowConstellation(false), 2000);
  };

  const saveTitle = async () => {
    const nextTitle = title.trim();
    if (!activeSession || !nextTitle || nextTitle === activeSession.title) {
      setIsEditing(false);
      setTitle(activeSession?.title || "");
      return;
    }
    await onRenameSession(activeSession.id, nextTitle);
    setIsEditing(false);
  };

  const retryTitle = async () => {
    if (!activeSession || !onRetryAutoTitle || isRetryingTitle) return;
    setIsRetryingTitle(true);
    try {
      await onRetryAutoTitle(activeSession.id);
    } finally {
      setIsRetryingTitle(false);
    }
  };

  return (
    <>
    <header className="sd-topbar flex min-h-16 shrink-0 items-center justify-between gap-2 border-b border-line bg-ink/78 px-3 py-2 backdrop-blur md:px-5">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <motion.button
          aria-label="Open stories"
          className="sd-icon-button grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-line bg-panel text-zinc-200 lg:hidden"
          onClick={onMenu}
          type="button"
          whileHover={{ y: -1 }}
          whileTap={{ scale: 0.96 }}
        >
          <Menu size={19} />
        </motion.button>
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            {isEditing ? (
              <>
                <input
                  aria-label="Story title"
                  className="sd-field min-w-0 max-w-[48vw] rounded-lg border border-line bg-panel px-2.5 py-1.5 text-sm font-semibold text-zinc-100 md:max-w-sm md:text-base"
                  onChange={(event) => setTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") saveTitle();
                    if (event.key === "Escape") {
                      setTitle(activeSession?.title || "");
                      setIsEditing(false);
                    }
                  }}
                  value={title}
                />
                <button
                  aria-label="Save story title"
                  className="sd-icon-button grid h-8 w-8 place-items-center rounded-lg border border-line bg-panel text-moss"
                  onClick={saveTitle}
                  type="button"
                >
                  <Check size={15} />
                </button>
                <button
                  aria-label="Cancel rename"
                  className="sd-icon-button grid h-8 w-8 place-items-center rounded-lg border border-line bg-panel text-zinc-300"
                  onClick={() => {
                    setTitle(activeSession?.title || "");
                    setIsEditing(false);
                  }}
                  type="button"
                >
                  <X size={15} />
                </button>
              </>
            ) : (
              <>
                <button aria-label="Current story title" className="min-w-0 truncate text-left text-sm font-semibold text-zinc-100 md:text-base" onClick={handleTitleConstellation} type="button">
                  {activeSession?.title || "StoryDriver"}
                </button>
                {activeSession ? (
                  <>
                    {showTitling ? (
                      <span className="sd-chip hidden rounded-full border border-line bg-panelSoft px-2 py-0.5 text-[11px] text-muted sm:inline">
                        Titling...
                      </span>
                    ) : null}
                    {canRetryTitle ? (
                      <button
                        aria-label="Retry automatic story title"
                        className="sd-icon-button grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-transparent text-muted transition hover:border-line hover:bg-panel hover:text-zinc-200 disabled:opacity-50"
                        disabled={isRetryingTitle}
                        onClick={retryTitle}
                        title="Retry automatic title"
                        type="button"
                      >
                        <RefreshCw className={isRetryingTitle ? "animate-spin" : ""} size={14} />
                      </button>
                    ) : null}
                    <button
                      aria-label="Rename story"
                      className="sd-icon-button grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-transparent text-muted transition hover:border-line hover:bg-panel hover:text-zinc-200"
                      onClick={() => setIsEditing(true)}
                      type="button"
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      aria-label="Delete story"
                      className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-transparent text-muted transition hover:border-red-400/30 hover:bg-red-500/10 hover:text-red-300"
                      onClick={() => onDeleteSession(activeSession)}
                      type="button"
                    >
                      <Trash2 size={14} />
                    </button>
                  </>
                ) : null}
              </>
            )}
            <span className="sd-chip hidden rounded-full border border-line bg-panelSoft px-2 py-0.5 text-xs text-muted sm:inline">
              local
            </span>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            {health.ok ? (
              <span className="inline-flex items-center gap-1.5 text-moss">
                <Wifi size={13} className="text-moss" />
                Local server connected
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-ember">
                <WifiOff size={13} className="text-ember" />
                Backend offline
              </span>
            )}
            <span className={`inline-flex items-center gap-1.5 ${kokoroReady ? "text-moss" : "text-muted"}`}>
              <LoaderCircle className={kokoroReady ? "" : "animate-spin"} size={13} />
              {kokoroReady ? "Narration ready" : "Narration starting"}
            </span>
            {activeSession ? (
              <>
                <span className="inline-flex items-center gap-1 text-muted">
                  <UsersRound size={13} />
                  {activeCharactersCount} active
                </span>
                <span className={`inline-flex items-center gap-1 ${worldNotesSet ? "text-moss" : "text-muted"}`}>
                  <Globe2 size={13} />
                  World {worldNotesSet ? "set" : "unset"}
                </span>
              </>
            ) : null}
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <motion.button
          aria-label="Story Details"
          className="sd-icon-button grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-line bg-panel text-zinc-200 transition hover:border-zinc-600 disabled:cursor-not-allowed disabled:opacity-45"
          disabled={!activeSession}
          onClick={onStoryDetails}
          title="Story Details"
          type="button"
          whileHover={{ y: -1 }}
          whileTap={{ scale: 0.96 }}
        >
          <BookOpenText size={18} />
        </motion.button>
        <motion.button
          aria-label="Model Settings"
          className="sd-icon-button grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-line bg-panel text-zinc-200 transition hover:border-zinc-600"
          onClick={onModelSettings}
          title="Model Settings"
          type="button"
          whileHover={{ y: -1 }}
          whileTap={{ scale: 0.96 }}
        >
          <SlidersHorizontal size={18} />
        </motion.button>
        <motion.button
          aria-label="Settings"
          className="sd-icon-button grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-line bg-panel text-zinc-200 transition hover:border-zinc-600"
          onClick={onSettings}
          title="Settings"
          type="button"
          whileHover={{ y: -1 }}
          whileTap={{ scale: 0.96 }}
        >
          <Settings size={18} />
        </motion.button>
      </div>
    </header>
    {showConstellation ? createPortal(
      <div aria-live="polite" className="sd-director-constellation" role="status">
        <div className="sd-constellation-mark" aria-hidden="true">
          <i className="line one" /><i className="line two" /><i className="line three" /><i className="line four" />
          <b className="point one" /><b className="point two" /><b className="point three" /><b className="point four" /><b className="point five" />
        </div>
        <div className="sd-constellation-title">{activeSession?.title}</div>
      </div>,
      document.body,
    ) : null}
    </>
  );
}
