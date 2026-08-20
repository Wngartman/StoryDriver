import { motion } from "framer-motion";
import {
  BookOpen,
  Clock3,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import BrandMark from "./BrandMark.jsx";
import { useAppStore } from "../store/useAppStore.js";

const formatDate = (value) =>
  new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value));

export default function Sidebar({
  collapsed = false,
  isMobile = false,
  onCreateSession,
  onDeleteSession,
  onSelectSession,
  onSettings,
  onToggle,
}) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [focusSearchWhenOpen, setFocusSearchWhenOpen] = useState(false);
  const searchRef = useRef(null);
  const {
    activeSessionId,
    deleteJobsById,
    isCreatingSession,
    isLoadingSessions,
    selectSession,
    sessions,
  } = useAppStore(useShallow((state) => ({
    activeSessionId: state.activeSessionId,
    deleteJobsById: state.deleteJobsById,
    isCreatingSession: state.isCreatingSession,
    isLoadingSessions: state.isLoadingSessions,
    selectSession: state.selectSession,
    sessions: state.sessions,
  })));

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim().toLocaleLowerCase()), 140);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!collapsed && focusSearchWhenOpen) {
      searchRef.current?.focus();
      setFocusSearchWhenOpen(false);
    }
  }, [collapsed, focusSearchWhenOpen]);

  const filteredSessions = useMemo(() => {
    if (!debouncedQuery) return sessions;
    return sessions.filter((session) => session.title?.toLocaleLowerCase().includes(debouncedQuery));
  }, [debouncedQuery, sessions]);
  const runningDeleteJobs = Object.values(deleteJobsById || {}).filter((job) =>
    ["queued", "running"].includes(job?.status),
  );

  const handleSelect = async (sessionId) => {
    await selectSession(sessionId);
    onSelectSession?.();
  };

  const openSearch = () => {
    if (collapsed) {
      setFocusSearchWhenOpen(true);
      onToggle?.();
    } else {
      searchRef.current?.focus();
    }
  };

  return (
    <nav aria-label="Stories" className={`sd-sidebar flex h-full flex-col ${collapsed ? "is-collapsed" : ""}`}>
      <div className="sd-sidebar-header border-b border-line p-3">
        <div className={`flex items-center ${collapsed ? "justify-center" : "justify-between gap-2"}`}>
          <div className="flex min-w-0 items-center gap-3">
            <BrandMark />
            {!collapsed ? (
              <div className="min-w-0">
                <h1 className="truncate text-base font-semibold text-zinc-100">StoryDriver</h1>
                <p className="truncate text-xs text-muted">Directed fiction</p>
              </div>
            ) : null}
          </div>
          {!isMobile ? (
            <button
              aria-label={collapsed ? "Open sidebar" : "Close sidebar"}
              className="sd-icon-button grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200"
              onClick={onToggle}
              title={collapsed ? "Open sidebar" : "Close sidebar"}
              type="button"
            >
              {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            </button>
          ) : null}
        </div>

        <div className={`mt-3 grid gap-2 ${collapsed ? "justify-items-center" : "grid-cols-[minmax(0,1fr)_auto]"}`}>
          <motion.button
            aria-label="New Story"
            className={`sd-button-primary flex min-h-11 items-center justify-center rounded-lg bg-zinc-100 text-sm font-semibold text-zinc-950 ${collapsed ? "w-11 px-0" : "gap-2 px-3"}`}
            disabled={isCreatingSession}
            onClick={() => onCreateSession("Untitled Story")}
            title="New Story"
            type="button"
            whileTap={{ scale: 0.97 }}
          >
            <Plus size={18} />
            {!collapsed ? "New Story" : null}
          </motion.button>
          <button
            aria-label="Search stories"
            className="sd-icon-button grid h-11 w-11 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200"
            onClick={openSearch}
            title="Search stories"
            type="button"
          >
            <Search size={18} />
          </button>
        </div>

        {!collapsed ? (
          <div className="relative mt-2">
            <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" size={15} />
            <input
              aria-label="Search stories"
              className="sd-field h-10 w-full rounded-lg border border-line bg-panelSoft pl-9 pr-9 text-sm text-zinc-100 placeholder:text-muted"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Escape") return;
                if (query) setQuery("");
                else event.currentTarget.blur();
              }}
              placeholder="Search stories"
              ref={searchRef}
              type="search"
              value={query}
            />
            {query ? (
              <button
                aria-label="Clear story search"
                className="absolute right-1 top-1 grid h-8 w-8 place-items-center rounded-md text-muted hover:bg-white/5 hover:text-zinc-100"
                onClick={() => {
                  setQuery("");
                  searchRef.current?.focus();
                }}
                type="button"
              >
                <X size={14} />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className={`story-scrollbar min-h-0 flex-1 overflow-y-auto ${collapsed ? "px-2 py-3" : "p-3"}`}>
        <div className={`mb-2 flex items-center text-xs font-medium text-muted ${collapsed ? "justify-center" : "gap-2 px-2"}`}>
          <Clock3 size={15} />
          {!collapsed ? "Recent" : <span className="sr-only">Recent stories</span>}
        </div>
        {!collapsed && runningDeleteJobs.length ? (
          <div className="mb-3 rounded-lg border border-line bg-panelSoft px-3 py-2 text-xs text-muted">
            Deleting {runningDeleteJobs.length} stor{runningDeleteJobs.length === 1 ? "y" : "ies"} safely...
          </div>
        ) : null}

        {!collapsed && isLoadingSessions ? (
          <div className="sd-empty-state rounded-lg border border-line bg-panelSoft px-3 py-4 text-sm text-muted">Loading stories...</div>
        ) : null}

        {!collapsed && !isLoadingSessions && sessions.length === 0 ? (
          <div className="sd-empty-state rounded-lg border border-line bg-panelSoft px-3 py-4 text-sm text-muted">No stories yet.</div>
        ) : null}

        {!collapsed && !isLoadingSessions && sessions.length > 0 && filteredSessions.length === 0 ? (
          <div className="sd-empty-state rounded-lg border border-line bg-panelSoft px-3 py-4 text-sm text-muted">No matching stories.</div>
        ) : null}

        <div className="space-y-1.5">
          {(collapsed ? sessions.slice(0, 7) : filteredSessions).map((session) => {
            const isActive = session.id === activeSessionId;
            if (collapsed) {
              return (
                <button
                  aria-label={`Open story ${session.title}`}
                  className={`sd-story-rail-button mx-auto grid h-11 w-11 place-items-center rounded-lg border text-sm font-semibold ${isActive ? "border-tide/45 bg-tide/15 text-tide" : "border-line bg-panelSoft text-zinc-300 hover:border-zinc-500"}`}
                  key={session.id}
                  onClick={() => handleSelect(session.id)}
                  title={session.title}
                  type="button"
                >
                  {(session.title || "S").trim().charAt(0).toLocaleUpperCase()}
                </button>
              );
            }
            return (
              <div
                className={`sd-story-row group grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-stretch rounded-lg border ${isActive ? "sd-story-row-active border-tide/35 bg-tide/10 text-zinc-100" : "border-line/80 bg-panelSoft/60 text-zinc-300 hover:border-zinc-600 hover:bg-panelSoft"}`}
                key={session.id}
              >
                <button className="min-w-0 px-3 py-2.5 text-left" onClick={() => handleSelect(session.id)} type="button">
                  <div className="line-clamp-1 break-all text-sm font-medium">{session.title}</div>
                  <div className="mt-1 flex items-center gap-1.5 text-xs text-muted">
                    <Clock3 size={12} />
                    {formatDate(session.updated_at)}
                  </div>
                </button>
                <button
                  aria-label={`Delete story ${session.title}`}
                  className="m-1.5 grid h-9 w-9 place-items-center rounded-lg border border-transparent text-muted opacity-0 transition hover:border-ember/30 hover:bg-ember/10 hover:text-ember focus:opacity-100 group-hover:opacity-100"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDeleteSession?.(session);
                  }}
                  title="Delete story"
                  type="button"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div className={`border-t border-line p-3 ${collapsed ? "grid place-items-center" : ""}`}>
        <button
          className={`sd-icon-button flex min-h-11 items-center rounded-lg border border-line bg-panelSoft text-zinc-200 ${collapsed ? "w-11 justify-center" : "w-full gap-2 px-3"}`}
          onClick={onSettings}
          title="Settings"
          type="button"
        >
          <Settings size={18} />
          {!collapsed ? <span className="text-sm font-medium">Settings</span> : <span className="sr-only">Settings</span>}
        </button>
      </div>
    </nav>
  );
}
