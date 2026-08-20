import { Info } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { settingExplanation } from "../settings/explanations.js";


export function SettingHelp({ name }) {
  const text = settingExplanation(name);
  const id = useId();
  const buttonRef = useRef(null);
  const timerRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ above: true, left: 12, top: 12 });

  const cancelTimer = () => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = null;
  };
  const show = (delayed = false) => {
    cancelTimer();
    if (delayed) timerRef.current = window.setTimeout(() => setOpen(true), 750);
    else setOpen(true);
  };

  useEffect(() => {
    if (!open) return undefined;
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      const width = Math.min(320, window.innerWidth - 24);
      const left = Math.max(12, Math.min(window.innerWidth - width - 12, rect.left - width + rect.width));
      const above = rect.top > 150;
      const top = above ? rect.top - 12 : rect.bottom + 12;
      setPosition({ above, left, top });
    }
    const close = () => setOpen(false);
    const onKey = (event) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => () => cancelTimer(), []);
  if (!text) return null;

  return (
    <>
      <button
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        aria-label={`About ${name}`}
        className="sd-setting-help grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted hover:bg-white/5 hover:text-zinc-100"
        onBlur={() => setOpen(false)}
        onClick={(event) => {
          event.preventDefault();
          setOpen((value) => !value);
        }}
        onFocus={() => show(false)}
        onMouseEnter={() => show(true)}
        onMouseLeave={() => {
          cancelTimer();
          setOpen(false);
        }}
        ref={buttonRef}
        type="button"
      >
        <Info size={14} />
      </button>
      {open ? createPortal(
        <div
          className={`sd-setting-tooltip fixed z-[100] w-[min(320px,calc(100vw-24px))] rounded-lg border border-line bg-panel px-3 py-2 text-left text-xs font-normal normal-case leading-5 text-zinc-200 shadow-glow ${position.above ? "-translate-y-full" : ""}`}
          id={id}
          role="tooltip"
          style={{ left: position.left, top: position.top }}
        >
          {text}
        </div>,
        document.body,
      ) : null}
    </>
  );
}

export function SettingLabel({ className = "", name }) {
  return (
    <span className={`flex min-h-7 items-center justify-between gap-2 ${className}`}>
      <span>{name}</span>
      <SettingHelp name={name} />
    </span>
  );
}
