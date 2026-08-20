import { create } from "zustand";
import { API_BASE_URL, api } from "../api.js";
import { ttsController } from "../services/ttsController.js";
import { applyDisplaySettings, DEFAULT_UI_SETTINGS, normalizeUiSettings } from "../services/displaySettings.js";
import { applyUiPreset, persistUiPresetId, readStoredUiPresetId } from "../ui-presets/applyUiPreset.js";
import { getAllUiPresets, getUiPreset, normalizeUiPresetId, setCustomUiPresets } from "../ui-presets/registry.js";

const sortSessions = (sessions) =>
  [...sessions].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));

const sortCharacters = (characters) =>
  [...characters].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));

const selectionKey = (sessionId) => `storydriver.selection.${sessionId}`;
let ttsPreviewAudio = null;
let storyContextRequestSequence = 0;
let promptPreviewRequestSequence = 0;
const sceneRequestSequences = new Map();
const TTS_PREVIEW_SAMPLE_TEXT =
  "The lantern guttered in the cold draft. Elara lowered her voice. 'Wait until the ridge goes dark,' she said. Beyond the barn, the valley held its breath.";

const latestVersion = (scene) => scene?.versions?.at(-1) || null;
const genericStoryTitles = new Set(["", "untitled", "untitled story", "new story", "blank story"]);

const isGenericStoryTitle = (title) => {
  const normalized = (title || "").trim().toLowerCase();
  return genericStoryTitles.has(normalized) || /^new story(?:\s+\d+)?$/.test(normalized) || /^untitled(?:\s+\d+)?$/.test(normalized);
};

const mergeSession = (sessions, session) => {
  if (!session?.id) return sessions;
  const exists = sessions.some((existing) => existing.id === session.id);
  const nextSessions = exists
    ? sessions.map((existing) => (existing.id === session.id ? { ...existing, ...session } : existing))
    : [session, ...sessions];
  return sortSessions(nextSessions);
};

const sessionNeedsAutoTitlePolling = (session) =>
  Boolean(session?.id) &&
  isGenericStoryTitle(session.title) &&
  ["pending", "skipped"].includes(session.auto_title_status || "skipped") &&
  session.title_source !== "user_set";

