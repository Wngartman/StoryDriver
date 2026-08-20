const apiPort = import.meta.env.VITE_API_PORT || "8001";

function isLoopbackHost(hostname) {
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(String(hostname || "").toLowerCase());
}

function normalizeBaseUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}

function resolveApiBaseUrl() {
  const pageHost = window.location.hostname || "localhost";
  const pageProtocol = window.location.protocol === "https:" ? "https:" : "http:";
  const configured = import.meta.env.VITE_API_BASE_URL;

  if (configured) {
    try {
      const url = new URL(configured);
      if (!isLoopbackHost(pageHost) && isLoopbackHost(url.hostname)) {
        url.hostname = pageHost;
        url.port = url.port || apiPort;
        return normalizeBaseUrl(url.toString());
      }
      return normalizeBaseUrl(url.toString());
    } catch {
      // Fall through to same-host LAN-safe discovery.
    }
  }

  if (window.location.port && window.location.port !== "5173") {
    return "";
  }

  const apiHost = isLoopbackHost(pageHost) ? "localhost" : pageHost;
  return `${pageProtocol}//${apiHost}:${apiPort}`;
}

export const API_BASE_URL = resolveApiBaseUrl();

if (typeof window !== "undefined") {
  window.__STORYDRIVER_API_BASE_URL = API_BASE_URL;
}

function backendOfflineMessage(error) {
  const detail = error?.message && error.message !== "Failed to fetch" ? ` ${error.message}` : "";
  const endpoint = API_BASE_URL || "this StoryDriver instance";
  return `StoryDriver loaded, but the local backend is unreachable at ${endpoint}. Try ${(API_BASE_URL || "")}/health from this device. Check Windows Firewall/private network access, then refresh.${detail}`;
}

function errorMessageFromBody(status, body) {
  if (!body) return `Request failed: ${status}`;
  try {
    const parsed = JSON.parse(body);
    if (parsed?.detail) {
      return typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    }
  } catch {
    // Fall through to the raw response body.
  }
  return body || `Request failed: ${status}`;
}

function parseJsonResponse(text, fallbackMessage = "StoryDriver returned an invalid response.") {
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(fallbackMessage);
  }
}

async function request(path, options = {}) {
  const { expectEmpty, ...fetchOptions } = options;
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(fetchOptions.headers || {}),
      },
      ...fetchOptions,
    });
  } catch (error) {
    throw new Error(backendOfflineMessage(error));
  }

  if (!response.ok) {
    const message = await response.text();
    throw new Error(errorMessageFromBody(response.status, message));
  }

  if (expectEmpty || response.status === 204) {
    return null;
  }

  const text = await response.text();
  if (!text.trim()) {
    throw new Error("StoryDriver returned an empty response.");
  }
  return parseJsonResponse(text);
}

