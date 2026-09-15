import { AnimatePresence, motion } from "framer-motion";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import Sidebar from "./Sidebar.jsx";
import StoryFeed from "./StoryFeed.jsx";
import StoryInput from "./StoryInput.jsx";
import TopBar from "./TopBar.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import NarrationMiniPlayer from "./NarrationMiniPlayer.jsx";
import WorkspaceBackground from "./WorkspaceBackground.jsx";
import { useAppStore } from "../store/useAppStore.js";
import { applyUiPreset } from "../ui-presets/applyUiPreset.js";
import { getResolvedLayoutConfig } from "../ui-presets/layoutConfig.js";
import { API_BASE_URL } from "../api.js";
import { applyDisplaySettings } from "../services/displaySettings.js";

const worldFields = ["setting", "tone", "rules", "locations", "factions", "conflicts", "history"];
const SettingsDrawer = lazy(() => import("./SettingsDrawer.jsx"));
const StoryDetailsDrawer = lazy(() => import("./StoryDetailsDrawer.jsx"));

export default function AppShell() {
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isStoryDetailsOpen, setIsStoryDetailsOpen] = useState(false);
  const layoutConfig = getResolvedLayoutConfig();

  const {
    activeSessionId,
    checkHealth,
    createSession,
    deleteSession,
    fetchTTSStatus,
    health,
    isGenerating,
    lastError,
    loadInitial,
    modelSettings,
    scenesBySession,
    sessionCharactersBySession,
    retryAutoTitleSession,
    sessions,
    saveUiSettings,
    uiPresetId,
    uiSettings,
    ttsStatus,
    updateSessionTitle,
    worldNotesBySession,
  } = useAppStore(useShallow((state) => ({
    activeSessionId: state.activeSessionId,
    checkHealth: state.checkHealth,
    createSession: state.createSession,
    deleteSession: state.deleteSession,
    fetchTTSStatus: state.fetchTTSStatus,
    health: state.health,
    isGenerating: state.isGenerating,
    lastError: state.lastError,
    loadInitial: state.loadInitial,
    modelSettings: state.modelSettings,
    scenesBySession: state.scenesBySession,
    sessionCharactersBySession: state.sessionCharactersBySession,
    retryAutoTitleSession: state.retryAutoTitleSession,
    sessions: state.sessions,
    saveUiSettings: state.saveUiSettings,
    uiPresetId: state.uiPresetId,
    uiSettings: state.uiSettings,
    ttsStatus: state.ttsStatus,
    updateSessionTitle: state.updateSessionTitle,
    worldNotesBySession: state.worldNotesBySession,
  })));

  useEffect(() => {
    loadInitial();
    let timer = null;
    const stopTimer = () => {
      if (timer) window.clearTimeout(timer);
      timer = null;
    };
    const scheduleHealthCheck = () => {
      stopTimer();
      if (document.visibilityState === "hidden") return;
      timer = window.setTimeout(async () => {
        await checkHealth();
        scheduleHealthCheck();
      }, 30000);
    };
    const handleVisibility = () => {
      if (document.visibilityState === "hidden") {
        stopTimer();
        return;
      }
      checkHealth().finally(scheduleHealthCheck);
    };
    document.addEventListener("visibilitychange", handleVisibility);
    scheduleHealthCheck();
    return () => {
      stopTimer();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [checkHealth, loadInitial]);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const pollUntilReady = async () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") {
        timer = window.setTimeout(pollUntilReady, 2000);
        return;
      }
      const status = await fetchTTSStatus();
      if (!cancelled) {
        timer = window.setTimeout(pollUntilReady, status?.startup?.status === "starting" ? 2000 : 30000);
      }
    };
    timer = window.setTimeout(pollUntilReady, 2000);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [fetchTTSStatus]);

  useEffect(() => {
    applyUiPreset(uiPresetId);
  }, [uiPresetId]);

  useEffect(() => {
    applyDisplaySettings(uiSettings);
  }, [uiSettings]);

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || null,
    [activeSessionId, sessions],
  );

  useEffect(() => {
    document.title = activeSession?.title ? `${activeSession.title} | StoryDriver` : "StoryDriver";
  }, [activeSession?.title]);

  const scenes = activeSessionId ? scenesBySession[activeSessionId] || [] : [];
  const sessionCharacters = activeSessionId ? sessionCharactersBySession[activeSessionId] || [] : [];
  const activeCharactersCount = sessionCharacters.filter((link) => link.is_active).length;
  const worldNotes = activeSessionId ? worldNotesBySession[activeSessionId] : null;
  const worldNotesSet = worldFields.some((field) => Boolean(worldNotes?.[field]?.trim()));
  const sidebarCollapsed = Boolean(uiSettings.sidebar_collapsed);
  useEffect(() => {
    document.documentElement.dataset.sidebarCollapsed = sidebarCollapsed ? "true" : "false";
  }, [sidebarCollapsed]);
  const toggleDesktopSidebar = () => {
    saveUiSettings({ sidebar_collapsed: !sidebarCollapsed }).catch(() => {});
  };

  return (
    <div
      className="sd-app-shell relative h-[100dvh] overflow-hidden bg-ink pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)] pt-[env(safe-area-inset-top)] text-zinc-100"
      data-right-sidebar={layoutConfig.rightSidebarEnabled ? "enabled" : "disabled"}
      data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"}
      data-utility-cards={layoutConfig.utilityCards}
      data-utility-tray={layoutConfig.utilityTrayEnabled ? "enabled" : "disabled"}
    >
      <div className="sd-app-texture absolute inset-0" />
      <div className="sd-app-content flex h-full">
        <div className="sd-sidebar-shell hidden h-full shrink-0 border-r border-line bg-panel/90 lg:block">
          <Sidebar
            collapsed={sidebarCollapsed}
            onCreateSession={createSession}
            onDeleteSession={setDeleteTarget}
            onSettings={() => setIsSettingsOpen(true)}
            onToggle={toggleDesktopSidebar}
          />
        </div>

        <AnimatePresence>
          {isSidebarOpen ? (
            <motion.div
              className="fixed inset-0 z-40 lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              <button
                aria-label="Close sidebar"
                className="absolute inset-0 bg-black/55"
                onClick={() => setIsSidebarOpen(false)}
                type="button"
              />
              <motion.div
                className="sd-sidebar-mobile relative h-full w-[86vw] max-w-80 border-r border-line bg-panel pt-[env(safe-area-inset-top)] shadow-glow"
                initial={{ x: -360 }}
                animate={{ x: 0 }}
                exit={{ x: -360 }}
                transition={{ type: "spring", stiffness: 360, damping: 36 }}
              >
                <Sidebar
                  isMobile
                  onCreateSession={createSession}
                  onDeleteSession={setDeleteTarget}
                  onSettings={() => {
                    setIsSidebarOpen(false);
                    setIsSettingsOpen(true);
                  }}
                  onSelectSession={() => setIsSidebarOpen(false)}
                />
              </motion.div>
            </motion.div>
          ) : null}
        </AnimatePresence>

        <main className="sd-main-shell flex min-w-0 flex-1 flex-col">
          <TopBar
            activeSession={activeSession}
            health={health}
            ttsStatus={ttsStatus}
            onDeleteSession={setDeleteTarget}
            onMenu={() => setIsSidebarOpen(true)}
            onModelSettings={() => setIsSettingsOpen(true)}
            onRetryAutoTitle={retryAutoTitleSession}
            onRenameSession={updateSessionTitle}
            onSettings={() => setIsSettingsOpen(true)}
            onStoryDetails={() => setIsStoryDetailsOpen(true)}
            activeCharactersCount={activeCharactersCount}
            worldNotesSet={worldNotesSet}
          />

          <div className="sd-workspace-grid grid min-h-0 flex-1 grid-cols-1">
            <section className="sd-writing-column flex min-h-0 flex-col">
              <WorkspaceBackground />
              {health.checked && !health.ok ? (
                <div className="mx-auto mt-3 w-full max-w-[var(--sd-reading-column-max)] px-3 md:px-6">
                  <div className="rounded-xl border border-ember/30 bg-ember/10 px-4 py-3 text-sm leading-6 text-ember">
                    <div className="font-semibold text-zinc-100">StoryDriver frontend loaded, but backend is unreachable.</div>
                    <div className="mt-1 text-ember/90">
                      Try <span className="break-all font-mono text-amber-100">{API_BASE_URL}/health</span> from this device.
                      Check Windows Firewall/private network access if it does not open.
                    </div>
                  </div>
                </div>
              ) : null}
              {modelSettings?.provider === "llama_cpp" && !modelSettings.model ? (
                <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 text-sm">
                  <span className="text-muted">No writing model selected</span>
                  <button className="sd-action-button min-h-10 rounded-md border border-line px-3 font-medium" onClick={() => setIsSettingsOpen(true)} type="button">Choose model</button>
                </div>
              ) : null}
              <StoryFeed activeSession={activeSession} scenes={scenes} />
              <AnimatePresence>
                <NarrationMiniPlayer />
              </AnimatePresence>
              <StoryInput
                activeSession={activeSession}
                onCreateSession={() => createSession("Untitled Story")}
              />
            </section>

          </div>
        </main>
      </div>

      <Suspense fallback={null}>

        <AnimatePresence>
          {lastError ? (
          <motion.div
            animate={{ opacity: 1, y: 0 }}
            className="fixed bottom-[calc(env(safe-area-inset-bottom)+14px)] left-[max(12px,env(safe-area-inset-left))] right-[max(12px,env(safe-area-inset-right))] z-[75] mx-auto max-w-2xl rounded-lg border border-ember/35 bg-[#17120c]/95 px-4 py-3 text-sm text-ember shadow-glow backdrop-blur"
            exit={{ opacity: 0, y: 12 }}
            initial={{ opacity: 0, y: 12 }}
          >
            {lastError}
          </motion.div>
          ) : null}
        </AnimatePresence>

        <AnimatePresence>
          {isSettingsOpen ? (
          <SettingsDrawer onClose={() => setIsSettingsOpen(false)} />
          ) : null}
        </AnimatePresence>

        <AnimatePresence>
          {isStoryDetailsOpen ? (
          <StoryDetailsDrawer
            activeSession={activeSession}
            onClose={() => setIsStoryDetailsOpen(false)}
          />
          ) : null}
        </AnimatePresence>

        <AnimatePresence>
          {deleteTarget ? (
          <ConfirmDialog
            body={`This permanently removes "${deleteTarget.title}" and queues safe cleanup of its story-owned generated media. Other stories, settings, and unrelated files are left alone.`}
            confirmLabel="Permanently Delete"
            onCancel={() => setDeleteTarget(null)}
            onConfirm={async () => {
              await deleteSession(deleteTarget.id, { permanent: true });
              setDeleteTarget(null);
            }}
            title="Permanently delete this story?"
          />
          ) : null}
        </AnimatePresence>
      </Suspense>
    </div>
  );
}
