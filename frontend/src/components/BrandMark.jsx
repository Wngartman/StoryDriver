import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import markUrl from "../assets/brand/storydriver-mark.svg";
import { useAppStore } from "../store/useAppStore.js";


export default function BrandMark({ className = "", label = "StoryDriver logo", size = "md" }) {
  const enabled = useAppStore((state) => state.uiSettings.easter_eggs_enabled !== false);
  const holdTimerRef = useRef(null);
  const hideTimerRef = useRef(null);
  const [showInkPath, setShowInkPath] = useState(false);
  const sizeClass = { sm: "h-8 w-8", md: "h-9 w-9", lg: "h-11 w-11" }[size] || "h-9 w-9";

  const cancelHold = () => {
    if (holdTimerRef.current) window.clearTimeout(holdTimerRef.current);
    holdTimerRef.current = null;
  };
  const trigger = () => {
    if (!enabled) return;
    setShowInkPath(true);
    if (hideTimerRef.current) window.clearTimeout(hideTimerRef.current);
    hideTimerRef.current = window.setTimeout(() => setShowInkPath(false), 2400);
  };
  const startHold = () => {
    if (!enabled) return;
    cancelHold();
    holdTimerRef.current = window.setTimeout(trigger, 2000);
  };

  useEffect(() => () => {
    cancelHold();
    if (hideTimerRef.current) window.clearTimeout(hideTimerRef.current);
  }, []);

  return (
    <>
      <button
        aria-label={`${label}. Hold for two seconds.`}
        className={`sd-brand-mark ${sizeClass} ${className}`}
        onContextMenu={(event) => event.preventDefault()}
        onKeyDown={(event) => {
          if (["Enter", " "].includes(event.key) && !event.repeat) startHold();
        }}
        onKeyUp={cancelHold}
        onPointerCancel={cancelHold}
        onPointerDown={startHold}
        onPointerLeave={cancelHold}
        onPointerUp={cancelHold}
        title="StoryDriver"
        type="button"
      >
        <img alt="" className="sd-brand-mark-image" decoding="async" draggable="false" src={markUrl} />
      </button>
      {showInkPath ? createPortal(
        <div aria-live="polite" className="sd-ink-path-effect" role="status">
          <div className="sd-ink-path-line" />
          <div className="sd-ink-path-page" />
          <span>The page is listening.</span>
        </div>,
        document.body,
      ) : null}
    </>
  );
}