export const api = {
  health: () => request("/health"),
  diagnostics: () => request("/diagnostics"),
  serviceStatus: () => request("/system/services"),
  startService: (serviceName) => request(`/system/services/${serviceName}/start`, { method: "POST" }),
  stopService: (serviceName) => request(`/system/services/${serviceName}/stop`, { method: "POST" }),
  restartService: (serviceName) => request(`/system/services/${serviceName}/restart`, { method: "POST" }),
  listModelProviders: () => request("/models/providers"),
  getModelLibrary: () => request("/models/library"),
  addModelGguf: (path) => request("/models/library/gguf", { method: "POST", body: JSON.stringify({ path }) }),
  scanModelFolder: (directory) => request("/models/library/scan", { method: "POST", body: JSON.stringify({ directory }) }),
  removeModelLibraryEntry: (id) => request("/models/library/remove", { method: "POST", body: JSON.stringify({ id }) }),
  diagnoseModelProvider: (payload) => request("/models/provider/diagnostics", { method: "POST", body: JSON.stringify(payload) }),
  loadProviderModel: (payload) => request("/models/provider/load", { method: "POST", body: JSON.stringify(payload) }),
  unloadProviderModel: (payload) => request("/models/provider/unload", { method: "POST", body: JSON.stringify(payload) }),
  testProviderModel: (payload) => request("/models/provider/test", { method: "POST", body: JSON.stringify(payload) }),
  listSessions: () => request("/sessions"),
  getSession: (sessionId) => request(`/sessions/${sessionId}`),
  createSession: (title) =>
    request("/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  updateSession: (sessionId, title) =>
    request(`/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  autoTitleSession: (sessionId, payload = {}) =>
    request(`/sessions/${sessionId}/auto-title`, {
      method: "POST",
      body: JSON.stringify(typeof payload === "string" ? { scene_text: payload } : payload),
    }),
  deleteSession: (sessionId, { permanent = false } = {}) =>
    request(`/sessions/${sessionId}${permanent ? "?permanent=true" : ""}`, {
      method: "DELETE",
    }),
  listDeleteJobs: ({ includeCompleted = true, limit = 20 } = {}) =>
    request(`/sessions/delete-jobs?${new URLSearchParams({
      include_completed: includeCompleted ? "true" : "false",
      limit: String(limit),
    }).toString()}`),
  getDeleteJob: (jobId) => request(`/sessions/delete-jobs/${jobId}`),
  resumeDeleteJob: (jobId) =>
    request(`/sessions/delete-jobs/${encodeURIComponent(jobId)}/resume`, {
      method: "POST",
    }),
  storageMaintenance: () => request("/storage/maintenance"),
  archiveSession: (sessionId) =>
    request(`/sessions/${sessionId}/archive`, {
      method: "POST",
    }),
  restoreSession: (sessionId) =>
    request(`/sessions/${sessionId}/restore`, {
      method: "POST",
    }),
  listScenes: (sessionId) => request(`/sessions/${sessionId}/scenes`),
  generateScene: (sessionId, payload) =>
    request(`/sessions/${sessionId}/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getProsePromptPreview: (sessionId, payload) =>
    request(`/sessions/${sessionId}/prose-prompt-preview`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  generateSceneStream: async (sessionId, payload, handlers = {}) => {
    let response;
    try {
      response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/generate-stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      throw new Error(backendOfflineMessage(error));
    }
    if (!response.ok || !response.body) {
      const message = await response.text();
      throw new Error(errorMessageFromBody(response.status, message));
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalScene = null;
    const handleEvent = (event) => {
      if (event.type === "delta") {
        handlers.onDelta?.(event.text);
      } else if (event.type === "replace") {
        handlers.onReplace?.(event.text || "");
      } else if (event.type === "status") {
        handlers.onStatus?.(event);
      } else if (event.type === "scene") {
        finalScene = event.scene;
        handlers.onScene?.(event.scene);
      } else if (event.type === "error") {
        throw new Error(event.detail || "Generation failed");
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        handleEvent(parseJsonResponse(line, "StoryDriver stream returned a malformed event."));
      }
    }
    if (buffer.trim()) {
      handleEvent(parseJsonResponse(buffer, "StoryDriver stream ended with a malformed event."));
    }

    if (!finalScene) {
      throw new Error("Scene generation ended before StoryDriver received a saved scene.");
    }
    return finalScene;
  },
  listModels: () => request("/models"),
  listModelProviders: () => request("/models/providers"),
  diagnoseModelProvider: (payload) =>
    request("/models/provider/diagnostics", { method: "POST", body: JSON.stringify(payload) }),
  loadModelProvider: (payload) =>
    request("/models/provider/load", { method: "POST", body: JSON.stringify(payload) }),
  unloadModelProvider: (payload) =>
    request("/models/provider/unload", { method: "POST", body: JSON.stringify(payload) }),
  testModelProvider: (payload) =>
    request("/models/provider/test", { method: "POST", body: JSON.stringify(payload) }),
  getModelLibrary: () => request("/models/library"),
  addModelLibraryGguf: (path) =>
    request("/models/library/gguf", { method: "POST", body: JSON.stringify({ path }) }),
  scanModelLibrary: (directory) =>
    request("/models/library/scan", { method: "POST", body: JSON.stringify({ directory }) }),
  removeModelLibraryEntry: (id) =>
    request("/models/library/remove", { method: "POST", body: JSON.stringify({ id }) }),
  getModelSettings: () => request("/settings/model"),
  saveModelSettings: (settings) =>
    request("/settings/model", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getTaskModelProfiles: () => request("/settings/task-model-profiles"),
  saveTaskModelProfile: (taskType, profile) =>
    request(`/settings/task-model-profiles/${encodeURIComponent(taskType)}`, {
      method: "PUT",
      body: JSON.stringify(profile),
    }),
  resetTaskModelProfile: (taskType) =>
    request(`/settings/task-model-profiles/${encodeURIComponent(taskType)}`, {
      method: "DELETE",
    }),
  getTTSSettings: () => request("/settings/tts"),
  saveTTSSettings: (settings) =>
    request("/settings/tts", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  /* Archived image settings API. Not shipped in the normal product path.
  getImageSettings: () => request("/settings/image"),
  saveImageSettings: (settings) =>
    request("/settings/image", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  */
  getStoryStateSettings: () => request("/settings/story-state"),
  saveStoryStateSettings: (settings) =>
    request("/settings/story-state", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getUiSettings: () => request("/settings/ui"),
  saveUiSettings: (settings) =>
    request("/settings/ui", {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  listBackgrounds: () => request("/backgrounds"),
  refreshBackgrounds: () => request("/backgrounds/refresh", { method: "POST" }),
  uploadBackground: (file) =>
    request("/backgrounds/upload", {
      method: "POST",
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        "X-StoryDriver-Filename": encodeURIComponent(file.name),
      },
      body: file,
    }),
  renameBackground: (itemId, displayName) =>
    request(`/backgrounds/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: JSON.stringify({ display_name: displayName }),
    }),
  deleteBackground: (itemId) =>
    request(`/backgrounds/${encodeURIComponent(itemId)}`, { method: "DELETE", expectEmpty: true }),
  listCustomUiPresets: () => request("/ui-presets/custom"),
  validateUiPreset: (preset) =>
    request("/ui-presets/validate", {
      method: "POST",
      body: JSON.stringify({ preset }),
    }),
  importUiPreset: (preset, conflictStrategy = "error") =>
    request("/ui-presets/import", {
      method: "POST",
      body: JSON.stringify({ preset, conflict_strategy: conflictStrategy }),
    }),
  duplicateUiPreset: (preset, displayName = null) =>
    request("/ui-presets/duplicate", {
      method: "POST",
      body: JSON.stringify({ preset, display_name: displayName }),
    }),
  exportUiPreset: (preset) =>
    request("/ui-presets/export", {
      method: "POST",
      body: JSON.stringify({ preset }),
    }),
  deleteCustomUiPreset: (presetId) =>
    request(`/ui-presets/custom/${encodeURIComponent(presetId)}`, {
      method: "DELETE",
    }),
  /* Archived image workflow API.
  listImageWorkflows: () => request("/image-workflows"),
  listImageWorkflowsForSession: (sessionId) =>
    request(`/image-workflows${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`),
  refreshImageWorkflows: () =>
    request("/image-workflows/refresh", {
      method: "POST",
    }),
  importImageWorkflow: (payload, sessionId = null) =>
    request(`/image-workflows/import${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  */
  openSystemFolder: (target) =>
    request("/system/open-folder", {
      method: "POST",
      body: JSON.stringify({ target }),
    }),
  /* Archived image generation API.
  getSessionImageSettings: (sessionId) => request(`/sessions/${sessionId}/image-settings`),
  saveSessionImageSettings: (sessionId, settings) =>
    request(`/sessions/${sessionId}/image-settings`, {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  getSceneImageSettings: (sessionId, sceneId, versionId = null) =>
    request(
      `/sessions/${sessionId}/scenes/${sceneId}/image-settings?${new URLSearchParams({
        ...(versionId ? { version_id: versionId } : {}),
      }).toString()}`,
    ),
  saveSceneImageSettings: (sessionId, sceneId, settings) =>
    request(`/sessions/${sessionId}/scenes/${sceneId}/image-settings`, {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
  saveImageWorkflowConfig: (workflowId, config) =>
    request(`/image-workflows/${workflowId}/config`, {
      method: "PATCH",
      body: JSON.stringify(config),
    }),
  validateImageWorkflow: (workflowId) =>
    request(`/image-workflows/${workflowId}/validate`, {
      method: "POST",
    }),
  generateImage: (payload) =>
    request("/images/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getImageJobStatus: () => request("/images/job-status"),
  generateImagePrompt: (payload) =>
    request("/images/prompt", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getSceneImagePrompt: (sessionId, sceneId, versionId = null, workflowId = null) =>
    request(
      `/sessions/${sessionId}/scenes/${sceneId}/image-prompt?${new URLSearchParams({
        ...(versionId ? { version_id: versionId } : {}),
        ...(workflowId ? { workflow_id: workflowId } : {}),
      }).toString()}`,
    ),
  refreshSceneImagePrompt: (sessionId, sceneId, payload) =>
    request(`/sessions/${sessionId}/scenes/${sceneId}/image-prompt`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getImage: (imageId) => request(`/images/${imageId}`),
  acceptImage: (imageId) =>
    request(`/images/${imageId}/accept`, {
      method: "POST",
    }),
  rejectImage: (imageId) =>
    request(`/images/${imageId}/reject`, {
      method: "POST",
    }),
  discardImage: (imageId) =>
    request(`/images/${imageId}/discard`, {
      method: "POST",
    }),
  listSceneImages: (sessionId, sceneId, versionId = null) =>
    request(
      `/sessions/${sessionId}/scenes/${sceneId}/images${
        versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""
      }`,
    ),
  */
  synthesizeTTS: (payload) =>
    request("/tts/synthesize", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  synthesizeTTSBatch: (payloads) =>
    request("/tts/synthesize-batch", {
      method: "POST",
      body: JSON.stringify(payloads),
    }),
  previewTTS: (payload) =>
    request("/tts/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  logTTSResilience: (payload) =>
    request("/tts/resilience-log", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  ttsStatus: () => request("/tts/status"),
  ttsProviders: () => request("/tts/providers"),
  ttsVoices: (provider) => request(`/tts/voices${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`),
  listCustomVoices: () => request("/tts/custom-voices"),
  createCustomVoice: (payload) =>
    request("/tts/custom-voices", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateCustomVoice: (voiceId, payload) =>
    request(`/tts/custom-voices/${encodeURIComponent(voiceId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  buildCustomVoice: (voiceId) =>
    request(`/tts/custom-voices/${encodeURIComponent(voiceId)}/build`, {
      method: "POST",
    }),
  saveCustomBreath: (voiceId, breathType, payload) =>
    request(`/tts/custom-voices/${encodeURIComponent(voiceId)}/breaths/${encodeURIComponent(breathType)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteCustomBreath: (voiceId, breathType) =>
    request(`/tts/custom-voices/${encodeURIComponent(voiceId)}/breaths/${encodeURIComponent(breathType)}`, {
      method: "DELETE",
    }),
  deleteCustomVoice: (voiceId, removeGeneratedAudio = false) =>
    request(
      `/tts/custom-voices/${encodeURIComponent(voiceId)}?remove_generated_audio=${removeGeneratedAudio ? "true" : "false"}`,
      { method: "DELETE" },
    ),
  cancelTTS: (payload = {}) =>
    request("/tts/cancel", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  unloadTTS: (payload = {}) =>
    request("/tts/unload", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listModelPresets: () => request("/settings/model-presets"),
  createModelPreset: (preset) =>
    request("/settings/model-presets", {
      method: "POST",
      body: JSON.stringify(preset),
    }),
  updateModelPreset: (presetId, preset) =>
    request(`/settings/model-presets/${presetId}`, {
      method: "PATCH",
      body: JSON.stringify(preset),
    }),
  deleteModelPreset: (presetId) =>
    request(`/settings/model-presets/${presetId}`, {
      method: "DELETE",
      expectEmpty: true,
    }),
  listCharacters: () => request("/characters"),
  createCharacter: (character) =>
    request("/characters", {
      method: "POST",
      body: JSON.stringify(character),
    }),
  getCharacter: (characterId) => request(`/characters/${characterId}`),
  updateCharacter: (characterId, character) =>
    request(`/characters/${characterId}`, {
      method: "PATCH",
      body: JSON.stringify(character),
    }),
  repairCharacterVisualProfile: (characterId, payload) =>
    request(`/characters/${characterId}/visual-profile/repair`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /* Archived character image reference API.
  listCharacterReferenceImages: (characterId, includeArchived = false) =>
    request(
      `/characters/${characterId}/reference-images${
        includeArchived ? "?include_archived=true" : ""
      }`,
    ),
  addCharacterReferenceImage: (characterId, payload) =>
    request(`/characters/${characterId}/reference-images`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateCharacterReferenceImage: (characterId, referenceId, payload) =>
    request(`/characters/${characterId}/reference-images/${referenceId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  setPrimaryCharacterReferenceImage: (characterId, referenceId) =>
    request(`/characters/${characterId}/reference-images/${referenceId}/primary`, {
      method: "POST",
    }),
  archiveCharacterReferenceImage: (characterId, referenceId) =>
    request(`/characters/${characterId}/reference-images/${referenceId}`, {
      method: "DELETE",
    }),
  */
  deleteCharacter: (characterId) =>
    request(`/characters/${characterId}`, {
      method: "DELETE",
      expectEmpty: true,
    }),
  listSessionCharacters: (sessionId) => request(`/sessions/${sessionId}/characters`),
  attachSessionCharacter: (sessionId, characterId, isActive = true) =>
    request(`/sessions/${sessionId}/characters/${characterId}`, {
      method: "POST",
      body: JSON.stringify({ is_active: isActive }),
    }),
  updateSessionCharacter: (sessionId, characterId, patch) =>
    request(`/sessions/${sessionId}/characters/${characterId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  detachSessionCharacter: (sessionId, characterId) =>
    request(`/sessions/${sessionId}/characters/${characterId}`, {
      method: "DELETE",
      expectEmpty: true,
    }),
  getWorldNotes: (sessionId) => request(`/sessions/${sessionId}/world`),
  saveWorldNotes: (sessionId, worldNotes) =>
    request(`/sessions/${sessionId}/world`, {
      method: "PATCH",
      body: JSON.stringify(worldNotes),
    }),
  getStoryFoundation: (sessionId) => request(`/sessions/${sessionId}/foundation`),
  ensureStoryFoundation: (sessionId, payload = {}) =>
    request(`/sessions/${sessionId}/foundation/ensure`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  refreshStoryFoundation: (sessionId, payload = {}) =>
    request(`/sessions/${sessionId}/foundation/refresh`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateStoryFoundation: (sessionId, payload = {}) =>
    request(`/sessions/${sessionId}/foundation`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  listQualityNotes: (sessionId, status = null) =>
    request(
      `/sessions/${sessionId}/quality/notes${
        status ? `?status=${encodeURIComponent(status)}` : ""
      }`,
    ),
  createQualityNote: (sessionId, note) =>
    request(`/sessions/${sessionId}/quality/notes`, {
      method: "POST",
      body: JSON.stringify(note),
    }),
  updateQualityNote: (sessionId, noteId, patch) =>
    request(`/sessions/${sessionId}/quality/notes/${noteId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  deleteQualityNote: (sessionId, noteId) =>
    request(`/sessions/${sessionId}/quality/notes/${noteId}`, {
      method: "DELETE",
      expectEmpty: true,
    }),
  getQualityHints: (sessionId) => request(`/sessions/${sessionId}/quality/hints`),
  listMemories: (sessionId) => request(`/sessions/${sessionId}/memories`),
  getSummary: (sessionId) => request(`/sessions/${sessionId}/summary`),
  refreshSummary: (sessionId) =>
    request(`/sessions/${sessionId}/summary/refresh`, {
      method: "POST",
    }),
  getStoryState: (sessionId) => request(`/sessions/${sessionId}/story-state`),
  extractStoryState: (sessionId, sceneId, versionId = null) =>
    request(
      `/sessions/${sessionId}/scenes/${sceneId}/story-state/extract${
        versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""
      }`,
      {
        method: "POST",
      },
    ),
  archiveStoryStateItem: (itemType, itemId) =>
    request(`/story-state/items/${encodeURIComponent(itemType)}/${encodeURIComponent(itemId)}/archive`, {
      method: "POST",
    }),
  disableStoryStateItem: (itemType, itemId) =>
    request(`/story-state/items/${encodeURIComponent(itemType)}/${encodeURIComponent(itemId)}/disable`, {
      method: "POST",
    }),
  restoreStoryStateItem: (itemType, itemId) =>
    request(`/story-state/items/${encodeURIComponent(itemType)}/${encodeURIComponent(itemId)}/restore`, {
      method: "POST",
    }),
  updateStoryStateItem: (itemType, itemId, patch) =>
    request(`/story-state/items/${encodeURIComponent(itemType)}/${encodeURIComponent(itemId)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  undoStoryStateRun: (runId) =>
    request(`/story-state/runs/${encodeURIComponent(runId)}/undo`, {
      method: "POST",
    }),
};