const absoluteAudioUrl = (audioUrl) => {
  if (!audioUrl) return "";
  if (/^https?:\/\//i.test(audioUrl)) return audioUrl;
  return `${API_BASE_URL}${audioUrl}`;
};

const copyTextToClipboard = async (text) => {
  try {
    await navigator.clipboard?.writeText(text);
    return true;
  } catch {
    return false;
  }
};

const writeStoredSelection = (sessionId, sceneId, versionId) => {
  if (!sessionId || !sceneId) return;
  try {
    window.localStorage.setItem(selectionKey(sessionId), JSON.stringify({ sceneId, versionId }));
  } catch {
    // localStorage is a convenience; the app still works without it.
  }
};

const resolveSelection = (sessionId, scenes) => {
  if (!scenes?.length) {
    return { sceneId: null, versionId: null, versionIndex: null };
  }

  const scene = scenes.at(-1);
  const version = latestVersion(scene);
  return {
    sceneId: scene.id,
    versionId: version?.id || scene.active_version_id || scene.id,
    versionIndex: version?.version_index || scene.active_version_index || 1,
  };
};

export const useAppStore = create((set, get) => ({
  activeSessionId: null,
  health: { checked: false, ok: false },
  diagnostics: null,
  isCreatingSession: false,
  isGenerating: false,
  isLoadingScenes: false,
  isLoadingSessions: false,
  isLoadingSettings: false,
  isLoadingStoryDetails: false,
  isRefreshingModels: false,
  isRefreshingFoundation: false,
  isRefreshingStoryState: false,
  isRefreshingSummary: false,
  lastError: "",
  deleteJobsById: {},
  uiPresetId: readStoredUiPresetId(),
  availableUiPresets: getAllUiPresets(),
  customUiPresets: [],
  isLoadingUiPresets: false,
  uiPresetMessage: "",
  uiSettings: { ...DEFAULT_UI_SETTINGS },
  isLoadingUiSettings: false,
  backgroundLibrary: { folder: "", items: [], max_file_bytes: 0 },
  isLoadingBackgrounds: false,
  backgroundMessage: "",
  characters: [],
  models: [],
  modelPresets: [],
  modelSettings: null,
  prosePromptPreview: null,
  prosePromptPreviewError: "",
  isLoadingProsePromptPreview: false,
  taskModelProfiles: {},
  taskModelResolved: {},
  taskModelTaskTypes: [],
  browserVoices: [],
  kokoroVoices: [],
  storageMaintenance: null,
  isLoadingStorageMaintenance: false,
  ttsStatus: null,
  ttsSettings: {
    tts_provider: "kokoro",
    tts_quality_mode: "balanced",
    tts_fallback_provider: "kokoro",
    high_quality_local_enabled: true,
    tts_speed: 0.95,
    tts_voice: null,
    tts_voice_profile_id: "natural_female_narrator",
    tts_voice_profiles: [],
    auto_read_new_scenes: false,
    fast_reading_mode: false,
    pre_synthesize_new_scenes: false,
    pre_synthesize_mode: "selected_scene",
    chunked_narration_mode: "progressive_chunks",
    tts_chunking_profile: "natural",
    tts_chunk_size: 1200,
    tts_prebuffer_chunks: 2,
    kokoro_base_url: "http://localhost:8880",
    allow_browser_fallback: false,
    highlight_narration: true,
    tts_follow_mode: "phrase",
    tts_follow_highlight: "phrase",
    narration_pacing: "natural",
    dialogue_pause_strength: "medium",
    paragraph_pause_strength: "medium",
    dialogue_narration_style: "neutral",
    breathing_mode: "natural",
    pronunciation_entries: [],
    pronunciation_dictionary_version: "empty",
    normalization_version: "tts-realism-v1",
    kokoro_temperature: null,
    kokoro_top_p: null,
    kokoro_exaggeration: null,
    kokoro_style: null,
    kokoro_cfg: null,
  },
  ttsVoicePreview: {
    isLoading: false,
    isPlaying: false,
    audioUrl: null,
    voice: null,
    voiceDisplayName: null,
    profileId: null,
    error: "",
  },
  narration: {
    isOpen: false,
    isNarrating: false,
    isPaused: false,
    currentNarrationSceneId: null,
    currentNarrationVersionId: null,
    currentNarrationSessionId: null,
    elapsedTime: 0,
    duration: null,
    bufferedStartTime: 0,
    bufferedEndTime: 0,
    generatedChunkCount: 0,
    chunkCount: 0,
    speed: 1.0,
    provider: "kokoro",
    voice: null,
    lastProviderUsed: null,
    lastVoiceUsed: null,
    lastModelUsed: null,
    lastGeneratedAudioFile: null,
    narrationCursor: null,
    lastTransitionGapMs: null,
    maxTransitionGapMs: null,
    needsUserResume: false,
    isBuffering: false,
    playbackBlocked: false,
    playbackBlockReason: null,
    statusMessage: "Ready",
  },
  scenesBySession: {},
  selectedSceneId: null,
  selectedVersionId: null,
  selectedVersions: {},
  memoriesBySession: {},
  sessionCharactersBySession: {},
  qualityNotesBySession: {},
  qualityHintsBySession: {},
  storyStateBySession: {},
  foundationsBySession: {},
  storyStateSettings: {
    automatic_story_state: true,
    run_state_extraction_in_background: true,
    state_extraction_timeout_seconds: 120,
  },
  summariesBySession: {},
  sessions: [],
  streamingScene: null,
  worldNotesBySession: {},

  setUiPreset: (presetId) => {
    const normalized = persistUiPresetId(presetId);
    const preset = applyUiPreset(normalized);
    applyDisplaySettings(get().uiSettings);
    set({ uiPresetId: preset.id });
    return preset;
  },

  fetchUiSettings: async () => {
    set({ isLoadingUiSettings: true });
    try {
      const uiSettings = normalizeUiSettings(await api.getUiSettings());
      applyDisplaySettings(uiSettings);
      set({ uiSettings, isLoadingUiSettings: false });
      return uiSettings;
    } catch (error) {
      const uiSettings = normalizeUiSettings(get().uiSettings);
      applyDisplaySettings(uiSettings);
      set({ uiSettings, isLoadingUiSettings: false, lastError: error.message });
      return uiSettings;
    }
  },

  saveUiSettings: async (patch) => {
    const previous = normalizeUiSettings(get().uiSettings);
    const next = normalizeUiSettings({ ...previous, ...patch });
    applyDisplaySettings(next);
    set({ uiSettings: next });
    try {
      const saved = normalizeUiSettings(await api.saveUiSettings(next));
      applyDisplaySettings(saved);
      set({ uiSettings: saved, lastError: "" });
      return saved;
    } catch (error) {
      applyDisplaySettings(previous);
      set({ uiSettings: previous, lastError: error.message });
      throw error;
    }
  },

  fetchBackgrounds: async (refresh = false) => {
    set({ isLoadingBackgrounds: true, backgroundMessage: "" });
    try {
      const backgroundLibrary = refresh ? await api.refreshBackgrounds() : await api.listBackgrounds();
      const availableIds = new Set((backgroundLibrary.items || []).map((item) => item.id));
      const selectedIds = (get().uiSettings.background_selected_ids || []).filter((id) => availableIds.has(id));
      set({ backgroundLibrary, isLoadingBackgrounds: false });
      if (selectedIds.length !== (get().uiSettings.background_selected_ids || []).length) {
        await get().saveUiSettings({ background_selected_ids: selectedIds });
      }
      return backgroundLibrary;
    } catch (error) {
      set({ isLoadingBackgrounds: false, backgroundMessage: error.message });
      return null;
    }
  },

  uploadBackgrounds: async (files) => {
    const accepted = Array.from(files || []);
    if (!accepted.length) return null;
    set({ isLoadingBackgrounds: true, backgroundMessage: "" });
    try {
      let response = null;
      for (const file of accepted) response = await api.uploadBackground(file);
      set({ backgroundLibrary: response || get().backgroundLibrary, isLoadingBackgrounds: false, backgroundMessage: `${accepted.length} background${accepted.length === 1 ? "" : "s"} added.` });
      return response;
    } catch (error) {
      set({ isLoadingBackgrounds: false, backgroundMessage: error.message });
      throw error;
    }
  },

  renameBackground: async (itemId, displayName) => {
    const response = await api.renameBackground(itemId, displayName);
    set({ backgroundLibrary: response, backgroundMessage: "Background renamed." });
    return response;
  },

  deleteBackground: async (itemId) => {
    await api.deleteBackground(itemId);
    const selectedIds = (get().uiSettings.background_selected_ids || []).filter((id) => id !== itemId);
    await get().saveUiSettings({ background_selected_ids: selectedIds });
    return get().fetchBackgrounds(true);
  },

  getActiveUiPreset: () => getUiPreset(normalizeUiPresetId(get().uiPresetId)),

  fetchCustomUiPresets: async () => {
    set({ isLoadingUiPresets: true, uiPresetMessage: "" });
    try {
      const response = await api.listCustomUiPresets();
      const rawPresets = response?.presets || [];
      const disabledPresetCount = Array.isArray(rawPresets)
        ? rawPresets.filter((preset) => preset?.disabled || preset?.validationError).length
        : 0;
      const customPresets = setCustomUiPresets(rawPresets);
      const availableUiPresets = getAllUiPresets();
      const activePreset = getUiPreset(get().uiPresetId);
      set({
        availableUiPresets,
        customUiPresets: customPresets,
        isLoadingUiPresets: false,
        uiPresetId: activePreset.id,
        uiPresetMessage: disabledPresetCount
          ? `${disabledPresetCount} custom preset${disabledPresetCount === 1 ? "" : "s"} failed validation and were disabled.`
          : customPresets.length
            ? `${customPresets.length} custom preset${customPresets.length === 1 ? "" : "s"} loaded.`
            : "",
      });
      applyUiPreset(activePreset.id);
      applyDisplaySettings(get().uiSettings);
      return customPresets;
    } catch (error) {
      set({ isLoadingUiPresets: false, uiPresetMessage: error.message });
      return [];
    }
  },

  validateUiPreset: async (preset) => api.validateUiPreset(preset),

  importUiPreset: async (preset, { apply = false, conflictStrategy = "error" } = {}) => {
    set({ isLoadingUiPresets: true, uiPresetMessage: "" });
    try {
      const response = await api.importUiPreset(preset, conflictStrategy);
      const customPresets = setCustomUiPresets(response?.presets || []);
      const availableUiPresets = getAllUiPresets();
      const importedPreset = response?.preset;
      const activeId = apply && importedPreset?.id ? importedPreset.id : get().uiPresetId;
      const activePreset = apply ? get().setUiPreset(activeId) : getUiPreset(activeId);
      set({
        availableUiPresets,
        customUiPresets: customPresets,
        isLoadingUiPresets: false,
        uiPresetId: activePreset.id,
        uiPresetMessage: importedPreset ? `Imported ${importedPreset.displayName || importedPreset.name}.` : "Preset imported.",
      });
      return response;
    } catch (error) {
      set({ isLoadingUiPresets: false, uiPresetMessage: error.message });
      throw error;
    }
  },

  duplicateUiPreset: async (preset, displayName = null) => {
    set({ isLoadingUiPresets: true, uiPresetMessage: "" });
    try {
      const response = await api.duplicateUiPreset(preset, displayName);
      const customPresets = setCustomUiPresets(response?.presets || []);
      const availableUiPresets = getAllUiPresets();
      const duplicatedPreset = response?.preset;
      if (duplicatedPreset?.id) {
        get().setUiPreset(duplicatedPreset.id);
      }
      set({
        availableUiPresets,
        customUiPresets: customPresets,
        isLoadingUiPresets: false,
        uiPresetId: duplicatedPreset?.id || get().uiPresetId,
        uiPresetMessage: duplicatedPreset ? `Duplicated ${duplicatedPreset.displayName || duplicatedPreset.name}.` : "Preset duplicated.",
      });
      return response;
    } catch (error) {
      set({ isLoadingUiPresets: false, uiPresetMessage: error.message });
      throw error;
    }
  },

  exportUiPreset: async (preset) => {
    try {
      const response = await api.exportUiPreset(preset);
      set({ uiPresetMessage: response?.export_path ? `Exported preset to ${response.export_path}` : "Preset exported." });
      return response;
    } catch (error) {
      set({ uiPresetMessage: error.message });
      throw error;
    }
  },

  deleteCustomUiPreset: async (presetId) => {
    try {
      const response = await api.deleteCustomUiPreset(presetId);
      const customPresets = setCustomUiPresets(response?.presets || []);
      const availableUiPresets = getAllUiPresets();
      let activeId = get().uiPresetId;
      if (activeId === presetId) {
        activeId = "default";
        get().setUiPreset(activeId);
      }
      set({
        availableUiPresets,
        customUiPresets: customPresets,
        uiPresetId: activeId,
        uiPresetMessage: "Custom preset deleted.",
      });
      return response;
    } catch (error) {
      set({ uiPresetMessage: error.message });
      throw error;
    }
  },

  checkHealth: async () => {
    try {
      const health = await api.health();
      set((state) => ({
        health: { checked: true, ok: health.ok, app: health.app },
        lastError: state.lastError.startsWith("StoryDriver backend is offline") ? "" : state.lastError,
      }));
    } catch (error) {
      set({ health: { checked: true, ok: false }, lastError: error.message });
    }
  },

  fetchDiagnostics: async () => {
    try {
      const diagnostics = await api.diagnostics();
      set({ diagnostics });
      return diagnostics;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  fetchStorageMaintenance: async () => {
    set({ isLoadingStorageMaintenance: true });
    try {
      const storageMaintenance = await api.storageMaintenance();
      set({ isLoadingStorageMaintenance: false, storageMaintenance });
      return storageMaintenance;
    } catch (error) {
      set({ isLoadingStorageMaintenance: false, lastError: error.message });
      return null;
    }
  },

  resumeDeleteJob: async (jobId) => {
    if (!jobId) return null;
    try {
      const job = await api.resumeDeleteJob(jobId);
      set((state) => ({
        deleteJobsById: { ...state.deleteJobsById, [jobId]: job },
        lastError: "",
      }));
      get().pollDeleteJob(jobId, job.session_id);
      get().fetchStorageMaintenance().catch(() => {});
      return job;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchSessions: async () => {
    set({ isLoadingSessions: true });
    try {
      const sessions = await api.listSessions();
      const currentActive = get().activeSessionId;
      const activeSessionId =
        sessions.some((session) => session.id === currentActive) ? currentActive : sessions[0]?.id || null;
      set({ sessions: sortSessions(sessions), activeSessionId, isLoadingSessions: false });
      if (activeSessionId) {
        await get().fetchScenes(activeSessionId);
        await get().fetchStoryContext(activeSessionId);
        get().fetchQualityHints(activeSessionId).catch(() => {});
      } else {
        await get().fetchCharacters();
      }
    } catch (error) {
      set({ isLoadingSessions: false, lastError: error.message });
    }
  },

  fetchScenes: async (sessionId) => {
    const requestId = (sceneRequestSequences.get(sessionId) || 0) + 1;
    sceneRequestSequences.set(sessionId, requestId);
    if (get().activeSessionId === sessionId) set({ isLoadingScenes: true });
    try {
      const scenes = await api.listScenes(sessionId);
      if (sceneRequestSequences.get(sessionId) !== requestId) return scenes;
      const selection = resolveSelection(sessionId, scenes);
      set((state) => {
        const isActive = state.activeSessionId === sessionId;
        return {
          isLoadingScenes: isActive ? false : state.isLoadingScenes,
          scenesBySession: { ...state.scenesBySession, [sessionId]: scenes },
          selectedSceneId: isActive ? selection.sceneId : state.selectedSceneId,
          selectedVersionId: isActive ? selection.versionId : state.selectedVersionId,
          selectedVersions: isActive && selection.sceneId
            ? { ...state.selectedVersions, [selection.sceneId]: selection.versionIndex }
            : state.selectedVersions,
          lastError: isActive ? "" : state.lastError,
        };
      });
      writeStoredSelection(sessionId, selection.sceneId, selection.versionId);
      return scenes;
    } catch (error) {
      if (sceneRequestSequences.get(sessionId) === requestId && get().activeSessionId === sessionId) {
        set({ isLoadingScenes: false, lastError: error.message });
      }
      return [];
    }
  },

  createSession: async (title = "Untitled Story") => {
    set({ isCreatingSession: true });
    try {
      const session = await api.createSession(title);
      set((state) => ({
        activeSessionId: session.id,
        isCreatingSession: false,
        sessions: sortSessions([session, ...state.sessions]),
        scenesBySession: { ...state.scenesBySession, [session.id]: [] },
        sessionCharactersBySession: { ...state.sessionCharactersBySession, [session.id]: [] },
        foundationsBySession: { ...state.foundationsBySession, [session.id]: null },
        storyStateBySession: { ...state.storyStateBySession, [session.id]: null },
        storyStateSettings: state.storyStateSettings,
        memoriesBySession: { ...state.memoriesBySession, [session.id]: [] },
        qualityNotesBySession: { ...state.qualityNotesBySession, [session.id]: [] },
        qualityHintsBySession: { ...state.qualityHintsBySession, [session.id]: null },
        summariesBySession: { ...state.summariesBySession, [session.id]: null },
        selectedSceneId: null,
        selectedVersionId: null,
        lastError: "",
      }));
      await get().fetchStoryContext(session.id);
    } catch (error) {
      set({ isCreatingSession: false, lastError: error.message });
    }
  },

  updateSessionTitle: async (sessionId, title) => {
    try {
      const session = await api.updateSession(sessionId, title);
      set((state) => ({
        sessions: mergeSession(state.sessions, session),
        lastError: "",
      }));
      return session;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  refreshSession: async (sessionId) => {
    try {
      const session = await api.getSession(sessionId);
      set((state) => ({
        sessions: mergeSession(state.sessions, session),
        lastError: "",
      }));
      return session;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  retryAutoTitleSession: async (sessionId) => {
    try {
      const session = await api.autoTitleSession(sessionId, {});
      set((state) => ({
        sessions: mergeSession(state.sessions, session),
        lastError: "",
      }));
      return session;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  pollAutoTitleSession: (sessionId, attempts = 45) => {
    const poll = async (remaining) => {
      try {
        const session = await api.getSession(sessionId);
        set((state) => ({
          sessions: mergeSession(state.sessions, session),
        }));
        if (remaining > 0 && sessionNeedsAutoTitlePolling(session)) {
          window.setTimeout(() => poll(remaining - 1), 1500);
        }
      } catch {
        // Auto-title polling should never interrupt writing.
      }
    };
    window.setTimeout(() => poll(attempts), 900);
  },

  pollDeleteJob: (jobId, sessionId, attempts = 240) => {
    if (!jobId) return;
    const poll = async (remaining) => {
      try {
        const job = await api.getDeleteJob(jobId);
        set((state) => ({
          deleteJobsById: { ...state.deleteJobsById, [jobId]: job },
        }));
        if (job.status === "failed") {
          set({
            lastError: `Delete failed for "${job.title || "story"}": ${job.error || "see logs for details"}`,
          });
          get().fetchSessions().catch(() => {});
          get().fetchStorageMaintenance().catch(() => {});
          return;
        }
        if (job.status === "completed") {
          get().fetchStorageMaintenance().catch(() => {});
        } else if (remaining > 0) {
          window.setTimeout(() => poll(remaining - 1), 1500);
        }
      } catch {
        if (remaining > 0) {
          window.setTimeout(() => poll(remaining - 1), 2500);
        }
      }
    };
    set((state) => ({
      deleteJobsById: {
        ...state.deleteJobsById,
        [jobId]: {
          job_id: jobId,
          session_id: sessionId,
          status: "queued",
          stage: "queued",
          message: "Deleting story in the background",
        },
      },
    }));
    window.setTimeout(() => poll(attempts), 900);
  },

  deleteSession: async (sessionId, options = {}) => {
    const stateBeforeDelete = get();
    const sessionSnapshot = stateBeforeDelete.sessions.find((session) => session.id === sessionId) || null;
    const wasActiveBeforeDelete = stateBeforeDelete.activeSessionId === sessionId;
    const previousSelection = {
      activeSessionId: stateBeforeDelete.activeSessionId,
      selectedSceneId: stateBeforeDelete.selectedSceneId,
      selectedVersionId: stateBeforeDelete.selectedVersionId,
    };
    try {
      set((state) => {
        const deletedIndex = state.sessions.findIndex((session) => session.id === sessionId);
        const sessions = sortSessions(state.sessions.filter((session) => session.id !== sessionId));
        const deletedActive = state.activeSessionId === sessionId;
        const activeSessionId = deletedActive
          ? (sessions[deletedIndex] || sessions[Math.max(0, deletedIndex - 1)] || sessions[0] || null)?.id || null
          : state.activeSessionId;
        const scenesBySession = { ...state.scenesBySession };
        const sessionCharactersBySession = { ...state.sessionCharactersBySession };
        const worldNotesBySession = { ...state.worldNotesBySession };
        const memoriesBySession = { ...state.memoriesBySession };
        const qualityNotesBySession = { ...state.qualityNotesBySession };
        const qualityHintsBySession = { ...state.qualityHintsBySession };
        const storyStateBySession = { ...state.storyStateBySession };
        const summariesBySession = { ...state.summariesBySession };
        const foundationsBySession = { ...state.foundationsBySession };
        delete scenesBySession[sessionId];
        delete sessionCharactersBySession[sessionId];
        delete worldNotesBySession[sessionId];
        delete memoriesBySession[sessionId];
        delete qualityNotesBySession[sessionId];
        delete qualityHintsBySession[sessionId];
        delete storyStateBySession[sessionId];
        delete summariesBySession[sessionId];
        delete foundationsBySession[sessionId];
        return {
          sessions,
          activeSessionId,
          scenesBySession,
          sessionCharactersBySession,
          worldNotesBySession,
          memoriesBySession,
          qualityNotesBySession,
          qualityHintsBySession,
          storyStateBySession,
          summariesBySession,
          foundationsBySession,
          selectedSceneId: deletedActive ? null : state.selectedSceneId,
          selectedVersionId: deletedActive ? null : state.selectedVersionId,
          lastError: "",
        };
      });
      const result = await api.deleteSession(sessionId, options);
      if (result?.job_id) {
        get().pollDeleteJob(result.job_id, sessionId);
      }
      const nextActive = get().activeSessionId;
      if (nextActive === sessionId) {
        return result;
      }
      if (nextActive && wasActiveBeforeDelete && !get().scenesBySession[nextActive]) {
        await get().fetchScenes(nextActive);
      }
      if (nextActive && wasActiveBeforeDelete && !get().sessionCharactersBySession[nextActive]) {
        await get().fetchStoryContext(nextActive);
      }
      if (nextActive && wasActiveBeforeDelete) {
        get().fetchQualityHints(nextActive).catch(() => {});
      }
      return result;
    } catch (error) {
      if (sessionSnapshot) {
        set((state) => ({
          sessions: sortSessions([sessionSnapshot, ...state.sessions.filter((session) => session.id !== sessionId)]),
          activeSessionId: wasActiveBeforeDelete ? previousSelection.activeSessionId : state.activeSessionId,
          selectedSceneId: wasActiveBeforeDelete ? previousSelection.selectedSceneId : state.selectedSceneId,
          selectedVersionId: wasActiveBeforeDelete ? previousSelection.selectedVersionId : state.selectedVersionId,
          lastError: error.message,
        }));
        if (wasActiveBeforeDelete) {
          get().fetchScenes(sessionId).catch(() => {});
          get().fetchStoryContext(sessionId).catch(() => {});
        }
      } else {
        set({ lastError: error.message });
      }
      throw error;
    }
  },

  archiveSession: async (sessionId) => {
    try {
      const archived = await api.archiveSession(sessionId);
      set((state) => {
        const sessions = sortSessions(state.sessions.filter((session) => session.id !== sessionId));
        const activeSessionId =
          state.activeSessionId === sessionId ? sessions[0]?.id || null : state.activeSessionId;
        return {
          sessions,
          activeSessionId,
          selectedSceneId: state.activeSessionId === sessionId ? null : state.selectedSceneId,
          selectedVersionId: state.activeSessionId === sessionId ? null : state.selectedVersionId,
          lastError: "",
        };
      });
      const nextActive = get().activeSessionId;
      if (nextActive && !get().scenesBySession[nextActive]) {
        await get().fetchScenes(nextActive);
      }
      if (nextActive && !get().sessionCharactersBySession[nextActive]) {
        await get().fetchStoryContext(nextActive);
      }
      if (nextActive) {
        get().fetchQualityHints(nextActive).catch(() => {});
      }
      return archived;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  restoreSession: async (sessionId) => {
    try {
      const restored = await api.restoreSession(sessionId);
      set((state) => ({
        sessions: sortSessions([restored, ...state.sessions.filter((session) => session.id !== restored.id)]),
        activeSessionId: restored.id,
        lastError: "",
      }));
      await get().fetchScenes(restored.id);
      await get().fetchStoryContext(restored.id);
      return restored;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  generateScene: async ({ sessionId, directorNote, mode, targetSceneId = null }) => {
    if (get().isGenerating) {
      return null;
    }
    if (get().narration.isNarrating || get().narration.isPaused || get().narration.isBuffering) {
      ttsController.stop({ reason: "director_note", preserveCursor: true });
    }
    set((state) => ({
      isGenerating: true,
      lastError: "",
      selectedSceneId: targetSceneId || state.selectedSceneId,
      streamingScene: {
        id: `streaming-${Date.now()}`,
        session_id: sessionId,
        target_scene_id: targetSceneId,
        director_note: directorNote,
        generated_text: "",
        mode,
        status_message: "Preparing context",
        stage: "preparing_context",
      },
    }));
    let streamBuffer = "";
    let streamFlushTimer = null;
    const flushStreamBuffer = () => {
      if (streamFlushTimer) {
        window.clearTimeout(streamFlushTimer);
        streamFlushTimer = null;
      }
      if (!streamBuffer) return;
      const text = streamBuffer;
      streamBuffer = "";
      set((state) => ({
        streamingScene: state.streamingScene
          ? {
              ...state.streamingScene,
              generated_text: `${state.streamingScene.generated_text}${text}`,
              status_message: "Writing scene",
              stage: "writing_scene",
            }
          : state.streamingScene,
      }));
    };
    const scheduleStreamFlush = () => {
      if (streamFlushTimer) return;
      streamFlushTimer = window.setTimeout(flushStreamBuffer, 45);
    };
    try {
      const priorSessionTitle = get().sessions.find((session) => session.id === sessionId)?.title || "";
      const clientSubmittedAt = new Date().toISOString();
      const scene = await api.generateSceneStream(
        sessionId,
        {
          director_note: directorNote,
          mode,
          target_scene_id: targetSceneId,
          client_submitted_at: clientSubmittedAt,
        },
        {
          onDelta: (text) => {
            streamBuffer += text || "";
            scheduleStreamFlush();
          },
          onReplace: (text) => {
            flushStreamBuffer();
            set((state) => ({
              streamingScene: state.streamingScene
                ? {
                    ...state.streamingScene,
                    generated_text: text || "",
                    status_message: "Refining scene",
                    stage: "refining_scene",
                  }
                : state.streamingScene,
            }));
          },
          onStatus: (event) => {
            flushStreamBuffer();
            set((state) => ({
              streamingScene: state.streamingScene
                ? {
                    ...state.streamingScene,
                    status_message: event.message || state.streamingScene.status_message,
                    stage: event.stage || state.streamingScene.stage,
                  }
                : state.streamingScene,
            }));
          },
        },
      );
      flushStreamBuffer();
      const savedVersion = latestVersion(scene);
      set((state) => {
        const nextScenes =
          mode === "continue"
            ? [...(state.scenesBySession[sessionId] || []), scene]
            : (state.scenesBySession[sessionId] || []).map((existing) =>
                existing.id === scene.id ? scene : existing,
              );
        const version = savedVersion;
        writeStoredSelection(sessionId, scene.id, version?.id || scene.active_version_id);
        const isActive = state.activeSessionId === sessionId;
        return {
          isGenerating: false,
          streamingScene: null,
          scenesBySession: { ...state.scenesBySession, [sessionId]: nextScenes },
          selectedSceneId: isActive ? scene.id : state.selectedSceneId,
          selectedVersionId: isActive ? version?.id || scene.active_version_id : state.selectedVersionId,
          selectedVersions: isActive
            ? { ...state.selectedVersions, [scene.id]: scene.active_version_index }
            : state.selectedVersions,
          sessions: sortSessions(
            state.sessions.map((session) =>
              session.id === sessionId ? { ...session, updated_at: scene.created_at } : session,
            ),
          ),
          lastError: "",
        };
      });
      if (get().ttsSettings.auto_read_new_scenes && get().activeSessionId === sessionId) {
        get().startNarrationForVersion(scene, savedVersion).catch((error) => {
          set({ lastError: error.message });
        });
      }
      get().fetchQualityHints(sessionId).catch(() => {});
      window.setTimeout(() => {
        get().fetchStoryState(sessionId).catch(() => {});
      }, 5000);
      window.setTimeout(() => {
        get().fetchStoryContext(sessionId).catch(() => {});
        get().fetchQualityHints(sessionId).catch(() => {});
      }, 7000);
      if (mode === "continue" && isGenericStoryTitle(priorSessionTitle)) {
        get().pollAutoTitleSession(sessionId);
      }
      return scene;
    } catch (error) {
      if (streamFlushTimer) {
        window.clearTimeout(streamFlushTimer);
      }
      flushStreamBuffer();
      set((state) => ({
        isGenerating: false,
        streamingScene: state.streamingScene
          ? {
              ...state.streamingScene,
              stage: "failed",
              status_message: "Generation failed",
              error: error.message,
            }
          : null,
        lastError: error.message,
      }));
      throw error;
    }
  },

  selectScene: (sceneId, versionId = null) =>
    set((state) => {
      const scenes = state.scenesBySession[state.activeSessionId] || [];
      const scene = scenes.find((item) => item.id === sceneId);
      const version = versionId
        ? scene?.versions?.find((item) => item.id === versionId)
        : latestVersion(scene);
      const nextVersionId = version?.id || scene?.active_version_id || null;
      const nextVersionIndex = version?.version_index || scene?.active_version_index || 1;
      writeStoredSelection(state.activeSessionId, sceneId, nextVersionId);
      return {
        selectedSceneId: sceneId,
        selectedVersionId: nextVersionId,
        selectedVersions: { ...state.selectedVersions, [sceneId]: nextVersionIndex },
      };
    }),

  setSceneVersion: (sceneId, versionIndex) =>
    set((state) => {
      const scenes = state.scenesBySession[state.activeSessionId] || [];
      const scene = scenes.find((item) => item.id === sceneId);
      const version = scene?.versions?.find((item) => item.version_index === versionIndex);
      const versionId = version?.id || scene?.active_version_id || null;
      writeStoredSelection(state.activeSessionId, sceneId, versionId);
      return {
        selectedSceneId: sceneId,
        selectedVersionId: versionId,
        selectedVersions: { ...state.selectedVersions, [sceneId]: versionIndex },
      };
    }),

  fetchModelSettings: async () => {
    set({ isLoadingSettings: true });
    try {
      const [modelSettings, modelPresets] = await Promise.all([
        api.getModelSettings(),
        api.listModelPresets(),
      ]);
      let taskRouting = null;
      try {
        taskRouting = await api.getTaskModelProfiles();
      } catch {
        taskRouting = null;
      }
      set({
        isLoadingSettings: false,
        modelSettings,
        modelPresets,
        taskModelProfiles: taskRouting?.profiles || get().taskModelProfiles,
        taskModelResolved: taskRouting?.resolved || get().taskModelResolved,
        taskModelTaskTypes: taskRouting?.task_types || get().taskModelTaskTypes,
        lastError: "",
      });
    } catch (error) {
      set({ isLoadingSettings: false, lastError: error.message });
    }
  },

  refreshModels: async () => {
    set({ isRefreshingModels: true, lastError: "" });
    try {
      const models = await api.listModels();
      set({ isRefreshingModels: false, models, lastError: "" });
      return models;
    } catch (error) {
      set({ isRefreshingModels: false, lastError: error.message });
      throw error;
    }
  },

  saveModelSettings: async (settings) => {
    set({ modelSettings: settings });
    try {
      const saved = await api.saveModelSettings(settings);
      set({ modelSettings: saved, lastError: "" });
      return saved;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchTaskModelProfiles: async () => {
    try {
      const routing = await api.getTaskModelProfiles();
      set({
        taskModelProfiles: routing.profiles || {},
        taskModelResolved: routing.resolved || {},
        taskModelTaskTypes: routing.task_types || [],
        lastError: "",
      });
      return routing;
    } catch (error) {
      set({ lastError: error.message });
      return {
        profiles: get().taskModelProfiles,
        resolved: get().taskModelResolved,
        task_types: get().taskModelTaskTypes,
      };
    }
  },

  saveTaskModelProfile: async (taskType, profile) => {
    try {
      const routing = await api.saveTaskModelProfile(taskType, profile);
      set({
        taskModelProfiles: routing.profiles || {},
        taskModelResolved: routing.resolved || {},
        taskModelTaskTypes: routing.task_types || [],
        lastError: "",
      });
      return routing;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  resetTaskModelProfile: async (taskType) => {
    try {
      const routing = await api.resetTaskModelProfile(taskType);
      set({
        taskModelProfiles: routing.profiles || {},
        taskModelResolved: routing.resolved || {},
        taskModelTaskTypes: routing.task_types || [],
        lastError: "",
      });
      return routing;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchProsePromptPreview: async (sessionId, payload = {}) => {
    const targetSessionId = sessionId || get().activeSessionId || get().sessions[0]?.id || null;
    if (!targetSessionId) {
      set({
        prosePromptPreview: null,
        prosePromptPreviewError: "Choose a story before previewing the prose prompt.",
      });
      return null;
    }
    const requestId = ++promptPreviewRequestSequence;
    const requestedSceneId = payload.target_scene_id || get().selectedSceneId || null;
    const requestedVersionId = get().selectedVersionId || null;
    set({ isLoadingProsePromptPreview: true, prosePromptPreviewError: "", prosePromptPreview: null });
    try {
      const preview = await api.getProsePromptPreview(targetSessionId, payload);
      const current = get();
      if (
        requestId !== promptPreviewRequestSequence ||
        preview?.session_id !== targetSessionId ||
        current.activeSessionId !== targetSessionId ||
        current.selectedSceneId !== requestedSceneId ||
        current.selectedVersionId !== requestedVersionId
      ) {
        return null;
      }
      set({
        prosePromptPreview: preview,
        prosePromptPreviewError: "",
        isLoadingProsePromptPreview: false,
      });
      return preview;
    } catch (error) {
      if (requestId !== promptPreviewRequestSequence || get().activeSessionId !== targetSessionId) return null;
      set({
        prosePromptPreviewError: error.message,
        isLoadingProsePromptPreview: false,
      });
      return null;
    }
  },

  fetchTTSSettings: async () => {
    try {
      const ttsSettings = await api.getTTSSettings();
      set((state) => ({
        ttsSettings,
        narration: {
          ...state.narration,
          speed: ttsSettings.tts_speed,
          provider: ttsSettings.tts_provider,
          voice: ttsSettings.tts_voice,
        },
        lastError: "",
      }));
      ttsController.setSpeed(ttsSettings.tts_speed);
      return ttsSettings;
    } catch (error) {
      set({ lastError: error.message });
      return get().ttsSettings;
    }
  },

  fetchTTSStatus: async () => {
    try {
      const ttsStatus = await api.ttsStatus();
      const profiles = ttsStatus?.tts_voice_profiles || ttsStatus?.kokoro?.voice_profiles || null;
      set((state) => ({
        ttsStatus,
        ttsSettings: profiles?.length
          ? { ...state.ttsSettings, tts_voice_profiles: profiles }
          : state.ttsSettings,
      }));
      return ttsStatus;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  fetchKokoroVoices: async () => {
    try {
      const voices = await api.ttsVoices("kokoro");
      const kokoroVoices = Array.isArray(voices.voices)
        ? voices.voices
        : Array.isArray(voices.kokoro)
          ? voices.kokoro
          : ["af_heart"];
      const voiceProfiles = voices.voice_profiles || voices.profiles || [];
      set((state) => ({
        kokoroVoices,
        ttsSettings: voiceProfiles.length
          ? {
              ...state.ttsSettings,
              tts_voice_profiles: [
                ...voiceProfiles,
                ...(state.ttsSettings.tts_voice_profiles || []).filter(
                  (profile) => !voiceProfiles.some((item) => item.id === profile.id),
                ),
              ],
            }
          : state.ttsSettings,
      }));
      return kokoroVoices;
    } catch (error) {
      set({ kokoroVoices: ["af_heart"] });
      return ["af_heart"];
    }
  },

  saveTTSSettings: async (settings) => {
    const nextSettings = { ...get().ttsSettings, ...settings };
    set((state) => ({
      ttsSettings: nextSettings,
      narration: {
        ...state.narration,
        speed: nextSettings.tts_speed,
        provider: nextSettings.tts_provider,
        voice: nextSettings.tts_voice,
      },
    }));
    ttsController.setSpeed(nextSettings.tts_speed);
    try {
      const saved = await api.saveTTSSettings(nextSettings);
      set((state) => ({
        ttsSettings: saved,
        narration: {
          ...state.narration,
          speed: saved.tts_speed,
          provider: saved.tts_provider,
          voice: saved.tts_voice,
        },
        lastError: "",
      }));
      return saved;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  /* Archived image workflow actions. Images are intentionally absent from the normal product path.
  fetchImageSettings: async () => {
    try {
      const imageSettings = await api.getImageSettings();
      set({ imageSettings, lastError: "" });
      return imageSettings;
    } catch (error) {
      set({ lastError: error.message });
      return get().imageSettings;
    }
  },

  saveImageSettings: async (settings) => {
    const nextSettings = { ...get().imageSettings, ...settings };
    const shouldRefreshWorkflows = Object.prototype.hasOwnProperty.call(settings, "selected_workflow_id");
    const shouldRefreshResourceStatus = [
      "image_generation_mode",
      "comfyui_base_url",
      "image_resource_mode",
      "lm_unload_policy",
      "lm_reload_policy",
      "lm_reload_use_fast_profile",
      "lm_reload_prefer_cli",
      "lm_reload_parallel",
      "lm_reload_context_length",
      "lm_reload_gpu_offload",
      "free_comfyui_memory_after_generation",
      "comfyui_idle_cleanup_mode",
      "free_comfyui_before_writing",
      "comfyui_free_throttle_seconds",
      "comfyui_regenerate_grace_seconds",
      "fast_regenerate_window_enabled",
      "regenerate_warm_window_seconds",
      "free_comfyui_after_regenerate_window",
      "reload_lm_after_regenerate_window",
      "auto_reload_lm_after_image",
    ].some((key) => Object.prototype.hasOwnProperty.call(settings, key));
    set({ imageSettings: nextSettings });
    try {
      const saved = await api.saveImageSettings(nextSettings);
      set({ imageSettings: saved, lastError: "" });
      if (shouldRefreshWorkflows) {
        await get().fetchImageWorkflows();
      }
      if (shouldRefreshResourceStatus) {
        await get().fetchResourceStatus();
      }
      return saved;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchImageWorkflows: async () => {
    try {
      const response = await api.listImageWorkflowsForSession(get().activeSessionId);
      set({
        imageWorkflows: response.workflows || [],
        workflowFolder: response.workflow_folder || "",
        imageSettings: response.image_settings || get().imageSettings,
        lastError: "",
      });
      return response;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  refreshImageWorkflows: async () => {
    set({ isRefreshingImageWorkflows: true });
    try {
      const response = await api.listImageWorkflowsForSession(get().activeSessionId);
      set({
        isRefreshingImageWorkflows: false,
        imageWorkflows: response.workflows || [],
        workflowFolder: response.workflow_folder || "",
        imageSettings: response.image_settings || get().imageSettings,
        lastError: "",
      });
      return response;
    } catch (error) {
      set({ isRefreshingImageWorkflows: false, lastError: error.message });
      throw error;
    }
  },

  importImageWorkflow: async ({ filename, content }) => {
    set({ isRefreshingImageWorkflows: true });
    try {
      const response = await api.importImageWorkflow({ filename, content }, get().activeSessionId);
      set({
        isRefreshingImageWorkflows: false,
        imageWorkflows: response.workflows || [],
        workflowFolder: response.workflow_folder || "",
        imageSettings: response.image_settings || get().imageSettings,
        lastError: "",
      });
      if (get().activeSessionId) {
        await get().fetchSessionImageSettings(get().activeSessionId);
      }
      return response;
    } catch (error) {
      set({ isRefreshingImageWorkflows: false, lastError: error.message });
      throw error;
    }
  },

  openWorkflowFolder: async () => {
    const fallbackPath = get().workflowFolder || "D:\\StoryDriver\\backend\\data\\comfy_workflows";
    const isLocalDesktop = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
    if (!isLocalDesktop) {
      const copied = await copyTextToClipboard(fallbackPath);
      return {
        ok: true,
        path: fallbackPath,
        opened: false,
        message: copied
          ? "Workflow folder path copied."
          : "Workflow folder path is ready. Copy it from the field if the browser blocks clipboard access.",
      };
    }
    try {
      const response = await api.openSystemFolder("comfy_workflows");
      if (response.path) {
        set({ workflowFolder: response.path });
      }
      return response;
    } catch (error) {
      const copied = await copyTextToClipboard(fallbackPath);
      set({ lastError: error.message });
      return {
        ok: false,
        path: fallbackPath,
        opened: false,
        message: copied ? `${error.message} Path copied instead.` : error.message,
      };
    }
  },

  fetchSessionImageSettings: async (sessionId) => {
    if (!sessionId) return null;
    try {
      const settings = await api.getSessionImageSettings(sessionId);
      set((state) => ({
        sessionImageSettingsBySession: {
          ...state.sessionImageSettingsBySession,
          [sessionId]: settings,
        },
        lastError: "",
      }));
      return settings;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  saveSessionImageSettings: async (sessionId, patch) => {
    if (!sessionId) return null;
    const shouldRefreshWorkflows = Object.prototype.hasOwnProperty.call(patch, "selected_workflow_id");
    const current = get().sessionImageSettingsBySession[sessionId] || {
      session_id: sessionId,
      selected_workflow_id: null,
      image_resource_mode: null,
      image_prompt_style: "",
      preferred_visual_tone: "",
      realism_notes: "",
      lighting_camera_notes: "",
      auto_open_preview: true,
      auto_attach_generated: true,
      generate_image_while_narrating: null,
      auto_image_on_narrate: null,
      default_negative_prompt: "",
    };
    const optimistic = { ...current, ...patch };
    set((state) => ({
      sessionImageSettingsBySession: {
        ...state.sessionImageSettingsBySession,
        [sessionId]: optimistic,
      },
    }));
    try {
      const saved = await api.saveSessionImageSettings(sessionId, patch);
      set((state) => ({
        sessionImageSettingsBySession: {
          ...state.sessionImageSettingsBySession,
          [sessionId]: saved,
        },
        lastError: "",
      }));
      if (shouldRefreshWorkflows) {
        await get().fetchImageWorkflows();
      }
      return saved;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchSceneImageSettings: async (sessionId, sceneId, versionId = null) => {
    if (!sessionId || !sceneId) return null;
    try {
      const settings = await api.getSceneImageSettings(sessionId, sceneId, versionId);
      set((state) => ({
        sceneImageSettingsByKey: {
          ...state.sceneImageSettingsByKey,
          [sceneImageSettingsKey(sceneId, versionId)]: settings,
        },
        lastError: "",
      }));
      return settings;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  saveSceneImageSettings: async (sessionId, sceneId, versionId = null, patch = {}) => {
    if (!sessionId || !sceneId) return null;
    const key = sceneImageSettingsKey(sceneId, versionId);
    const current = get().sceneImageSettingsByKey[key] || {
      session_id: sessionId,
      scene_id: sceneId,
      version_id: versionId,
      selected_workflow_id: null,
    };
    const optimistic = { ...current, ...patch, version_id: versionId };
    set((state) => ({
      sceneImageSettingsByKey: {
        ...state.sceneImageSettingsByKey,
        [key]: optimistic,
      },
    }));
    try {
      const saved = await api.saveSceneImageSettings(sessionId, sceneId, {
        version_id: versionId,
        ...patch,
      });
      set((state) => ({
        sceneImageSettingsByKey: {
          ...state.sceneImageSettingsByKey,
          [key]: saved,
        },
        lastError: "",
      }));
      return saved;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  saveImageWorkflowConfig: async (workflowId, config) => {
    try {
      const response = await api.saveImageWorkflowConfig(workflowId, config);
      set({
        imageWorkflows: response.workflows || [],
        workflowFolder: response.workflow_folder || "",
        imageSettings: response.image_settings || get().imageSettings,
        lastError: "",
      });
      return response;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  validateImageWorkflow: async (workflowId) => {
    try {
      const validation = await api.validateImageWorkflow(workflowId);
      set((state) => ({
        workflowValidation: { ...state.workflowValidation, [workflowId]: validation },
        lastError: validation.valid ? "" : validation.errors.join(" "),
      }));
      return validation;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },
  */

  createModelPreset: async (preset) => {
    try {
      const created = await api.createModelPreset(preset);
      set((state) => ({
        modelPresets: [created, ...state.modelPresets],
        lastError: "",
      }));
      return created;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  updateModelPreset: async (presetId, preset) => {
    try {
      const updated = await api.updateModelPreset(presetId, preset);
      set((state) => ({
        modelPresets: state.modelPresets.map((existing) =>
          existing.id === presetId ? updated : existing,
        ),
        lastError: "",
      }));
      return updated;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  deleteModelPreset: async (presetId) => {
    try {
      await api.deleteModelPreset(presetId);
      set((state) => ({
        modelPresets: state.modelPresets.filter((preset) => preset.id !== presetId),
        modelSettings:
          state.modelSettings?.active_preset_id === presetId
            ? { ...state.modelSettings, active_preset_id: null }
            : state.modelSettings,
        lastError: "",
      }));
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchCharacters: async () => {
    try {
      const characters = await api.listCharacters();
      set({ characters: sortCharacters(characters), lastError: "" });
      return characters;
    } catch (error) {
      set({ lastError: error.message });
      return [];
    }
  },

  fetchSessionCharacters: async (sessionId) => {
    if (!sessionId) return [];
    try {
      const sessionCharacters = await api.listSessionCharacters(sessionId);
      set((state) => ({
        sessionCharactersBySession: {
          ...state.sessionCharactersBySession,
          [sessionId]: sessionCharacters,
        },
        lastError: "",
      }));
      return sessionCharacters;
    } catch (error) {
      set({ lastError: error.message });
      return [];
    }
  },

  fetchWorldNotes: async (sessionId) => {
    if (!sessionId) return null;
    try {
      const worldNotes = await api.getWorldNotes(sessionId);
      set((state) => ({
        worldNotesBySession: { ...state.worldNotesBySession, [sessionId]: worldNotes },
        lastError: "",
      }));
      return worldNotes;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  fetchStoryFoundation: async (sessionId) => {
    if (!sessionId) return null;
    try {
      const foundation = await api.getStoryFoundation(sessionId);
      set((state) => ({
        foundationsBySession: { ...state.foundationsBySession, [sessionId]: foundation },
        lastError: "",
      }));
      return foundation;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  refreshStoryFoundation: async (sessionId, payload = {}) => {
    if (!sessionId) return null;
    set({ isRefreshingFoundation: true });
    try {
      const foundation = await api.refreshStoryFoundation(sessionId, payload);
      set((state) => ({
        isRefreshingFoundation: false,
        foundationsBySession: { ...state.foundationsBySession, [sessionId]: foundation },
        lastError: "",
      }));
      await Promise.all([get().fetchSessionCharacters(sessionId), get().fetchWorldNotes(sessionId), get().fetchCharacters()]);
      return foundation;
    } catch (error) {
      set({ isRefreshingFoundation: false, lastError: error.message });
      throw error;
    }
  },

  updateStoryFoundation: async (sessionId, payload = {}) => {
    if (!sessionId) return null;
    try {
      const foundation = await api.updateStoryFoundation(sessionId, payload);
      set((state) => ({
        foundationsBySession: { ...state.foundationsBySession, [sessionId]: foundation },
        lastError: "",
      }));
      if (payload.apply_to_cards !== false) {
        await Promise.all([get().fetchSessionCharacters(sessionId), get().fetchWorldNotes(sessionId), get().fetchCharacters()]);
      }
      return foundation;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchQualityNotes: async (sessionId) => {
    if (!sessionId) return [];
    try {
      const notes = await api.listQualityNotes(sessionId);
      set((state) => ({
        qualityNotesBySession: { ...state.qualityNotesBySession, [sessionId]: notes },
        lastError: "",
      }));
      return notes;
    } catch (error) {
      set({ lastError: error.message });
      return [];
    }
  },

  addQualityNote: async (sessionId, note) => {
    if (!sessionId) return null;
    try {
      const created = await api.createQualityNote(sessionId, note);
      set((state) => ({
        qualityNotesBySession: {
          ...state.qualityNotesBySession,
          [sessionId]: [created, ...(state.qualityNotesBySession[sessionId] || [])],
        },
        lastError: "",
      }));
      get().fetchQualityHints(sessionId).catch(() => {});
      return created;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  updateQualityNote: async (sessionId, noteId, patch) => {
    if (!sessionId || !noteId) return null;
    try {
      const updated = await api.updateQualityNote(sessionId, noteId, patch);
      set((state) => ({
        qualityNotesBySession: {
          ...state.qualityNotesBySession,
          [sessionId]: (state.qualityNotesBySession[sessionId] || []).map((note) =>
            note.id === noteId ? updated : note,
          ),
        },
        lastError: "",
      }));
      get().fetchQualityHints(sessionId).catch(() => {});
      return updated;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  deleteQualityNote: async (sessionId, noteId) => {
    if (!sessionId || !noteId) return null;
    try {
      await api.deleteQualityNote(sessionId, noteId);
      set((state) => ({
        qualityNotesBySession: {
          ...state.qualityNotesBySession,
          [sessionId]: (state.qualityNotesBySession[sessionId] || []).filter((note) => note.id !== noteId),
        },
        lastError: "",
      }));
      get().fetchQualityHints(sessionId).catch(() => {});
      return true;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  fetchQualityHints: async (sessionId) => {
    if (!sessionId) return null;
    try {
      const hints = await api.getQualityHints(sessionId);
      set((state) => ({
        qualityHintsBySession: { ...state.qualityHintsBySession, [sessionId]: hints },
        lastError: "",
      }));
      return hints;
    } catch {
      return null;
    }
  },

  fetchMemoryScaffold: async (sessionId) => {
    if (!sessionId) return { memories: [], summary: null };
    try {
      const [memories, summary] = await Promise.all([
        api.listMemories(sessionId),
        api.getSummary(sessionId),
      ]);
      set((state) => ({
        memoriesBySession: { ...state.memoriesBySession, [sessionId]: memories },
        summariesBySession: { ...state.summariesBySession, [sessionId]: summary },
        lastError: "",
      }));
      return { memories, summary };
    } catch (error) {
      set({ lastError: error.message });
      return { memories: [], summary: null };
    }
  },

  refreshSummary: async (sessionId) => {
    if (!sessionId) return null;
    set({ isRefreshingSummary: true, lastError: "" });
    try {
      const response = await api.refreshSummary(sessionId);
      set((state) => ({
        isRefreshingSummary: false,
        summariesBySession: { ...state.summariesBySession, [sessionId]: response?.summary || null },
        storyStateBySession:
          response?.summary_status && state.storyStateBySession[sessionId]
            ? {
                ...state.storyStateBySession,
                [sessionId]: {
                  ...state.storyStateBySession[sessionId],
                  summary_status: response.summary_status,
                },
              }
            : state.storyStateBySession,
        lastError: "",
      }));
      await get().fetchStoryState(sessionId);
      get().fetchQualityHints(sessionId).catch(() => {});
      return response;
    } catch (error) {
      set({ isRefreshingSummary: false, lastError: error.message });
      return null;
    }
  },

  fetchStoryStateSettings: async () => {
    try {
      const storyStateSettings = await api.getStoryStateSettings();
      set({ storyStateSettings, lastError: "" });
      return storyStateSettings;
    } catch (error) {
      set({ lastError: error.message });
      return get().storyStateSettings;
    }
  },

  saveStoryStateSettings: async (patch) => {
    const nextSettings = { ...get().storyStateSettings, ...patch };
    set({ storyStateSettings: nextSettings });
    try {
      const saved = await api.saveStoryStateSettings(nextSettings);
      set({ storyStateSettings: saved, lastError: "" });
      return saved;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  fetchStoryState: async (sessionId) => {
    if (!sessionId) return null;
    set({ isRefreshingStoryState: true });
    try {
      const storyState = await api.getStoryState(sessionId);
      set((state) => ({
        isRefreshingStoryState: false,
        storyStateBySession: { ...state.storyStateBySession, [sessionId]: storyState },
        storyStateSettings: storyState.settings || state.storyStateSettings,
        lastError: "",
      }));
      return storyState;
    } catch (error) {
      set({ isRefreshingStoryState: false, lastError: error.message });
      return null;
    }
  },

  extractStoryStateForSelectedScene: async () => {
    const state = get();
    const scene = state.getSelectedScene();
    const version = state.getSelectedSceneVersion();
    if (!state.activeSessionId || !scene || !version) {
      set({ lastError: "Select a scene before refreshing Story State." });
      return null;
    }
    set({ isRefreshingStoryState: true });
    try {
      const run = await api.extractStoryState(state.activeSessionId, scene.id, version.id);
      await get().fetchStoryState(state.activeSessionId);
      set({ isRefreshingStoryState: false, lastError: "" });
      return run;
    } catch (error) {
      set({ isRefreshingStoryState: false, lastError: error.message });
      return null;
    }
  },

  archiveStoryStateItem: async (itemType, itemId) => {
    const state = get();
    if (!state.activeSessionId || !itemType || !itemId) return null;
    set({ isRefreshingStoryState: true });
    try {
      const result = await api.archiveStoryStateItem(itemType, itemId);
      await get().fetchStoryState(state.activeSessionId);
      set({ isRefreshingStoryState: false, lastError: "" });
      return result;
    } catch (error) {
      set({ isRefreshingStoryState: false, lastError: error.message });
      return null;
    }
  },

  disableStoryStateItem: async (itemType, itemId) => {
    const state = get();
    if (!state.activeSessionId || !itemType || !itemId) return null;
    set({ isRefreshingStoryState: true });
    try {
      const result = await api.disableStoryStateItem(itemType, itemId);
      await get().fetchStoryState(state.activeSessionId);
      set({ isRefreshingStoryState: false, lastError: "" });
      return result;
    } catch (error) {
      set({ isRefreshingStoryState: false, lastError: error.message });
      return null;
    }
  },

  restoreStoryStateItem: async (itemType, itemId) => {
    const state = get();
    if (!state.activeSessionId || !itemType || !itemId) return null;
    set({ isRefreshingStoryState: true });
    try {
      const result = await api.restoreStoryStateItem(itemType, itemId);
      await get().fetchStoryState(state.activeSessionId);
      set({ isRefreshingStoryState: false, lastError: "" });
      return result;
    } catch (error) {
      set({ isRefreshingStoryState: false, lastError: error.message });
      return null;
    }
  },

  updateStoryStateItem: async (itemType, itemId, patch) => {
    const state = get();
    if (!state.activeSessionId || !itemType || !itemId) return null;
    set({ isRefreshingStoryState: true });
    try {
      const result = await api.updateStoryStateItem(itemType, itemId, patch);
      await get().fetchStoryState(state.activeSessionId);
      set({ isRefreshingStoryState: false, lastError: "" });
      return result;
    } catch (error) {
      set({ isRefreshingStoryState: false, lastError: error.message });
      return null;
    }
  },

  undoStoryStateRun: async (runId) => {
    const state = get();
    if (!state.activeSessionId || !runId) return null;
    set({ isRefreshingStoryState: true });
    try {
      const result = await api.undoStoryStateRun(runId);
      await get().fetchStoryState(state.activeSessionId);
      set({ isRefreshingStoryState: false, lastError: "" });
      return result;
    } catch (error) {
      set({ isRefreshingStoryState: false, lastError: error.message });
      return null;
    }
  },

  fetchStoryContext: async (sessionId) => {
    const requestId = ++storyContextRequestSequence;
    if (sessionId && get().activeSessionId === sessionId) set({ isLoadingStoryDetails: true });
    try {
      if (!sessionId) {
        const characters = await api.listCharacters();
        if (requestId === storyContextRequestSequence) {
          set({ characters: sortCharacters(characters), isLoadingStoryDetails: false, lastError: "" });
        }
        return;
      }
      const [characters, sessionCharacters, worldNotes, foundation, memories, summary, storyState, qualityNotes, qualityHints] = await Promise.all([
        api.listCharacters(),
        api.listSessionCharacters(sessionId),
        api.getWorldNotes(sessionId),
        api.getStoryFoundation(sessionId),
        api.listMemories(sessionId),
        api.getSummary(sessionId),
        api.getStoryState(sessionId),
        api.listQualityNotes(sessionId),
        api.getQualityHints(sessionId),
      ]);
      const scopedResponses = [worldNotes, foundation, summary, storyState, qualityHints].filter(Boolean);
      const responseMismatch = scopedResponses.some(
        (response) => response.session_id && response.session_id !== sessionId,
      );
      const linkMismatch = sessionCharacters.some(
        (link) => link.session_id && link.session_id !== sessionId,
      );
      const memoryMismatch = memories.some(
        (memory) => memory.session_id && memory.session_id !== sessionId,
      );
      if (
        requestId !== storyContextRequestSequence ||
        get().activeSessionId !== sessionId ||
        responseMismatch ||
        linkMismatch ||
        memoryMismatch
      ) {
        return null;
      }
      set((state) => ({
        characters: sortCharacters(characters),
        isLoadingStoryDetails: false,
        sessionCharactersBySession: {
          ...state.sessionCharactersBySession,
          [sessionId]: sessionCharacters,
        },
        worldNotesBySession: { ...state.worldNotesBySession, [sessionId]: worldNotes },
        foundationsBySession: { ...state.foundationsBySession, [sessionId]: foundation },
        memoriesBySession: { ...state.memoriesBySession, [sessionId]: memories },
        qualityNotesBySession: { ...state.qualityNotesBySession, [sessionId]: qualityNotes },
        qualityHintsBySession: { ...state.qualityHintsBySession, [sessionId]: qualityHints },
        summariesBySession: { ...state.summariesBySession, [sessionId]: summary },
        storyStateBySession: { ...state.storyStateBySession, [sessionId]: storyState },
        storyStateSettings: storyState.settings || state.storyStateSettings,
        lastError: "",
      }));
      return { foundation, sessionCharacters, worldNotes, storyState };
    } catch (error) {
      if (requestId === storyContextRequestSequence && get().activeSessionId === sessionId) {
        set({ isLoadingStoryDetails: false, lastError: error.message });
      }
      return null;
    }
  },

  createCharacter: async (character) => {
    try {
      const created = await api.createCharacter(character);
      set((state) => ({ characters: sortCharacters([created, ...state.characters]), lastError: "" }));
      if (character.attach_to_session && character.session_id) {
        await get().fetchSessionCharacters(character.session_id);
      }
      return created;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  updateCharacter: async (characterId, patch) => {
    try {
      const updated = await api.updateCharacter(characterId, patch);
      set((state) => ({
        characters: sortCharacters(
          state.characters.map((character) => (character.id === characterId ? updated : character)),
        ),
        lastError: "",
      }));
      const activeSessionId = get().activeSessionId;
      if (activeSessionId) {
        await get().fetchSessionCharacters(activeSessionId);
      }
      return updated;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  repairCharacterVisualProfile: async (characterId, payload) => {
    try {
      const result = await api.repairCharacterVisualProfile(characterId, payload);
      const updated = result.character;
      set((state) => ({
        characters: sortCharacters(
          state.characters.map((character) => (character.id === characterId ? updated : character)),
        ),
        lastError: "",
      }));
      const activeSessionId = get().activeSessionId;
      if (activeSessionId) {
        await get().fetchSessionCharacters(activeSessionId);
      }
      return result;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  refreshCharacter: async (characterId) => {
    try {
      const updated = await api.getCharacter(characterId);
      set((state) => ({
        characters: sortCharacters(
          state.characters.map((character) => (character.id === characterId ? updated : character)),
        ),
        lastError: "",
      }));
      const activeSessionId = get().activeSessionId;
      if (activeSessionId) {
        await get().fetchSessionCharacters(activeSessionId);
      }
      return updated;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  deleteCharacter: async (characterId) => {
    try {
      await api.deleteCharacter(characterId);
      set((state) => {
        const sessionCharactersBySession = Object.fromEntries(
          Object.entries(state.sessionCharactersBySession).map(([sessionId, links]) => [
            sessionId,
            links.filter((link) => link.character_id !== characterId),
          ]),
        );
        return {
          characters: state.characters.filter((character) => character.id !== characterId),
          sessionCharactersBySession,
          lastError: "",
        };
      });
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  attachCharacter: async (sessionId, characterId, isActive = true) => {
    try {
      const attached = await api.attachSessionCharacter(sessionId, characterId, isActive);
      set((state) => ({
        sessionCharactersBySession: {
          ...state.sessionCharactersBySession,
          [sessionId]: [
            attached,
            ...(state.sessionCharactersBySession[sessionId] || []).filter(
              (link) => link.character_id !== characterId,
            ),
          ],
        },
        lastError: "",
      }));
      return attached;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  updateSessionCharacter: async (sessionId, characterId, patch) => {
    try {
      const updated = await api.updateSessionCharacter(sessionId, characterId, patch);
      set((state) => ({
        sessionCharactersBySession: {
          ...state.sessionCharactersBySession,
          [sessionId]: (state.sessionCharactersBySession[sessionId] || []).map((link) =>
            link.character_id === characterId ? updated : link,
          ),
        },
        lastError: "",
      }));
      return updated;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  detachCharacter: async (sessionId, characterId) => {
    try {
      await api.detachSessionCharacter(sessionId, characterId);
      set((state) => ({
        sessionCharactersBySession: {
          ...state.sessionCharactersBySession,
          [sessionId]: (state.sessionCharactersBySession[sessionId] || []).filter(
            (link) => link.character_id !== characterId,
          ),
        },
        lastError: "",
      }));
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  saveWorldNotes: async (sessionId, patch) => {
    try {
      const worldNotes = await api.saveWorldNotes(sessionId, patch);
      set((state) => ({
        worldNotesBySession: { ...state.worldNotesBySession, [sessionId]: worldNotes },
        lastError: "",
      }));
      return worldNotes;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  loadInitial: async () => {
    await get().checkHealth();
    await get().fetchCustomUiPresets();
    await get().fetchSessions();
    await Promise.all([
      get().fetchModelSettings(),
      get().fetchUiSettings(),
      get().fetchDiagnostics(),
      get().fetchTTSSettings(),
      get().fetchTTSStatus(),
      get().fetchKokoroVoices(),
      get().fetchStoryStateSettings(),
    ]);
    await get().fetchBackgrounds();
  },

  selectSession: async (sessionId) => {
    promptPreviewRequestSequence += 1;
    set((state) => ({
      activeSessionId: sessionId,
      selectedSceneId: null,
      selectedVersionId: null,
      isLoadingScenes: true,
      isLoadingStoryDetails: true,
      prosePromptPreview: null,
      prosePromptPreviewError: "",
      sessionCharactersBySession: { ...state.sessionCharactersBySession, [sessionId]: [] },
      worldNotesBySession: { ...state.worldNotesBySession, [sessionId]: null },
      foundationsBySession: { ...state.foundationsBySession, [sessionId]: null },
      memoriesBySession: { ...state.memoriesBySession, [sessionId]: [] },
      qualityNotesBySession: { ...state.qualityNotesBySession, [sessionId]: [] },
      qualityHintsBySession: { ...state.qualityHintsBySession, [sessionId]: null },
      summariesBySession: { ...state.summariesBySession, [sessionId]: null },
      storyStateBySession: { ...state.storyStateBySession, [sessionId]: null },
    }));
    await Promise.all([
      get().fetchScenes(sessionId),
      get().fetchStoryContext(sessionId),
    ]);
    get().fetchQualityHints(sessionId).catch(() => {});
  },

  getSelectedScene: () => {
    const state = get();
    const scenes = state.scenesBySession[state.activeSessionId] || [];
    return scenes.find((scene) => scene.id === state.selectedSceneId) || scenes.at(-1) || null;
  },

  getSelectedSceneVersion: () => {
    const scene = get().getSelectedScene();
    if (!scene) return null;
    const state = get();
    return (
      scene.versions?.find((version) => version.id === state.selectedVersionId) ||
      scene.versions?.find((version) => version.version_index === state.selectedVersions[scene.id]) ||
      latestVersion(scene)
    );
  },

  getSelectedSceneText: () => get().getSelectedSceneVersion()?.generated_text || "",

  getSceneAndVersion: (sceneId, versionId = null, sessionId = null) => {
    const state = get();
    const scenes = state.scenesBySession[sessionId || state.activeSessionId] || [];
    const scene = scenes.find((item) => item.id === sceneId) || null;
    if (!scene) return { scene: null, version: null };
    const version =
      scene.versions?.find((item) => item.id === versionId) ||
      scene.versions?.find((item) => item.version_index === state.selectedVersions[scene.id]) ||
      latestVersion(scene);
    return { scene, version };
  },

  /* Archived image generation and preview actions. Kept only as rollback source.
  getSelectedWorkflow: () => {
    const state = get();
    const sessionWorkflowId =
      state.sessionImageSettingsBySession[state.activeSessionId]?.selected_workflow_id ||
      state.imageSettings.selected_workflow_id;
    return state.imageWorkflows.find((workflow) => workflow.id === sessionWorkflowId) || null;
  },

  getWorkflowForScene: (sceneId, versionId = null) => {
    const state = get();
    const sceneWorkflowId = state.sceneImageSettingsByKey[sceneImageSettingsKey(sceneId, versionId)]?.selected_workflow_id;
    if (sceneWorkflowId) {
      return state.imageWorkflows.find((workflow) => workflow.id === sceneWorkflowId) || null;
    }
    return state.getSelectedWorkflow();
  },

  getSceneImageSettingsForVersion: (sceneId, versionId = null) =>
    get().sceneImageSettingsByKey[sceneImageSettingsKey(sceneId, versionId)] || null,

  fetchSceneImages: async (sessionId, sceneId, versionId = null) => {
    if (!sessionId || !sceneId) return [];
    try {
      const images = await api.listSceneImages(sessionId, sceneId, versionId);
      set((state) => ({
        sceneImagesByKey: {
          ...state.sceneImagesByKey,
          [imageKey(sceneId, versionId)]: images,
        },
        lastError: "",
      }));
      return images;
    } catch (error) {
      set({ lastError: error.message });
      return [];
    }
  },

  getAcceptedImageForVersion: (sceneId, versionId = null) => {
    const images = get().sceneImagesByKey[imageKey(sceneId, versionId)] || [];
    return (
      images.find((image) => image.status === "accepted" && image.is_primary) ||
      images.find((image) => image.status === "accepted") ||
      null
    );
  },

  getImagesForVersion: (sceneId, versionId = null) =>
    get().sceneImagesByKey[imageKey(sceneId, versionId)] || [],

  pollImageJobStatus: async () => {
    try {
      const status = await api.getImageJobStatus();
      set((state) => ({
        imageGeneration: status.active
          ? {
              ...state.imageGeneration,
              isGenerating: true,
              sceneId: status.scene_id || state.imageGeneration.sceneId,
              versionId: status.version_id || state.imageGeneration.versionId,
              mode: status.resource_mode || state.imageGeneration.mode,
              statusMessage: status.message || state.imageGeneration.statusMessage,
              warnings: status.warnings || state.imageGeneration.warnings,
              actions: status.actions || state.imageGeneration.actions,
              promptId: status.prompt_id || state.imageGeneration.promptId,
              percent: status.percent,
              stage: status.stage || state.imageGeneration.stage,
              elapsedSeconds: status.elapsed_seconds ?? state.imageGeneration.elapsedSeconds,
              expectedTimeSecondsMin: status.expected_time_seconds_min ?? state.imageGeneration.expectedTimeSecondsMin,
              expectedTimeSecondsMax: status.expected_time_seconds_max ?? state.imageGeneration.expectedTimeSecondsMax,
              slowWarning: status.slow_warning || null,
              fastRegenerateWindow: status.fast_regenerate_window || state.imageGeneration.fastRegenerateWindow || {},
            }
          : {
              ...state.imageGeneration,
              isGenerating: false,
              statusMessage: status.message || "",
              warnings: status.warnings || state.imageGeneration.warnings,
              actions: status.actions || state.imageGeneration.actions,
              promptId: status.prompt_id || state.imageGeneration.promptId,
              percent: status.percent,
              stage: status.stage || "ready",
              elapsedSeconds: status.elapsed_seconds ?? state.imageGeneration.elapsedSeconds,
              expectedTimeSecondsMin: status.expected_time_seconds_min ?? state.imageGeneration.expectedTimeSecondsMin,
              expectedTimeSecondsMax: status.expected_time_seconds_max ?? state.imageGeneration.expectedTimeSecondsMax,
              slowWarning: status.slow_warning || null,
              fastRegenerateWindow: status.fast_regenerate_window || state.imageGeneration.fastRegenerateWindow || {},
            },
        lastImageResourceEvent: !status.active
          ? {
              mode: status.resource_mode || state.imageGeneration.mode || state.imageSettings.image_resource_mode,
              warnings: status.warnings || state.imageGeneration.warnings || [],
              actions: status.actions || state.imageGeneration.actions || {},
            }
          : state.lastImageResourceEvent,
      }));
      return status;
    } catch {
      return null;
    }
  },

  fetchSceneImagePrompt: async (sessionId, sceneId, versionId = null, workflowId = null) => {
    if (!sessionId || !sceneId) return null;
    const selectedWorkflowId = workflowId || get().getWorkflowForScene(sceneId, versionId)?.id || "";
    try {
      const prompt = await api.getSceneImagePrompt(sessionId, sceneId, versionId, selectedWorkflowId || null);
      set((state) => ({
        sceneImagePromptsByKey: {
          ...state.sceneImagePromptsByKey,
          [imagePromptKey(sceneId, versionId, selectedWorkflowId)]: prompt,
          ...(prompt
            ? { [imagePromptKey(sceneId, prompt.version_id, prompt.workflow_id)]: prompt }
            : {}),
        },
        lastError: "",
      }));
      return prompt;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  refreshSceneImagePrompt: async ({ sceneId = null, versionId = null, workflowId = null, negativePrompt = null } = {}) => {
    const state = get();
    const scene = sceneId ? state.getSceneAndVersion(sceneId, versionId).scene : state.getSelectedScene();
    const version = sceneId ? state.getSceneAndVersion(sceneId, versionId).version : state.getSelectedSceneVersion();
    const selectedWorkflowId = workflowId || state.getWorkflowForScene(scene?.id, version?.id)?.id || "";
    if (!scene || !version) {
      set({ lastError: "Select a scene before preparing an image prompt." });
      return null;
    }
    try {
      const prompt = await api.refreshSceneImagePrompt(scene.session_id, scene.id, {
        session_id: scene.session_id,
        scene_id: scene.id,
        version_id: version.id,
        workflow_id: selectedWorkflowId || null,
        negative_prompt: negativePrompt,
      });
      set((current) => ({
        sceneImagePromptsByKey: {
          ...current.sceneImagePromptsByKey,
          [imagePromptKey(scene.id, version.id, selectedWorkflowId)]: prompt,
          [imagePromptKey(scene.id, prompt.version_id, prompt.workflow_id)]: prompt,
        },
        lastError: "",
      }));
      return prompt;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },

  getImagePromptForVersion: (sceneId, versionId = null, workflowId = null) => {
    const selectedWorkflowId = workflowId || get().getWorkflowForScene(sceneId, versionId)?.id || "";
    return get().sceneImagePromptsByKey[imagePromptKey(sceneId, versionId, selectedWorkflowId)] || null;
  },

  openImagePreview: (image = null) => {
    set((state) => ({
      imagePreview: {
        isOpen: true,
        image: image || state.imagePreview.image,
      },
    }));
  },

  closeImagePreview: async ({ discardPending = true } = {}) => {
    const image = get().imagePreview.image;
    if (discardPending && image?.id && image.status === "pending") {
      try {
        const rejected = await api.discardImage(image.id);
        set((state) => ({
          sceneImagesByKey: {
            ...state.sceneImagesByKey,
            [imageKey(rejected.scene_id, rejected.version_id)]: [
              rejected,
              ...(state.sceneImagesByKey[imageKey(rejected.scene_id, rejected.version_id)] || []).filter(
                (existing) => existing.id !== rejected.id,
              ),
            ],
          },
        }));
      } catch (error) {
        set({ lastError: error.message });
      }
    }
    set({ imagePreview: { isOpen: false, image: null } });
  },

  acceptPreviewImage: async () => {
    const image = get().imagePreview.image;
    if (!image?.id) return null;
    try {
      const accepted = await api.acceptImage(image.id);
      const refreshed = await api.listSceneImages(accepted.session_id, accepted.scene_id, accepted.version_id);
      set((state) => ({
        imagePreview: { isOpen: false, image: null },
        sceneImagesByKey: {
          ...state.sceneImagesByKey,
          [imageKey(accepted.scene_id, accepted.version_id)]: refreshed,
        },
        lastError: "",
      }));
      return accepted;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  rejectPreviewImage: async () => {
    const image = get().imagePreview.image;
    if (!image?.id) {
      set({ imagePreview: { isOpen: false, image: null } });
      return null;
    }
    try {
      const rejected = await api.discardImage(image.id);
      set((state) => ({
        imagePreview: { isOpen: false, image: null },
        sceneImagesByKey: {
          ...state.sceneImagesByKey,
          [imageKey(rejected.scene_id, rejected.version_id)]: [
            rejected,
            ...(state.sceneImagesByKey[imageKey(rejected.scene_id, rejected.version_id)] || []).filter(
              (existing) => existing.id !== rejected.id,
            ),
          ],
        },
        lastError: "",
      }));
      return rejected;
    } catch (error) {
      set({ lastError: error.message });
      throw error;
    }
  },

  generateSelectedImage: async ({
    forceNewSeed = false,
    promptOverride = null,
    negativePrompt = null,
    workflowId = null,
    seed = undefined,
    targetSceneId = null,
    targetVersionId = null,
  } = {}) => {
    let state = get();
    const target = targetSceneId ? state.getSceneAndVersion(targetSceneId, targetVersionId) : {};
    if (targetSceneId && (!target.scene || !target.version)) {
      set({ lastError: "The image preview target is no longer loaded. Select the scene and try again." });
      return { ok: false, needsSettings: false };
    }
    const scene = target.scene || state.getSelectedScene();
    const version = target.version || state.getSelectedSceneVersion();
    if (!scene || !version) {
      set({ lastError: "Select a scene before generating an image." });
      return { ok: false, needsSettings: false };
    }
    if (imageGenerationPaused(state)) {
      const pausedMessage = "Image generation paused. Existing images remain available.";
      set({
        lastError: "",
        imageGeneration: {
          ...state.imageGeneration,
          isGenerating: false,
          sceneId: null,
          versionId: null,
          mode: "paused",
          statusMessage: pausedMessage,
          warnings: [],
          actions: { image_generation_mode: "paused", paused: true, message: pausedMessage },
          promptId: null,
          percent: null,
          stage: "paused",
          elapsedSeconds: null,
          expectedTimeSecondsMin: null,
          expectedTimeSecondsMax: null,
          slowWarning: null,
        },
        lastImageResourceEvent: {
          mode: "paused",
          warnings: [],
          actions: { image_generation_mode: "paused", paused: true, message: pausedMessage },
        },
      });
      return { ok: false, needsSettings: false, paused: true };
    }
    const sessionImageSettings = sessionImageSettingsFor(state, scene.session_id);
    const requestedResourceMode =
      effectiveStoryImageSetting(state, scene.session_id, "image_resource_mode", "image_priority") || "image_priority";
    if (state.isGenerating) {
      set({ lastError: "Wait for the current scene to finish before generating an image." });
      return { ok: false, needsSettings: false };
    }
    if (state.imageGeneration.isGenerating) {
      set({ lastError: "An image generation is already running." });
      return { ok: false, needsSettings: false };
    }
    if (!workflowId && !state.sceneImageSettingsByKey[sceneImageSettingsKey(scene.id, version.id)]) {
      await get().fetchSceneImageSettings(scene.session_id, scene.id, version.id);
      state = get();
    }
    const workflow =
      state.imageWorkflows.find((item) => item.id === workflowId) ||
      state.getWorkflowForScene(scene.id, version.id);
    if (!workflow || workflow.status !== "ready") {
      set({ lastError: workflow ? "Selected image workflow is not ready. Open Image Settings and validate it." : "Select an image workflow before generating." });
      return { ok: false, needsSettings: true };
    }
    const resourceMode = effectiveImageResourceMode(requestedResourceMode, workflow);

    const previousPreviewImage = state.imagePreview.image;
    if (previousPreviewImage?.id && previousPreviewImage.status === "pending") {
      api.rejectImage(previousPreviewImage.id).catch(() => {});
    }

    if (!promptOverride) {
      get().fetchSceneImagePrompt(scene.session_id, scene.id, version.id, workflow.id).catch(() => {});
    }

    set({
      lastError: "",
      imageGeneration: {
        isGenerating: true,
        sceneId: scene.id,
        versionId: version.id,
        mode: resourceMode,
        statusMessage: `Generating image with ${imageModeLabel(resourceMode)} mode`,
        warnings:
          resourceMode === "manual"
            ? [manualResourceGuidance]
            : resourceMode !== requestedResourceMode
              ? ["Z-Image Turbo uses Auto Image Priority by default on this PC."]
              : [],
        actions: {},
        promptId: null,
        percent: 0,
        stage: "preparing_prompt",
        elapsedSeconds: 0,
        expectedTimeSecondsMin: workflow.config?.expected_time_seconds_min ?? workflow.metadata?.expected_time_seconds_min ?? null,
        expectedTimeSecondsMax: workflow.config?.expected_time_seconds_max ?? workflow.metadata?.expected_time_seconds_max ?? null,
        slowWarning: null,
      },
      imagePreview: { isOpen: sessionImageSettings.auto_open_preview !== false, image: null },
    });
    let poll = null;
    try {
      poll = window.setInterval(() => {
        get().pollImageJobStatus();
      }, 1200);
      const image = await api.generateImage({
        session_id: scene.session_id,
        scene_id: scene.id,
        version_id: version.id,
        workflow_id: workflowId || state.sceneImageSettingsByKey[sceneImageSettingsKey(scene.id, version.id)]?.selected_workflow_id || undefined,
        prompt_override: promptOverride || undefined,
        negative_prompt: negativePrompt ?? sessionImageSettings.default_negative_prompt ?? undefined,
        seed: seed !== undefined ? seed : forceNewSeed ? null : undefined,
        open_preview: true,
      });
      window.clearInterval(poll);
      const resourceEvent = {
        mode: image.resource_mode || resourceMode,
        warnings: image.resource_warnings || [],
        actions: image.resource_actions || {},
      };
      set((current) => ({
        imageGeneration: {
          isGenerating: false,
          sceneId: null,
          versionId: null,
          mode: null,
          statusMessage: "",
          warnings: resourceEvent.warnings,
          actions: resourceEvent.actions,
          promptId: null,
          percent: 100,
          stage: "completed",
          elapsedSeconds: resourceEvent.actions?.performance_total_seconds ?? null,
          expectedTimeSecondsMin: resourceEvent.actions?.workflow_expected_time_seconds_min ?? null,
          expectedTimeSecondsMax: resourceEvent.actions?.workflow_expected_time_seconds_max ?? null,
          slowWarning: (image.resource_warnings || []).find((warning) => warning.includes("Generation took")) || null,
          fastRegenerateWindow: resourceEvent.actions?.fast_regenerate_window || {},
        },
        lastImageResourceEvent: resourceEvent,
        imagePreview: { isOpen: sessionImageSettings.auto_open_preview !== false, image },
        sceneImagesByKey: {
          ...current.sceneImagesByKey,
          [imageKey(scene.id, version.id)]: [
            image,
            ...(current.sceneImagesByKey[imageKey(scene.id, version.id)] || []).filter(
              (existing) => existing.id !== image.id,
            ),
          ],
        },
        lastError: "",
      }));
      get().fetchResourceStatus();
      if (resourceEvent.actions?.fast_regenerate_window_active) {
        const delaySeconds = Number(
          resourceEvent.actions?.fast_regenerate_window_seconds ||
            resourceEvent.actions?.regenerate_warm_window_seconds ||
            get().imageSettings.regenerate_warm_window_seconds ||
            90,
        );
        window.setTimeout(() => {
          get().pollImageJobStatus();
          get().fetchResourceStatus();
        }, Math.max(2, delaySeconds + 2) * 1000);
      }
      get().fetchSceneImagePrompt(scene.session_id, scene.id, version.id, workflow.id).catch(() => {});
      return { ok: true, image };
    } catch (error) {
      if (poll) {
        window.clearInterval(poll);
      }
      const finalJobStatus = await get().pollImageJobStatus();
      const resourceEvent = {
        mode: finalJobStatus?.resource_mode || resourceMode,
        warnings: finalJobStatus?.warnings || [],
        actions: finalJobStatus?.actions || {},
      };
      set({
        imageGeneration: {
          isGenerating: false,
          sceneId: null,
          versionId: null,
          mode: null,
          statusMessage: finalJobStatus?.message || "",
          warnings: resourceEvent.warnings,
          actions: resourceEvent.actions,
          promptId: finalJobStatus?.prompt_id || null,
          percent: finalJobStatus?.percent ?? null,
          stage: "failed",
          elapsedSeconds: finalJobStatus?.elapsed_seconds ?? null,
          expectedTimeSecondsMin: finalJobStatus?.expected_time_seconds_min ?? null,
          expectedTimeSecondsMax: finalJobStatus?.expected_time_seconds_max ?? null,
          slowWarning: finalJobStatus?.slow_warning || null,
          fastRegenerateWindow: finalJobStatus?.fast_regenerate_window || {},
        },
        lastImageResourceEvent: resourceEvent,
        imagePreview: { isOpen: false, image: null },
        lastError: error.message,
      });
      get().fetchResourceStatus();
      if (scene?.id && version?.id) {
        get().fetchSceneImages(scene.session_id, scene.id, version.id);
      }
      return { ok: false, needsSettings: false };
    }
  },

  regenerateImagePrompt: async ({ negativePrompt = null, targetSceneId = null, targetVersionId = null, workflowId = null } = {}) => {
    let state = get();
    const target = targetSceneId ? state.getSceneAndVersion(targetSceneId, targetVersionId) : {};
    const scene = target.scene || state.getSelectedScene();
    const version = target.version || state.getSelectedSceneVersion();
    const workflow = workflowId
      ? state.imageWorkflows.find((item) => item.id === workflowId)
      : state.getWorkflowForScene(scene?.id, version?.id);
    if (!scene || !version) {
      set({ lastError: "Select a scene before regenerating an image prompt." });
      return null;
    }
    if (!workflowId && !state.sceneImageSettingsByKey[sceneImageSettingsKey(scene.id, version.id)]) {
      await get().fetchSceneImageSettings(scene.session_id, scene.id, version.id);
      state = get();
    }
    const resolvedWorkflow = workflowId
      ? workflow
      : state.getWorkflowForScene(scene.id, version.id);
    try {
      const response = await get().refreshSceneImagePrompt({
        sceneId: scene.id,
        versionId: version.id,
        workflowId: workflowId || state.sceneImageSettingsByKey[sceneImageSettingsKey(scene.id, version.id)]?.selected_workflow_id || resolvedWorkflow?.id || null,
        negativePrompt,
      });
      return response;
    } catch (error) {
      set({ lastError: error.message });
      return null;
    }
  },
  */

  loadBrowserVoices: () => {
    const voices = ttsController.browserVoices().map((voice) => ({
      name: voice.name,
      voiceURI: voice.voiceURI,
      lang: voice.lang,
    }));
    set({ browserVoices: voices });
    return voices;
  },

  openNarration: async () => {
    const state = get();
    if (state.isGenerating) return;
    if (
      state.narration.isNarrating &&
      (state.narration.isPaused || state.narration.needsUserResume) &&
      state.narration.currentNarrationSessionId === state.activeSessionId &&
      state.narration.currentNarrationSceneId === state.selectedSceneId &&
      state.narration.currentNarrationVersionId === state.selectedVersionId
    ) {
      await get().resumeNarration();
      return;
    }

    const selectedScene = state.getSelectedScene();
    const selectedVersion = state.getSelectedSceneVersion();
    await get().startNarrationForVersion(selectedScene, selectedVersion);
  },

  startNarrationForVersion: async (selectedScene, selectedVersion) => {
    const state = get();
    if (state.isGenerating) return;
    const text = selectedVersion?.generated_text || "";
    if (!selectedScene || !selectedVersion || !text.trim()) {
      set({ lastError: "Select a scene to narrate." });
      return;
    }
    if (
      state.narration.currentNarrationSessionId !== selectedScene.session_id ||
      state.narration.currentNarrationSceneId !== selectedScene.id ||
      state.narration.currentNarrationVersionId !== selectedVersion.id
    ) {
      ttsController.stop({ reason: "switch_narration_target", preserveCursor: true });
    }

    const requestedProvider = state.ttsSettings.tts_provider;
    const provider = requestedProvider;
    const speed = Number(state.ttsSettings.tts_speed) || 1;
    const activeProfile = (state.ttsSettings.tts_voice_profiles || []).find(
      (profile) => profile.id === state.ttsSettings.tts_voice_profile_id,
    );
    const voice = state.ttsSettings.tts_voice || activeProfile?.voice_id || null;
    const resumeCursor = provider === "kokoro" || provider === "high_quality_local"
      ? ttsController.getSavedCursor({
          text,
          provider,
          speed,
          voice: voice || "default",
          voiceProfileId: state.ttsSettings.tts_voice_profile_id,
          breathingMode: state.ttsSettings.breathing_mode || "natural",
          sessionId: selectedScene.session_id,
          sceneId: selectedScene.id,
          versionId: selectedVersion.id,
        })
      : null;
    const kokoroOptions = {
      temperature: state.ttsSettings.kokoro_temperature,
      top_p: state.ttsSettings.kokoro_top_p,
      exaggeration: state.ttsSettings.kokoro_exaggeration,
      style: state.ttsSettings.kokoro_style,
      cfg: state.ttsSettings.kokoro_cfg,
    };
    if (ttsPreviewAudio) {
      ttsPreviewAudio.pause();
      ttsPreviewAudio = null;
      set((current) => ({
        ttsVoicePreview: { ...current.ttsVoicePreview, isPlaying: false, isLoading: false },
      }));
    }

    ttsController.configure({
      onUpdate: (patch) =>
        set((current) => ({
          narration: {
            ...current.narration,
            ...patch,
            isOpen:
              patch.isNarrating === false && patch.elapsedTime === 0
                ? current.narration.isOpen
                : true,
          },
        })),
      onError: (message) => set({ lastError: message }),
    });

    set((current) => ({
      lastError: "",
      narration: {
        ...current.narration,
        isOpen: true,
        isNarrating: true,
        isPaused: false,
        currentNarrationSceneId: selectedScene.id,
        currentNarrationVersionId: selectedVersion.id,
        currentNarrationSessionId: selectedScene.session_id,
        elapsedTime: resumeCursor?.globalNarrationTime || 0,
        duration: null,
        narrationCursor: resumeCursor?.cursor || null,
        needsUserResume: false,
        isBuffering: false,
        playbackBlocked: false,
        playbackBlockReason: null,
        statusMessage: provider === "kokoro" || provider === "high_quality_local"
          ? resumeCursor
            ? "Resuming narration..."
            : provider === "high_quality_local" ? "Preparing Qwen3-TTS 0.6B" : "Preparing Kokoro"
          : "Narrating...",
        speed,
        provider,
        voice,
        voiceDisplayName: activeProfile?.display_name || voice,
        lastProviderUsed: null,
        lastVoiceUsed: null,
        lastModelUsed: null,
      },
    }));

    try {
      await ttsController.play({
        text,
        provider,
        speed,
        voice,
        kokoroOptions,
        chunkMode: state.ttsSettings.chunked_narration_mode,
        chunkSize: state.ttsSettings.tts_chunk_size,
        prebufferChunks: state.ttsSettings.tts_prebuffer_chunks,
        followMode: state.ttsSettings.tts_follow_mode || state.ttsSettings.tts_follow_highlight || "phrase",
        voiceProfileId: state.ttsSettings.tts_voice_profile_id,
        chunkingProfile: state.ttsSettings.tts_chunking_profile || "natural",
        narrationPacing: state.ttsSettings.narration_pacing || "natural",
        dialoguePauseStrength: state.ttsSettings.dialogue_pause_strength || "medium",
        paragraphPauseStrength: state.ttsSettings.paragraph_pause_strength || "medium",
        dialogueNarrationStyle: state.ttsSettings.dialogue_narration_style || "neutral",
        breathingMode: state.ttsSettings.breathing_mode || "natural",
        pronunciationDictionaryVersion: state.ttsSettings.pronunciation_dictionary_version || null,
        normalizationVersion: state.ttsSettings.normalization_version || "tts-realism-v1",
        sessionId: selectedScene.session_id,
        sceneId: selectedScene.id,
        versionId: selectedVersion.id,
        resumeCursor,
      });
      if (provider === "kokoro" || provider === "high_quality_local") {
        get().fetchTTSStatus();
      }
    } catch (error) {
      const canFallback = provider === "kokoro" && state.ttsSettings.allow_browser_fallback;
      if (canFallback) {
        const fallbackMessage =
          "Kokoro-FastAPI is not running. Browser fallback sounds robotic. Configure Kokoro in D:\\StoryDriver\\.env or start it manually. Browser fallback is active.";
        set((current) => ({
          lastError: fallbackMessage,
          narration: {
            ...current.narration,
            isOpen: true,
            isNarrating: true,
            isPaused: false,
            provider: "browser",
            voice,
          },
        }));
        try {
          await ttsController.play({ text, provider: "browser", speed, voice });
          return;
        } catch (fallbackError) {
          set((current) => ({
            lastError: fallbackError.message,
            narration: { ...current.narration, isNarrating: false, isPaused: false },
          }));
          return;
        }
      }
      set((current) => ({
        lastError:
          provider === "kokoro"
            ? "Kokoro narration paused. StoryDriver saved your place; tap Play or Narrate again to resume when Kokoro is reachable."
            : error.message,
        narration: {
          ...current.narration,
          isOpen: true,
          isNarrating: false,
          isPaused: true,
          needsUserResume: true,
          isBuffering: false,
          playbackBlocked: true,
          playbackBlockReason: provider === "kokoro" ? "kokoro_unreachable" : "tts_error",
          statusMessage: provider === "kokoro" ? "Tap play to resume when Kokoro is reachable" : "Narration error",
        },
      }));
      if (provider === "kokoro") {
        get().fetchTTSStatus();
      }
    }
  },

  narrateSceneVersion: async (sceneId, versionId = null, sessionId = null) => {
    const state = get();
    if (state.isGenerating) return;
    const targetSessionId = sessionId || state.activeSessionId;
    const { scene, version } = state.getSceneAndVersion(sceneId, versionId, targetSessionId);
    if (!scene || !version) {
      set({ lastError: "Select a scene to narrate." });
      return;
    }
    ttsController.stop({ reason: "switch_scene", preserveCursor: true });
    if (targetSessionId === state.activeSessionId) {
      set((current) => ({
        selectedSceneId: scene.id,
        selectedVersionId: version.id,
        selectedVersions: { ...current.selectedVersions, [scene.id]: version.version_index || scene.active_version_index || 1 },
      }));
      writeStoredSelection(targetSessionId, scene.id, version.id);
    }
    await get().startNarrationForVersion(scene, version);
  },

  pauseNarration: () => {
    ttsController.pause();
    set((state) => ({
      narration: {
        ...state.narration,
        isPaused: true,
        isNarrating: true,
        isOpen: true,
        needsUserResume: false,
        isBuffering: false,
        playbackBlocked: false,
        playbackBlockReason: null,
        statusMessage: "Paused",
      },
    }));
  },

  resumeNarration: async () => {
    const resumed = await ttsController.resume();
    if (resumed !== false) {
      set((state) => ({
        narration: {
          ...state.narration,
          isPaused: false,
          isNarrating: true,
          isOpen: true,
          needsUserResume: false,
          isBuffering: false,
          playbackBlocked: false,
          playbackBlockReason: null,
          statusMessage: "Narrating",
        },
      }));
    }
    return resumed;
  },

  playNarration: async () => {
    const state = get();
    if (state.narration.isNarrating && (state.narration.isPaused || state.narration.needsUserResume)) {
      await get().resumeNarration();
      return;
    }
    if (state.narration.isNarrating) return;
    const sceneId = state.narration.currentNarrationSceneId || state.selectedSceneId;
    const versionId = state.narration.currentNarrationVersionId || state.selectedVersionId;
    const sessionId = state.narration.currentNarrationSessionId || state.activeSessionId;
    if (sceneId) {
      await get().narrateSceneVersion(sceneId, versionId, sessionId);
      return;
    }
    await get().openNarration();
  },

  previewTTSVoice: async ({ profileId = null, voice = null, speed = null } = {}) => {
    const state = get();
    if (state.narration.isNarrating || state.narration.isPaused) {
      set((current) => ({
        ttsVoicePreview: {
          ...current.ttsVoicePreview,
          isLoading: false,
          isPlaying: false,
          error: "Pause or stop scene narration before previewing a voice.",
        },
      }));
      return null;
    }
    if (ttsPreviewAudio) {
      ttsPreviewAudio.pause();
      ttsPreviewAudio = null;
    }
    const selectedProfileId = profileId || state.ttsSettings.tts_voice_profile_id || "natural_female_narrator";
    const activeProfile = (state.ttsSettings.tts_voice_profiles || []).find((item) => item.id === selectedProfileId);
    const selectedVoice = voice || state.ttsSettings.tts_voice || activeProfile?.voice_id || null;
    set((current) => ({
      ttsVoicePreview: {
        ...current.ttsVoicePreview,
        isLoading: true,
        isPlaying: false,
        error: "",
        profileId: selectedProfileId,
        voice: selectedVoice,
      },
    }));
    try {
      const response = await api.previewTTS({
        sample_text: TTS_PREVIEW_SAMPLE_TEXT,
        voice_profile_id: selectedProfileId,
        voice: selectedVoice,
        speed: speed || Number(state.ttsSettings.tts_speed) || null,
      });
      const audioUrl = absoluteAudioUrl(response.audio_url);
      ttsPreviewAudio = new Audio(audioUrl);
      ttsPreviewAudio.onended = () => {
        set((current) => ({
          ttsVoicePreview: { ...current.ttsVoicePreview, isPlaying: false, isLoading: false },
        }));
      };
      ttsPreviewAudio.onerror = () => {
        set((current) => ({
          ttsVoicePreview: {
            ...current.ttsVoicePreview,
            isPlaying: false,
            isLoading: false,
            error: "Preview audio could not be played.",
          },
        }));
      };
      await ttsPreviewAudio.play();
      set({
        ttsVoicePreview: {
          isLoading: false,
          isPlaying: true,
          audioUrl,
          voice: response.voice,
          profileId: response.profile?.id || selectedProfileId,
          error: "",
        },
      });
      get().fetchTTSStatus();
      return response;
    } catch (error) {
      set((current) => ({
        ttsVoicePreview: {
          ...current.ttsVoicePreview,
          isLoading: false,
          isPlaying: false,
          error: error.message,
        },
      }));
      return null;
    }
  },

  testNarration: async (providerOverride = null) => {
    const state = get();
    const requestedProvider = providerOverride || state.ttsSettings.tts_provider;
    const provider = requestedProvider;
    const activeProfile = (state.ttsSettings.tts_voice_profiles || []).find(
      (profile) => profile.id === state.ttsSettings.tts_voice_profile_id,
    );
    const voice = state.ttsSettings.tts_voice || activeProfile?.voice_id || null;
    ttsController.configure({
      onUpdate: (patch) =>
        set((current) => ({ narration: { ...current.narration, ...patch, isOpen: true } })),
      onError: (message) => set({ lastError: message }),
    });
    set((current) => ({
      lastError: "",
      narration: {
        ...current.narration,
        isOpen: true,
        isNarrating: true,
        isPaused: false,
        currentNarrationSceneId: null,
        currentNarrationVersionId: null,
        currentNarrationSessionId: null,
        elapsedTime: 0,
        duration: null,
        speed: state.ttsSettings.tts_speed,
        provider,
        voice,
      },
    }));
    try {
      await ttsController.play({
        text: "StoryDriver narration is ready.",
        provider,
        speed: Number(state.ttsSettings.tts_speed) || 1,
        voice,
        chunkMode: state.ttsSettings.chunked_narration_mode,
        chunkSize: state.ttsSettings.tts_chunk_size,
        prebufferChunks: state.ttsSettings.tts_prebuffer_chunks,
        followMode: state.ttsSettings.tts_follow_mode || state.ttsSettings.tts_follow_highlight || "phrase",
        voiceProfileId: state.ttsSettings.tts_voice_profile_id,
        chunkingProfile: state.ttsSettings.tts_chunking_profile || "natural",
        narrationPacing: state.ttsSettings.narration_pacing || "natural",
        dialoguePauseStrength: state.ttsSettings.dialogue_pause_strength || "medium",
        paragraphPauseStrength: state.ttsSettings.paragraph_pause_strength || "medium",
        dialogueNarrationStyle: state.ttsSettings.dialogue_narration_style || "neutral",
        breathingMode: state.ttsSettings.breathing_mode || "natural",
        pronunciationDictionaryVersion: state.ttsSettings.pronunciation_dictionary_version || null,
        normalizationVersion: state.ttsSettings.normalization_version || "tts-realism-v1",
      });
      if (provider === "kokoro" || provider === "high_quality_local") {
        get().fetchTTSStatus();
      }
    } catch (error) {
      set((current) => ({
        lastError:
          provider === "kokoro"
            ? "Kokoro-FastAPI is not running. Browser fallback sounds robotic. Configure Kokoro in D:\\StoryDriver\\.env or start it manually."
            : error.message,
        narration: { ...current.narration, isOpen: false, isNarrating: false, isPaused: false },
      }));
      if (provider === "kokoro") {
        get().fetchTTSStatus();
      }
    }
  },

  closeNarration: () => {
    ttsController.stop();
    set((state) => ({
      narration: {
        ...state.narration,
        isOpen: false,
        isNarrating: false,
        isPaused: false,
        currentNarrationSceneId: null,
        currentNarrationVersionId: null,
        currentNarrationSessionId: null,
        elapsedTime: 0,
        duration: null,
        narrationCursor: null,
        needsUserResume: false,
        isBuffering: false,
        playbackBlocked: false,
        playbackBlockReason: null,
      },
    }));
  },

  stopNarration: () => {
    ttsController.stop();
    set((state) => ({
      narration: {
        ...state.narration,
        isOpen: false,
        isNarrating: false,
        isPaused: false,
        elapsedTime: 0,
        duration: null,
        narrationCursor: null,
        needsUserResume: false,
        isBuffering: false,
        playbackBlocked: false,
        playbackBlockReason: null,
        statusMessage: "Ready",
      },
    }));
  },

  skipNarration: (seconds) => {
    ttsController.skip(seconds);
  },

  seekNarration: (seconds) => {
    ttsController.seek(seconds);
  },

  setNarrationSpeed: (speed) => {
    const numericSpeed = Number(speed) || 1;
    get().saveTTSSettings({ tts_speed: numericSpeed });
  },

  setNarrationVoice: (voice) => {
    get().saveTTSSettings({ tts_voice: voice || null });
  },
}));
