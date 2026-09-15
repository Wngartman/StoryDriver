import { API_BASE_URL, api } from "../api.js";
import { buildFollowAudioChunkPlan, normalizeTtsFollowMode } from "./ttsFollowPlan.js";
import { premiumBatchStart, premiumBufferCoversNextBatch, premiumNarrationUnits } from "./ttsPremiumPlan.js";
import { contiguousGeneratedRange, resolvePerformanceSeek } from "./ttsSeekMath.js";
import { ttsBreathScheduler } from "./ttsBreathScheduler.js";
import { breathScheduleForTimeline } from "./ttsBreathPolicy.js";
import { localBrowserVoices, selectLocalBrowserVoice } from "./ttsLocalVoices.js";

const SPEED_OPTIONS = [0.75, 1, 1.1, 1.25, 1.5, 1.75];

let utterance = null;
let audio = null;
let timer = null;
let elapsed = 0;
let startedAt = 0;
let onUpdate = () => {};
let onError = () => {};
let playbackId = 0;
let stopRequested = false;
let suppressBrowserError = false;
let browserChunks = [];
let browserChunkIndex = 0;
let browserSpeed = 1;
let browserVoice = null;
let audioChunks = [];
let audioChunkIndex = null;
let audioChunkDurations = [];
let audioChunkDurationEstimates = [];
let audioChunkPauseDurations = [];
let audioChunkBreathBeforeDurations = [];
let audioChunkBreathAfterDurations = [];
let audioChunkBreathDecodedDurations = [];
let audioChunkRanges = [];
let audioChunkMetadata = [];
let audioChunkResponses = [];
let audioPreloads = new Map();
let audioTransitionGaps = [];
let requestedAudioSeek = null;
let lastChunkEndedAt = 0;
let transitionGapPending = false;
let lastScheduledPauseMs = 0;
let scheduledNarrationPause = false;
let boundaryCursorOverride = null;
let playbackWatchdog = null;
let playbackShouldBeActive = false;
let intentionallyPaused = false;
let awaitingUserResume = false;
let watchdogResumeAttempts = 0;
let lastWatchdogMediaTime = 0;
let lastWatchdogProgressAt = 0;
let activeChunkResolve = null;
const LONG_TEXT_CHARS = 3500;
const CURSOR_STORAGE_PREFIX = "storydriver_tts_cursor:";
const CURSOR_MAX_AGE_MS = 1000 * 60 * 60 * 24 * 7;
let activeCursorContext = null;
let lastCursorPersistedAt = 0;
let lastResilienceReportAt = 0;
let cursorLifecycleListenersAttached = false;
let kokoroHealthWatchdog = null;
let kokoroWasOffline = false;

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function ttsDebugEnabled() {
  try {
    return (
      window.localStorage?.getItem("storydriver_tts_debug") === "1" ||
      new URLSearchParams(window.location.search).has("ttsDebug")
    );
  } catch {
    return false;
  }
}

function debugTts(event, payload = {}) {
  if (!ttsDebugEnabled()) return;
  const snapshot = {
    event,
    chunkIndex: audioChunkIndex,
    chunkCount: audioChunks.length || null,
    src: audio?.currentSrc || audio?.src || null,
    currentTime: audio?.currentTime || 0,
    duration: Number.isFinite(audio?.duration) ? audio.duration : null,
    playbackRate: audio?.playbackRate || null,
    audioPaused: audio?.paused ?? null,
    audioEnded: audio?.ended ?? null,
    readyState: audio?.readyState ?? null,
    playbackShouldBeActive,
    intentionallyPaused,
    awaitingUserResume,
    ...payload,
  };
  console.debug("[StoryDriver TTS]", JSON.stringify(snapshot));
}

function cursorStorageKey(context = activeCursorContext) {
  if (!context?.sessionId || !context?.sceneId || !context?.versionId || !context?.textHash) return null;
  return [
    CURSOR_STORAGE_PREFIX,
    context.sessionId,
    context.sceneId,
    context.versionId,
    context.voice || "default",
    String(context.speed || 1),
    context.breathingMode || "natural",
    context.textHash,
  ].join(":");
}

function legacyCursorStorageKey(context = activeCursorContext) {
  if (!context?.sessionId || !context?.sceneId || !context?.versionId || !context?.textHash) return null;
  return [
    CURSOR_STORAGE_PREFIX,
    context.sessionId,
    context.sceneId,
    context.versionId,
    context.voice || "default",
    String(context.speed || 1),
    context.textHash,
  ].join(":");
}

function compatibleCursorEntries(context) {
  if (!context?.sessionId || !context?.sceneId || !context?.versionId || !context?.textHash) return [];
  const prefix = [
    CURSOR_STORAGE_PREFIX,
    context.sessionId,
    context.sceneId,
    context.versionId,
    "",
  ].join(":");
  try {
    return Object.keys(window.localStorage || {})
      .filter((key) => key.startsWith(prefix))
      .map((key) => {
        try {
          return { key, value: JSON.parse(window.localStorage?.getItem(key) || "null") };
        } catch {
          return null;
        }
      })
      .filter((entry) => (
        entry?.value
        && entry.value.sessionId === context.sessionId
        && entry.value.sceneId === context.sceneId
        && entry.value.versionId === context.versionId
        && entry.value.textHash === context.textHash
      ))
      .sort((left, right) => Number(right.value.updatedAtMs || 0) - Number(left.value.updatedAtMs || 0));
  } catch {
    return [];
  }
}

function reportTtsResilience(event, payload = {}, { throttleMs = 0 } = {}) {
  const now = Date.now();
  if (throttleMs && now - lastResilienceReportAt < throttleMs) return;
  lastResilienceReportAt = now;
  debugTts(event, payload);
  api
    .logTTSResilience({
      event,
      session_id: activeCursorContext?.sessionId || null,
      scene_id: activeCursorContext?.sceneId || null,
      version_id: activeCursorContext?.versionId || null,
      voice: activeCursorContext?.voice || null,
      voice_profile_id: activeCursorContext?.voiceProfileId || null,
      speed: activeCursorContext?.speed || null,
      text_hash: activeCursorContext?.textHash || null,
      chunk_index: audioChunkIndex,
      chunk_count: audioChunks.length || null,
      audio_url: audio?.currentSrc || audio?.src || null,
      audio_current_time: audio?.currentTime || 0,
      audio_duration: Number.isFinite(audio?.duration) ? audio.duration : null,
      global_narration_time: elapsed,
      audio_paused: audio?.paused ?? null,
      audio_ended: audio?.ended ?? null,
      audio_ready_state: audio?.readyState ?? null,
      playback_rate: audio?.playbackRate || null,
      ...payload,
    })
    .catch(() => {});
}

function persistNarrationCursor(reason = "tick", { force = false, cursorOverride = null } = {}) {
  const key = cursorStorageKey();
  if (!key) return;
  const now = Date.now();
  if (!force && now - lastCursorPersistedAt < 2000) return;
  const cursor = cursorOverride || currentNarrationCursor();
  if (!cursor) return;
  lastCursorPersistedAt = now;
  const payload = {
    ...activeCursorContext,
    chunkIndex: cursor.chunkIndex,
    chunkCount: cursor.chunkCount,
    chunkCurrentTime: cursor.chunkElapsed || 0,
    performancePhase: cursor.performancePhase || "speech",
    breathElapsed: cursor.breathElapsed || 0,
    globalNarrationTime: elapsed,
    audioUrl: audio?.currentSrc || audio?.src || null,
    reason,
    stopped: reason === "stop",
    completed: reason === "completed",
    updatedAt: new Date(now).toISOString(),
    updatedAtMs: now,
    cursor,
  };
  try {
    compatibleCursorEntries(activeCursorContext)
      .filter((entry) => entry.key !== key)
      .forEach((entry) => window.localStorage?.removeItem(entry.key));
    window.localStorage?.setItem(key, JSON.stringify(payload));
  } catch {
    // Cursor persistence is best-effort; narration still works without it.
  }
}

function loadNarrationCursor(context) {
  const exactKeys = [cursorStorageKey(context), legacyCursorStorageKey(context)].filter(Boolean);
  if (!exactKeys.length) return null;
  try {
    const compatible = compatibleCursorEntries(context);
    const parsed = compatible[0]?.value || null;
    if (!parsed || parsed.textHash !== context.textHash) return null;
    if (Date.now() - Number(parsed.updatedAtMs || 0) > CURSOR_MAX_AGE_MS) return null;
    if (parsed.completed || parsed.stopped) return null;
    const chunkIndex = Number(parsed.chunkIndex);
    const chunkCurrentTime = Number(parsed.chunkCurrentTime);
    const breathElapsed = Number(parsed.breathElapsed);
    if (!Number.isFinite(chunkIndex) || chunkIndex < 0) return null;
    return {
      ...parsed,
      chunkIndex,
      chunkCurrentTime: Number.isFinite(chunkCurrentTime) ? Math.max(0, chunkCurrentTime) : 0,
      breathElapsed: Number.isFinite(breathElapsed) ? Math.max(0, breathElapsed) : 0,
      performancePhase: ["start", "breath_before", "speech", "breath_after", "boundary_complete"].includes(parsed.performancePhase)
        ? parsed.performancePhase
        : "speech",
    };
  } catch {
    return null;
  }
}

function clearNarrationCursor(context = activeCursorContext) {
  const keys = [
    cursorStorageKey(context),
    legacyCursorStorageKey(context),
    ...compatibleCursorEntries(context).map((entry) => entry.key),
  ].filter(Boolean);
  if (!keys.length) return;
  try {
    [...new Set(keys)].forEach((key) => window.localStorage?.removeItem(key));
  } catch {
    // Best effort only.
  }
}

function ensureCursorLifecycleListeners() {
  if (cursorLifecycleListenersAttached || typeof window === "undefined") return;
  cursorLifecycleListenersAttached = true;
  window.addEventListener("beforeunload", () => persistNarrationCursor("beforeunload", { force: true }));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      persistNarrationCursor("visibility_hidden", { force: true });
    }
  });
}

function resolveActiveChunk(value) {
  if (!activeChunkResolve) return;
  const resolve = activeChunkResolve;
  activeChunkResolve = null;
  resolve(value);
}

function clearPlaybackWatchdog() {
  if (playbackWatchdog) {
    window.clearInterval(playbackWatchdog);
    playbackWatchdog = null;
  }
}

function ensureAudioElement() {
  if (!audio) {
    audio = new Audio();
    audio.preload = "auto";
    audio.playsInline = true;
  }
  return audio;
}

function clearAudioHandlers(element = audio) {
  if (!element) return;
  element.onloadedmetadata = null;
  element.oncanplay = null;
  element.oncanplaythrough = null;
  element.onplaying = null;
  element.onwaiting = null;
  element.onstalled = null;
  element.onpause = null;
  element.onended = null;
  element.onerror = null;
}

function clearKokoroHealthWatchdog() {
  if (kokoroHealthWatchdog) {
    window.clearInterval(kokoroHealthWatchdog);
    kokoroHealthWatchdog = null;
  }
  kokoroWasOffline = false;
}

function absoluteAudioUrl(audioUrl) {
  if (!audioUrl) return "";
  if (/^https?:\/\//i.test(audioUrl)) return audioUrl;
  return `${API_BASE_URL}${audioUrl}`;
}

function clearTimer() {
  if (timer) {
    window.clearInterval(timer);
    timer = null;
  }
}

function narrationChunks(text) {
  return (text || "")
    .replace(/\r/g, "")
    .split(/(?<=[.!?])\s+|\n{2,}/)
    .map((chunk) => chunk.trim())
    .filter(Boolean);
}

function groupedNarrationChunks(text, maxChars = 1200) {
  const target = Math.max(400, Number(maxChars) || 1200);
  const chunks = narrationChunks(text);
  const groups = [];
  let current = "";
  for (const chunk of chunks) {
    if (!current) {
      current = chunk;
      continue;
    }
    if (`${current} ${chunk}`.length <= target) {
      current = `${current} ${chunk}`;
      continue;
    }
    groups.push(current);
    current = chunk;
  }
  if (current) groups.push(current);
  return groups.length ? groups : [(text || "").trim()].filter(Boolean);
}

function normalizedTextMap(text = "") {
  let normalized = "";
  const map = [];
  let inWhitespace = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (/\s/.test(char)) {
      if (!inWhitespace) {
        normalized += " ";
        map.push(index);
        inWhitespace = true;
      }
      continue;
    }
    normalized += char;
    map.push(index);
    inWhitespace = false;
  }
  return { normalized, map };
}

function normalizeForRangeSearch(text = "") {
  return text.replace(/\s+/g, " ").trim();
}

function chunkRangesForText(fullText = "", chunks = []) {
  const source = normalizedTextMap(fullText);
  const ranges = [];
  let searchFrom = 0;
  let fallbackStart = 0;
  for (const chunk of chunks) {
    const needle = normalizeForRangeSearch(chunk);
    const index = needle ? source.normalized.indexOf(needle, searchFrom) : -1;
    if (index >= 0) {
      const start = source.map[index] ?? fallbackStart;
      const endMapIndex = Math.max(index, index + needle.length - 1);
      const end = Math.min(fullText.length, (source.map[endMapIndex] ?? start) + 1);
      ranges.push({ start, end });
      searchFrom = index + needle.length;
      fallbackStart = end;
      continue;
    }
    const fallbackEnd = Math.min(fullText.length, fallbackStart + String(chunk || "").length);
    ranges.push({ start: fallbackStart, end: fallbackEnd });
    fallbackStart = fallbackEnd;
  }
  return ranges;
}

function hashTextValue(text) {
  let hash = 2166136261;
  const value = text || "";
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `ui-${(hash >>> 0).toString(16)}-${value.length}`;
}

function canonicalNarrationText(text) {
  return String(text || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+(?=\n|$)/g, "")
    .trim();
}

function lightweightTextHash(text) {
  return hashTextValue(canonicalNarrationText(text));
}

function legacyLightweightTextHash(text) {
  return hashTextValue(String(text || ""));
}

function narrationJobId() {
  return `tts-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function effectiveKokoroChunkMode(text, chunkMode) {
  if (chunkMode === "full_scene_only") return "full_scene_only";
  if (chunkMode === "first_chunk_fast") return "first_chunk_fast";
  if (chunkMode === "progressive_chunks") return "progressive_chunks";
  if ((text || "").length >= LONG_TEXT_CHARS) return "progressive_chunks";
  return "full_scene_only";
}

function chunkFromProgress(chunks, progress) {
  if (!chunks.length) return null;
  const index = Math.min(chunks.length - 1, Math.max(0, Math.floor(progress * chunks.length)));
  return { chunkIndex: index, text: chunks[index] };
}

function wordCount(text) {
  return ((text || "").match(/\b[\w'-]+\b/g) || []).length;
}

function estimateSpeechDuration(text, speed = 1) {
  const words = wordCount(text);
  const rate = Math.max(80, 170 * (Number(speed) || 1));
  return Math.max(1.5, (words / rate) * 60);
}

function resetAudioPlan() {
  audioChunkDurations = [];
  audioChunkDurationEstimates = [];
    audioChunkPauseDurations = [];
  audioChunkBreathBeforeDurations = [];
  audioChunkBreathAfterDurations = [];
  audioChunkBreathDecodedDurations = [];
  audioChunkRanges = [];
  audioChunkMetadata = [];
  audioChunkResponses = [];
  audioTransitionGaps = [];
  requestedAudioSeek = null;
  lastChunkEndedAt = 0;
  transitionGapPending = false;
  lastScheduledPauseMs = 0;
  scheduledNarrationPause = false;
  boundaryCursorOverride = null;
  for (const preload of audioPreloads.values()) {
    try {
      preload.element.pause();
      preload.element.src = "";
      preload.element.load?.();
    } catch {
      // Best effort cleanup only.
    }
  }
  audioPreloads = new Map();
  ttsBreathScheduler.cancel();
}

function chunkDuration(index) {
  return audioChunkDurations[index] || audioChunkDurationEstimates[index] || 0;
}

function chunkPauseDuration(index) {
  if (index < 0 || index >= audioChunks.length - 1) return 0;
  return Math.max(0, Number(audioChunkPauseDurations[index]) || 0);
}

function chunkBreathBeforeDuration(index) {
  return Math.max(0, Number(audioChunkBreathBeforeDurations[index]) || 0);
}

function chunkBreathAfterDuration(index) {
  return Math.max(0, Number(audioChunkBreathAfterDurations[index]) || 0);
}

function chunkTimelineDuration(index) {
  return chunkBreathBeforeDuration(index) + chunkDuration(index) + chunkBreathAfterDuration(index) + chunkPauseDuration(index);
}

function speechOffset(index) {
  return chunkOffset(index) + chunkBreathBeforeDuration(index);
}

function chunkOffset(index) {
  if (index === null || index === undefined || index <= 0) return 0;
  return audioChunks
    .slice(0, index)
    .reduce((total, _chunk, chunkIndex) => total + chunkTimelineDuration(chunkIndex), 0);
}

function totalAudioDuration() {
  if (!audioChunks.length) return null;
  const total = audioChunks.reduce(
    (sum, _chunk, index) => sum + chunkTimelineDuration(index),
    0,
  );
  return total > 0 ? total : null;
}

async function waitForNarrationPause(pauseMs, runId, chunkIndex) {
  let remaining = Math.max(0, Math.min(1200, Number(pauseMs) || 0));
  if (!remaining || chunkIndex >= audioChunks.length - 1) {
    lastScheduledPauseMs = 0;
    return true;
  }
  scheduledNarrationPause = true;
  lastScheduledPauseMs = remaining;
  try {
    while (remaining > 0) {
      if (runId !== playbackId || stopRequested) return false;
      if (intentionallyPaused) {
        await sleep(50);
        continue;
      }
      const step = Math.min(50, remaining);
      await sleep(step);
      remaining -= step;
    }
    elapsed = chunkOffset(chunkIndex) + chunkTimelineDuration(chunkIndex);
    onUpdate({ elapsedTime: elapsed, duration: totalAudioDuration(), narrationCursor: currentNarrationCursor() });
    return true;
  } finally {
    scheduledNarrationPause = false;
  }
}

function bufferedAudioRange() {
  return contiguousGeneratedRange(
    audioChunks.map((_chunk, index) => chunkTimelineDuration(index)),
    audioChunkResponses.map((response) => Boolean(response?.audio_url)),
  );
}

function playbackRangeUpdate() {
  const buffered = bufferedAudioRange();
  return {
    duration: totalAudioDuration(),
    bufferedStartTime: buffered.start,
    bufferedEndTime: buffered.end,
    generatedChunkCount: buffered.generatedChunkCount,
    chunkCount: audioChunks.length,
  };
}

function cursorForAudioChunk(index, localElapsed = 0, performanceState = {}) {
  if (index === null || index === undefined || !audioChunks[index]) return null;
  const range = audioChunkRanges[index] || {};
  const metadata = audioChunkMetadata[index] || {};
  const chunkTotal = chunkDuration(index);
  return {
    chunkIndex: index,
    chunkCount: audioChunks.length,
    text: audioChunks[index],
    displayText: metadata.display_text || metadata.raw_text || audioChunks[index],
    normalizedTtsText: metadata.normalized_tts_text || audioChunks[index],
    rawText: metadata.raw_text || metadata.display_text || audioChunks[index],
    textStart: range.start ?? metadata.textStart ?? null,
    textEnd: range.end ?? metadata.textEnd ?? null,
    chunkElapsed: Math.max(0, localElapsed || 0),
    chunkDuration: chunkTotal || null,
    chunkOffset: chunkOffset(index),
    timingSource: audioChunkDurations[index] ? "audio_duration" : "estimated_duration",
    audioDurationKnown: Boolean(audioChunkDurations[index]),
    rangeIsFollowChunk: Boolean(range.isFollowChunk || metadata.isFollowChunk),
    followMode: range.followMode || metadata.followMode || null,
    unitIds: range.unitIds || metadata.unitIds || [],
    wordTimestamps: null,
    performancePhase: performanceState.performancePhase || "speech",
    breathElapsed: Math.max(0, Number(performanceState.breathElapsed) || 0),
    breathDuration: Math.max(0, Number(performanceState.breathDuration) || 0),
    breathEvent: performanceState.breathEvent || null,
  };
}

function currentNarrationCursor() {
  const breath = ttsBreathScheduler.snapshot();
  if (breath && audioChunks[breath.chunkIndex]) {
    const localElapsed = breath.phase === "breath_after" ? chunkDuration(breath.chunkIndex) : 0;
    return cursorForAudioChunk(breath.chunkIndex, localElapsed, {
      performancePhase: breath.phase,
      breathElapsed: breath.elapsed,
      breathDuration: breath.duration,
      breathEvent: breath.event,
    });
  }
  if (boundaryCursorOverride) return boundaryCursorOverride;
  if (audio && audioChunkIndex !== null && audioChunks[audioChunkIndex]) {
    const chunkElapsed = Math.max(0, audio.currentTime || 0);
    return cursorForAudioChunk(audioChunkIndex, chunkElapsed);
  }
  if (audio && audioChunks.length && totalAudioDuration()) {
    const cursor = chunkFromProgress(audioChunks, Math.min(0.999, elapsed / Math.max(totalAudioDuration(), 1)));
    if (!cursor) return null;
    const chunkStart = chunkOffset(cursor.chunkIndex);
    return cursorForAudioChunk(cursor.chunkIndex, Math.max(0, elapsed - chunkStart));
  }
  if (browserChunks[browserChunkIndex]) {
    return {
      chunkIndex: browserChunkIndex,
      chunkCount: browserChunks.length,
      text: browserChunks[browserChunkIndex],
      textStart: null,
      textEnd: null,
      chunkElapsed: 0,
      chunkDuration: null,
      timingSource: "browser_estimate",
      wordTimestamps: null,
    };
  }
  return null;
}

function preloadAudioResponse(index, response, speed) {
  if (index === null || index === undefined || !response?.audio_url || audioPreloads.has(index)) return;
  const url = absoluteAudioUrl(response.audio_url);
  const element = new Audio(url);
  const preload = {
    element,
    url,
    requestedAt: performance.now(),
    metadataAt: null,
    canPlayAt: null,
  };
  element.preload = "auto";
  element.playbackRate = Number(speed) || 1;
  element.onloadedmetadata = () => {
    preload.metadataAt = performance.now();
    if (Number.isFinite(element.duration)) {
      audioChunkDurations[index] = element.duration;
      refreshScheduledBreathDurations();
      onUpdate({ ...playbackRangeUpdate(), narrationCursor: currentNarrationCursor() });
    }
  };
  element.oncanplay = () => {
    preload.canPlayAt = performance.now();
  };
  audioPreloads.set(index, preload);
  try {
    element.load();
  } catch {
    audioPreloads.delete(index);
  }
}

function preloadBreathResponse(index, response) {
  if (index === null || index === undefined || !response?.breath_audio_url || !response?.breath_reference_used) return;
  const event = response.breath_before || response.breath_after;
  if (!event) return;
  audioChunkBreathDecodedDurations[index] = Math.max(0, Number(response.breath_duration) || 0);
  refreshScheduledBreathDurations();
  const url = absoluteAudioUrl(response.breath_audio_url);
  ttsBreathScheduler.preload(url).then((buffer) => {
    if (!buffer) return;
    audioChunkBreathDecodedDurations[index] = buffer.duration;
    refreshScheduledBreathDurations();
    onUpdate({ ...playbackRangeUpdate(), narrationCursor: currentNarrationCursor() });
  }).catch((error) => {
    reportTtsResilience("Breath preload failed", {
      chunk_index: index,
      breath_event: event,
      error_message: error?.message || String(error),
    });
  });
}

function currentBreathSchedule() {
  return breathScheduleForTimeline({
    responses: audioChunkResponses,
    speechDurations: audioChunks.map((_chunk, index) => chunkDuration(index)),
    pauseDurations: audioChunkPauseDurations,
  });
}

function refreshScheduledBreathDurations() {
  const schedule = currentBreathSchedule();
  audioChunkBreathBeforeDurations.fill(0);
  audioChunkBreathAfterDurations.fill(0);
  schedule.forEach((entry, index) => {
    const duration = Math.max(0, Number(audioChunkBreathDecodedDurations[index]) || 0);
    if (entry.breath_before) audioChunkBreathBeforeDurations[index] = duration;
    if (entry.breath_after) audioChunkBreathAfterDurations[index] = duration;
  });
}

function shouldScheduleBreath(response, phase, index) {
  if (audioChunkResponses[index] !== response) return false;
  return Boolean(currentBreathSchedule()[index]?.[phase]);
}

async function playBreathResponse(response, phase, runId, chunkIndex, offset = 0) {
  if (!shouldScheduleBreath(response, phase, chunkIndex)) return true;
  if (runId !== playbackId || stopRequested) return false;
  const url = absoluteAudioUrl(response.breath_audio_url);
  const event = response[phase];
  const base = phase === "breath_after"
    ? speechOffset(chunkIndex) + chunkDuration(chunkIndex)
    : chunkOffset(chunkIndex);
  playbackShouldBeActive = true;
  intentionallyPaused = false;
  onUpdate({ isNarrating: true, isPaused: false, isBuffering: false, statusMessage: "Narrating" });
  try {
    const completed = await ttsBreathScheduler.play({
      url,
      chunkIndex,
      phase,
      event,
      offset,
      playbackRate: 1,
      onProgress: (snapshot) => {
        if (!snapshot || runId !== playbackId) return;
        elapsed = base + snapshot.elapsed;
        const cursor = cursorForAudioChunk(
          chunkIndex,
          phase === "breath_after" ? chunkDuration(chunkIndex) : 0,
          {
            performancePhase: phase,
            breathElapsed: snapshot.elapsed,
            breathDuration: snapshot.duration,
            breathEvent: event,
          },
        );
        onUpdate({ elapsedTime: elapsed, duration: totalAudioDuration(), narrationCursor: cursor });
        persistNarrationCursor("breath_tick", { cursorOverride: cursor });
      },
    });
    if (completed) {
      const nextPhase = phase === "breath_before" ? "speech" : "boundary_complete";
      const cursor = cursorForAudioChunk(chunkIndex, phase === "breath_after" ? chunkDuration(chunkIndex) : 0, {
        performancePhase: nextPhase,
      });
      persistNarrationCursor("breath_completed", { force: true, cursorOverride: cursor });
    }
    return completed;
  } catch (error) {
    reportTtsResilience("Breath playback skipped", {
      chunk_index: chunkIndex,
      breath_event: event,
      error_message: error?.message || String(error),
    });
    return true;
  }
}

function startTimer() {
  clearTimer();
  startedAt = Date.now() - elapsed * 1000;
  timer = window.setInterval(() => {
    const breath = ttsBreathScheduler.snapshot();
    if (breath) {
      const base = breath.phase === "breath_after"
        ? speechOffset(breath.chunkIndex) + chunkDuration(breath.chunkIndex)
        : chunkOffset(breath.chunkIndex);
      elapsed = base + breath.elapsed;
    } else if (audio) {
      elapsed = (audioChunkIndex !== null ? speechOffset(audioChunkIndex) : 0) + (audio.currentTime || 0);
    } else {
      elapsed = Math.max(0, (Date.now() - startedAt) / 1000);
    }
    const duration =
      audio && audioChunkIndex !== null
        ? totalAudioDuration()
        : audio?.duration && Number.isFinite(audio.duration)
          ? audio.duration
          : totalAudioDuration();
    onUpdate({
      elapsedTime: elapsed,
      duration,
      narrationCursor: currentNarrationCursor(),
    });
    persistNarrationCursor("tick");
  }, 250);
}

function stopBrowserSpeech() {
  if (window.speechSynthesis) {
    suppressBrowserError = true;
    window.speechSynthesis.cancel();
  }
  utterance = null;
}

function stopAudio() {
  clearPlaybackWatchdog();
  clearKokoroHealthWatchdog();
  playbackShouldBeActive = false;
  intentionallyPaused = false;
  awaitingUserResume = false;
  watchdogResumeAttempts = 0;
  resolveActiveChunk(false);
  ttsBreathScheduler.cancel();
  if (audio) {
    clearAudioHandlers(audio);
    audio.pause();
    audio.currentTime = 0;
    audio.src = "";
    audio.load?.();
  }
  audio = null;
  audioChunkIndex = null;
}

function selectedBrowserVoice(voiceName) {
  return selectLocalBrowserVoice(window.speechSynthesis, voiceName);
}

function speakBrowserChunk(runId) {
  if (runId !== playbackId || !browserChunks.length) return;
  suppressBrowserError = false;
  stopBrowserSpeech();
  suppressBrowserError = false;

  const resolvedVoice = selectedBrowserVoice(browserVoice);
  if (!resolvedVoice) {
    clearTimer();
    onError("No local browser voice is available. Use Kokoro or install an offline system voice.");
    onUpdate({ isNarrating: false, isPaused: true, statusMessage: "Local voice unavailable" });
    return;
  }
  utterance = new SpeechSynthesisUtterance(browserChunks[browserChunkIndex]);
  utterance.rate = browserSpeed;
  utterance.voice = resolvedVoice;

  utterance.onstart = () => {
    if (runId !== playbackId) return;
    startTimer();
    onUpdate({
      isNarrating: true,
      isPaused: false,
      elapsedTime: elapsed,
      duration: null,
      provider: "browser",
      voice: resolvedVoice?.name || browserVoice || null,
      lastProviderUsed: "browser",
      lastVoiceUsed: resolvedVoice?.name || browserVoice || "Default Browser Voice",
      lastGeneratedAudioFile: null,
      narrationCursor: { chunkIndex: browserChunkIndex, text: browserChunks[browserChunkIndex] },
      statusMessage: "Narrating...",
    });
  };
  utterance.onend = () => {
    if (runId !== playbackId || stopRequested) return;
    if (browserChunkIndex < browserChunks.length - 1) {
      browserChunkIndex += 1;
      speakBrowserChunk(runId);
      return;
    }
    clearTimer();
    onUpdate({
      isNarrating: false,
      isPaused: false,
      elapsedTime: 0,
      duration: null,
      narrationCursor: null,
      statusMessage: "Ready",
    });
  };
  utterance.onerror = (event) => {
    if (runId !== playbackId || stopRequested || suppressBrowserError || event.error === "interrupted" || event.error === "canceled") {
      return;
    }
    clearTimer();
    onError(event.error ? `Browser TTS failed: ${event.error}.` : "Browser TTS failed.");
    onUpdate({ isNarrating: false, isPaused: false, narrationCursor: null, statusMessage: "Ready" });
  };

  window.speechSynthesis.speak(utterance);
}

function playBrowser({ text, speed, voice }) {
  if (!("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") {
    throw new Error("Browser text-to-speech is not available in this browser.");
  }
  if (!selectedBrowserVoice(voice)) {
    throw new Error("No local browser voice is available. Use Kokoro or install an offline system voice.");
  }

  stopBrowserSpeech();
  stopAudio();
  resetAudioPlan();
  elapsed = 0;
  browserChunks = narrationChunks(text);
  browserChunkIndex = 0;
  browserSpeed = Number(speed) || 1;
  browserVoice = voice;
  speakBrowserChunk(playbackId);
}

async function requestAudioPlay({ runId, reason = "play", statusMessage = "Narrating" } = {}) {
  if (!audio || runId !== playbackId || stopRequested) return false;
  playbackShouldBeActive = true;
  intentionallyPaused = false;
  debugTts("play-request", { reason });
  try {
    const playPromise = audio.play();
    if (playPromise?.then) {
      await playPromise;
    }
    if (runId !== playbackId || stopRequested) return false;
    awaitingUserResume = false;
    playbackShouldBeActive = true;
    startPlaybackWatchdog(runId);
    debugTts("play-resolved", { reason });
    onUpdate({
      isNarrating: true,
      isPaused: false,
      needsUserResume: false,
      isBuffering: false,
      playbackBlocked: false,
      playbackBlockReason: null,
      statusMessage,
      narrationCursor: currentNarrationCursor(),
    });
    return true;
  } catch (error) {
    if (runId !== playbackId || stopRequested || error?.name === "AbortError") return false;
    clearTimer();
    playbackShouldBeActive = false;
    awaitingUserResume = true;
    debugTts("play-rejected", { reason, errorName: error?.name, errorMessage: error?.message });
    persistNarrationCursor("play_rejected", { force: true });
    reportTtsResilience("Frontend play() rejected", {
      reason,
      error_name: error?.name || null,
      error_message: error?.message || String(error),
      kokoro_health: null,
      cached_chunk_existed: audioChunkIndex !== null ? audioPreloads.has(audioChunkIndex) : null,
    });
    onUpdate({
      isNarrating: true,
      isPaused: true,
      needsUserResume: true,
      isBuffering: false,
      playbackBlocked: true,
      playbackBlockReason: error?.name || "play_rejected",
      statusMessage: "Tap play to continue narration",
      narrationCursor: currentNarrationCursor(),
    });
    return false;
  }
}

function startPlaybackWatchdog(runId) {
  clearPlaybackWatchdog();
  if (!audio) return;
  lastWatchdogMediaTime = audio.currentTime || 0;
  lastWatchdogProgressAt = Date.now();
  playbackWatchdog = window.setInterval(() => {
    if (runId !== playbackId || stopRequested || !audio) {
      clearPlaybackWatchdog();
      return;
    }
    if (!playbackShouldBeActive || intentionallyPaused || awaitingUserResume) return;
    const mediaTime = audio.currentTime || 0;
    const readyState = audio.readyState || 0;
    if (!audio.paused && mediaTime > lastWatchdogMediaTime + 0.05) {
      lastWatchdogMediaTime = mediaTime;
      lastWatchdogProgressAt = Date.now();
      return;
    }
    const stalledMs = Date.now() - lastWatchdogProgressAt;
    if (readyState < 3 && stalledMs > 1200) {
      onUpdate({ isBuffering: true, statusMessage: "Buffering..." });
      return;
    }
    if ((audio.paused || stalledMs > 2800) && !audio.ended && watchdogResumeAttempts < 1) {
      watchdogResumeAttempts += 1;
      debugTts("watchdog-resume", { stalledMs, readyState, audioPaused: audio.paused });
      persistNarrationCursor("watchdog_resume", { force: true });
      reportTtsResilience(
        "Frontend watchdog resume",
        { stalled_ms: stalledMs, ready_state: readyState, audio_paused: audio.paused },
        { throttleMs: 5000 },
      );
      requestAudioPlay({ runId, reason: "watchdog", statusMessage: "Narrating" });
      return;
    }
    if ((audio.paused || stalledMs > 3600) && !audio.ended && watchdogResumeAttempts >= 1) {
      playbackShouldBeActive = false;
      awaitingUserResume = true;
      debugTts("watchdog-needs-user-resume", { stalledMs, readyState, audioPaused: audio.paused });
      persistNarrationCursor("watchdog_needs_user_resume", { force: true });
      reportTtsResilience("Frontend watchdog needs user resume", {
        stalled_ms: stalledMs,
        ready_state: readyState,
        audio_paused: audio.paused,
        playback_block_reason: audio.paused ? "audio_paused" : "no_progress",
      });
      onUpdate({
        isNarrating: true,
        isPaused: true,
        needsUserResume: true,
        isBuffering: false,
        playbackBlocked: true,
        playbackBlockReason: audio.paused ? "audio_paused" : "no_progress",
        statusMessage: "Tap play to continue narration",
        narrationCursor: currentNarrationCursor(),
      });
    }
  }, 1000);
}

function playAudioResponse({
  response,
  runId,
  speed,
  voice,
  statusMessage = "Narrating...",
  chunkIndex = null,
  chunkCount = null,
  startAtSeconds = 0,
  autoplay = true,
}) {
  return new Promise((resolve, reject) => {
    if (runId !== playbackId || stopRequested) {
      resolve(false);
      return;
    }
    resolveActiveChunk(false);
    audioChunkIndex = chunkIndex;
    const audioUrl = absoluteAudioUrl(response.audio_url);
    const preload = chunkIndex !== null ? audioPreloads.get(chunkIndex) : null;
    const previousAudio = audio;
    const activeAudio = preload?.element || ensureAudioElement();
    if (previousAudio && previousAudio !== activeAudio) {
      clearAudioHandlers(previousAudio);
      if (!previousAudio.ended) previousAudio.pause();
    }
    clearAudioHandlers(activeAudio);
    if (chunkIndex !== null) {
      audioPreloads.delete(chunkIndex);
    }
    audio = activeAudio;
    audio.preload = "auto";
    audio.playsInline = true;
    audio.playbackRate = Number(speed) || 1;
    if (!preload && audio.src !== audioUrl) {
      audio.src = audioUrl;
      try {
        audio.load?.();
      } catch {
        // Browser can still lazy load on play.
      }
    }
    const resumeAt = Math.max(0, Number(startAtSeconds) || 0);
    let autoplayRequested = false;
    const applyResumePosition = () => {
      if (resumeAt <= 0) return;
      try {
        const target = Number.isFinite(audio.duration)
          ? Math.min(Math.max(0, audio.duration - 0.2), resumeAt)
          : resumeAt;
        audio.currentTime = target;
        elapsed = (chunkIndex !== null ? speechOffset(chunkIndex) : 0) + (audio.currentTime || target);
        onUpdate({ elapsedTime: elapsed, narrationCursor: currentNarrationCursor() });
        persistNarrationCursor("resume_seek", { force: true });
      } catch {
        // The metadata handler retries when the browser accepts seeking.
      }
    };
    const beginAutoplay = () => {
      if (!autoplay || autoplayRequested) return;
      autoplayRequested = true;
      requestAudioPlay({ runId, reason: chunkIndex === null ? "single-audio" : `chunk-${chunkIndex}`, statusMessage });
    };
    activeChunkResolve = resolve;
    watchdogResumeAttempts = 0;
    audio.onloadedmetadata = () => {
      if (runId !== playbackId) return;
      if (chunkIndex !== null && Number.isFinite(audio.duration)) {
        audioChunkDurations[chunkIndex] = audio.duration;
        refreshScheduledBreathDurations();
      }
      const duration =
        chunkIndex !== null
          ? totalAudioDuration()
          : Number.isFinite(audio.duration)
            ? audio.duration
            : response.duration || null;
      onUpdate({ ...(chunkIndex !== null ? playbackRangeUpdate() : { duration }), narrationCursor: currentNarrationCursor() });
      debugTts("loadedmetadata", { chunkIndex, duration });
      applyResumePosition();
      if (!autoplay) {
        intentionallyPaused = true;
        playbackShouldBeActive = false;
        awaitingUserResume = false;
        elapsed = (chunkIndex !== null ? speechOffset(chunkIndex) : 0) + (audio.currentTime || 0);
        onUpdate({
          ...(chunkIndex !== null ? playbackRangeUpdate() : {}),
          isNarrating: true,
          isPaused: true,
          needsUserResume: false,
          isBuffering: false,
          elapsedTime: elapsed,
          statusMessage: "Paused",
          narrationCursor: currentNarrationCursor(),
        });
      } else {
        beginAutoplay();
      }
    };
    audio.oncanplay = () => {
      if (runId !== playbackId) return;
      debugTts("canplay", { chunkIndex });
    };
    audio.oncanplaythrough = () => {
      if (runId !== playbackId) return;
      debugTts("canplaythrough", { chunkIndex });
    };
    audio.onwaiting = () => {
      if (runId !== playbackId || intentionallyPaused || stopRequested) return;
      debugTts("waiting", { chunkIndex });
      onUpdate({ isBuffering: true, statusMessage: "Buffering...", narrationCursor: currentNarrationCursor() });
    };
    audio.onstalled = () => {
      if (runId !== playbackId || intentionallyPaused || stopRequested) return;
      debugTts("stalled", { chunkIndex });
      onUpdate({ isBuffering: true, statusMessage: "Buffering...", narrationCursor: currentNarrationCursor() });
    };
    audio.onplay = () => {
      if (runId !== playbackId) return;
      if (chunkIndex !== null && chunkIndex > 0 && transitionGapPending && lastChunkEndedAt) {
        const gap = Math.max(0, performance.now() - lastChunkEndedAt - lastScheduledPauseMs);
        audioTransitionGaps.push(gap);
        transitionGapPending = false;
        lastScheduledPauseMs = 0;
      }
      startTimer();
      onUpdate({
        isNarrating: true,
        isPaused: false,
        needsUserResume: false,
        isBuffering: false,
        playbackBlocked: false,
        playbackBlockReason: null,
        provider: response.effective_provider || response.provider || "kokoro",
        voice: response.effective_voice || voice || "af_heart",
        lastProviderUsed: response.effective_provider || response.provider || "kokoro",
        lastVoiceUsed: response.effective_voice || voice || "af_heart",
        lastModelUsed: response.effective_model || null,
        lastGeneratedAudioFile: response.audio_url,
        statusMessage,
        ...(chunkIndex !== null ? { duration: totalAudioDuration() } : {}),
        narrationCursor: currentNarrationCursor(),
        lastTransitionGapMs: audioTransitionGaps.length ? Math.round(audioTransitionGaps.at(-1)) : null,
        maxTransitionGapMs: audioTransitionGaps.length ? Math.round(Math.max(...audioTransitionGaps)) : null,
      });
      const resource = performance.getEntriesByName(audioUrl).at(-1);
      debugTts("play", {
        chunkIndex,
        chunkCount,
        requestedProvider: response.requested_provider || null,
        effectiveProvider: response.effective_provider || response.provider || null,
        effectiveVoice: response.effective_voice || voice || null,
        synthesisSeconds: response.synthesis_seconds ?? null,
        preloadRequestedAt: preload?.requestedAt ?? null,
        preloadMetadataAt: preload?.metadataAt ?? null,
        preloadCanPlayAt: preload?.canPlayAt ?? null,
        resourceFetchMs: Number.isFinite(resource?.duration) ? Math.round(resource.duration) : null,
      });
    };
    audio.onplaying = () => {
      if (runId !== playbackId) return;
      debugTts("playing", { chunkIndex });
      onUpdate({ isNarrating: true, isPaused: false, needsUserResume: false, isBuffering: false });
    };
    audio.onpause = () => {
      if (runId !== playbackId || stopRequested) return;
      if (!audio?.ended) {
        clearTimer();
        elapsed = (chunkIndex !== null ? speechOffset(chunkIndex) : 0) + (audio?.currentTime || 0);
        debugTts("pause", { chunkIndex, intentionallyPaused });
        persistNarrationCursor(intentionallyPaused ? "pause" : "unexpected_pause", { force: true });
        onUpdate({
          isPaused: true,
          elapsedTime: elapsed,
          narrationCursor: currentNarrationCursor(),
          statusMessage: intentionallyPaused ? "Paused" : "Tap play to continue narration",
          needsUserResume: !intentionallyPaused,
          playbackBlocked: !intentionallyPaused,
          playbackBlockReason: intentionallyPaused ? null : "audio_pause_event",
        });
      }
    };
    audio.onended = () => {
      if (runId !== playbackId || stopRequested) {
        resolveActiveChunk(false);
        return;
      }
      clearTimer();
      playbackShouldBeActive = false;
      if (chunkIndex !== null) {
        const finalChunkTime = Number.isFinite(audio?.duration) ? audio.duration : chunkDuration(chunkIndex);
        elapsed = speechOffset(chunkIndex) + finalChunkTime;
        lastChunkEndedAt = performance.now();
        transitionGapPending = true;
        onUpdate({ elapsedTime: elapsed, duration: totalAudioDuration(), isBuffering: false, narrationCursor: currentNarrationCursor() });
        persistNarrationCursor("chunk_ended", { force: true });
      }
      debugTts("ended", { chunkIndex, chunkCount });
      resolveActiveChunk(true);
    };
    audio.onerror = () => {
      if (runId !== playbackId || stopRequested) {
        resolveActiveChunk(false);
        return;
      }
      clearTimer();
      debugTts("error", { chunkIndex, mediaError: audio?.error?.code || null });
      persistNarrationCursor("audio_error", { force: true });
      reportTtsResilience("Frontend audio element error", {
        chunk_index: chunkIndex,
        chunk_count: chunkCount,
        media_error_code: audio?.error?.code || null,
        media_error_message: audio?.error?.message || null,
        cached_chunk_existed: Boolean(response?.cached),
      });
      activeChunkResolve = null;
      reject(new Error("Narration audio could not be played."));
    };
    if (resumeAt > 0 && audio.readyState >= 1) applyResumePosition();
    if (autoplay) {
      if (resumeAt > 0 && audio.readyState < 1) {
        onUpdate({ isBuffering: true, statusMessage: "Restoring narration position..." });
      } else {
        beginAutoplay();
      }
    } else {
      intentionallyPaused = true;
      playbackShouldBeActive = false;
      onUpdate({ isNarrating: true, isPaused: true, isBuffering: false, statusMessage: "Paused" });
    }
  });
}

async function synthesizeKokoroChunk({ text, speed, voice, kokoroOptions, metadata = {} }) {
  return api.synthesizeTTS({
    text,
    voice: voice || null,
    speed: Number(speed) || 1,
    provider: "kokoro",
    ...metadata,
    ...kokoroOptions,
  });
}

async function kokoroHealthSnapshot() {
  try {
    const status = await api.ttsStatus();
    const kokoro = status?.kokoro || {};
    return {
      reachable: Boolean(kokoro.reachable ?? status?.reachable),
      provider: status?.active_provider || "kokoro",
      base_url: kokoro.base_url || status?.kokoro_base_url || null,
      error: kokoro.error || status?.error || null,
      last_chunk_index: status?.last_chunk_index ?? null,
      last_chunk_count: status?.last_chunk_count ?? null,
    };
  } catch (error) {
    return {
      reachable: false,
      error: error?.message || String(error),
    };
  }
}

function startKokoroHealthWatchdog(runId) {
  clearKokoroHealthWatchdog();
  kokoroHealthWatchdog = window.setInterval(async () => {
    if (runId !== playbackId || stopRequested || !activeCursorContext || activeCursorContext.provider !== "kokoro") {
      clearKokoroHealthWatchdog();
      return;
    }
    if (!playbackShouldBeActive && !awaitingUserResume) return;
    const health = await kokoroHealthSnapshot();
    if (!health.reachable) {
      if (!kokoroWasOffline) {
        kokoroWasOffline = true;
        persistNarrationCursor("kokoro_offline", { force: true });
        reportTtsResilience("Kokoro health watcher offline", { kokoro_health: health });
      }
      if (audio?.paused || awaitingUserResume) {
        onUpdate({
          isBuffering: true,
          statusMessage: "Kokoro reconnecting...",
          narrationCursor: currentNarrationCursor(),
        });
      }
      return;
    }
    if (kokoroWasOffline) {
      kokoroWasOffline = false;
      reportTtsResilience("Kokoro health watcher recovered", { kokoro_health: health });
      onUpdate({
        isBuffering: false,
        statusMessage: awaitingUserResume ? "Tap play to continue narration" : "Narrating",
        narrationCursor: currentNarrationCursor(),
      });
    }
  }, 10000);
}

async function synthesizeKokoroChunkWithRetry(args, { runId, index, chunkCount } = {}) {
  const delays = [0, 1000, 3000];
  let lastError = null;
  for (let attempt = 0; attempt < delays.length; attempt += 1) {
    if (runId !== playbackId || stopRequested) return null;
    if (delays[attempt]) {
      await sleep(delays[attempt]);
    }
    try {
      const response = await synthesizeKokoroChunk(args);
      if (attempt > 0) {
        reportTtsResilience("Chunk synthesis recovered", {
          chunk_index: index,
          chunk_count: chunkCount,
          retry_attempt: attempt,
          cached_chunk_existed: Boolean(response?.cached),
        });
      }
      return response;
    } catch (error) {
      lastError = error;
      persistNarrationCursor("synthesis_error", { force: true });
      const health = await kokoroHealthSnapshot();
      const statusMessage = health.reachable ? "Retrying narration chunk..." : "Kokoro reconnecting...";
      onUpdate({
        isNarrating: true,
        isPaused: false,
        isBuffering: true,
        needsUserResume: false,
        playbackBlocked: false,
        statusMessage,
        narrationCursor: currentNarrationCursor() || cursorForAudioChunk(index, 0),
      });
      reportTtsResilience("Chunk synthesis failed", {
        chunk_index: index,
        chunk_count: chunkCount,
        retry_attempt: attempt,
        error_message: error?.message || String(error),
        kokoro_health: health,
        cached_chunk_existed: false,
      });
    }
  }
  throw lastError || new Error("Kokoro chunk synthesis failed.");
}

async function playKokoroFull({ text, speed, voice, kokoroOptions = {}, metadata = {}, runId }) {
  const resumeCursor = metadata.resume_cursor || null;
  const startAtSeconds = Math.max(0, Number(resumeCursor?.chunkCurrentTime) || 0);
  elapsed = startAtSeconds;
  audioChunks = [text];
  audioChunkDurations = [null];
  audioChunkDurationEstimates = [estimateSpeechDuration(text, speed)];
  audioChunkRanges = [{ start: 0, end: text.length }];
  audioChunkMetadata = [{
    index: 0,
    raw_text: text,
    display_text: text,
    normalized_tts_text: text,
    textStart: 0,
    textEnd: text.length,
    isFollowChunk: false,
  }];
  onUpdate({
    isNarrating: true,
    isPaused: false,
    elapsedTime: startAtSeconds,
    duration: null,
    provider: "kokoro",
    voice: voice || "af_heart",
    statusMessage: startAtSeconds > 0 ? "Resuming narration..." : "Preparing narration...",
    duration: totalAudioDuration(),
    narrationCursor: cursorForAudioChunk(0, startAtSeconds),
  });

  const response = await synthesizeKokoroChunkWithRetry(
    {
      text,
      speed,
      voice,
      kokoroOptions,
      metadata: {
        ...metadata,
        follow_mode: "off",
        text_start_offset: 0,
        text_end_offset: text.length,
        normalized_tts_text: text,
      },
    },
    { runId, index: 0, chunkCount: 1 },
  );

  if (runId !== playbackId || stopRequested) return;
  if (!response) return;
  onUpdate({
    statusMessage: response.cached ? "Using cached narration..." : "Narrating...",
  });
  await playAudioResponse({ response, runId, speed, voice, startAtSeconds });
  if (runId !== playbackId || stopRequested) return;
  elapsed = 0;
  clearKokoroHealthWatchdog();
  clearNarrationCursor();
  onUpdate({ isNarrating: false, isPaused: false, elapsedTime: 0, narrationCursor: null, statusMessage: "Ready" });
}

async function playKokoroChunked({ text, provider = "kokoro", speed, voice, kokoroOptions = {}, chunkSize = 1200, metadata = {}, runId, progressive = false }) {
  const premium = provider === "high_quality_local";
  const premiumUnits = premium ? premiumNarrationUnits(text) : [];
  const prebufferChunks = Math.max(1, Math.min(3, Number(metadata.prebuffer_chunks) || 2));
  const followMode = normalizeTtsFollowMode(metadata.follow_mode || "off", "off");
  const followAudioPlan = premium
    ? { chunks: [], mode: followMode }
    : buildFollowAudioChunkPlan(text, {
        followMode,
        maxChars: chunkSize,
        speed,
        chunkingProfile: metadata.tts_chunking_profile || "natural",
      });
  const followChunks = followAudioPlan.chunks || [];
  const chunkFollowMode = followChunks.length ? followAudioPlan.mode || followMode : "off";
  const chunks = premium
    ? premiumUnits.map((unit) => unit.text)
    : followChunks.length
      ? followChunks.map((chunk) => chunk.text)
      : groupedNarrationChunks(text, chunkSize);
  audioChunks = chunks;
  audioChunkDurations = new Array(chunks.length).fill(null);
  audioChunkDurationEstimates = chunks.map((chunk) => estimateSpeechDuration(chunk, speed));
  audioChunkPauseDurations = new Array(chunks.length).fill(0);
  audioChunkBreathBeforeDurations = new Array(chunks.length).fill(0);
  audioChunkBreathAfterDurations = new Array(chunks.length).fill(0);
  audioChunkBreathDecodedDurations = new Array(chunks.length).fill(0);
  audioChunkResponses = new Array(chunks.length).fill(null);
  audioChunkMetadata = premium
    ? premiumUnits.map((unit, index) => ({
        ...unit,
        index,
        raw_text: unit.text,
        display_text: unit.text,
        normalized_tts_text: unit.text,
        isFollowChunk: false,
      }))
    : followChunks.length
    ? followChunks
    : chunks.map((chunk, index) => ({
        index,
        raw_text: chunk,
        display_text: chunk,
        normalized_tts_text: chunk,
        isFollowChunk: false,
      }));
  audioChunkRanges = followChunks.length
    ? followChunks.map((chunk) => ({
        start: chunk.textStart,
        end: chunk.textEnd,
        followMode: chunk.followMode,
        unitIds: chunk.unitIds,
        isFollowChunk: true,
      }))
    : chunkRangesForText(text, chunks);
  const jobId = metadata.narration_job_id || narrationJobId();
  const fullTextHash = metadata.text_hash || lightweightTextHash(text);
  const resumeCursor = metadata.resume_cursor || null;
  let startIndex = Math.max(0, Math.min(chunks.length - 1, Number(resumeCursor?.chunkIndex) || 0));
  let startAtSeconds = Math.max(0, Number(resumeCursor?.chunkCurrentTime) || 0);
  let startPhase = resumeCursor
    ? (resumeCursor.performancePhase || resumeCursor.cursor?.performancePhase || "speech")
    : "start";
  let startBreathSeconds = Math.max(0, Number(resumeCursor?.breathElapsed || resumeCursor?.cursor?.breathElapsed) || 0);
  if (startPhase === "boundary_complete" && startIndex < chunks.length - 1) {
    startIndex += 1;
    startAtSeconds = 0;
    startBreathSeconds = 0;
    startPhase = "start";
  } else if (
    startPhase === "speech" &&
    startAtSeconds > Math.max(2, audioChunkDurationEstimates[startIndex] - 0.5) &&
    startIndex < chunks.length - 1
  ) {
    startIndex += 1;
    startAtSeconds = 0;
    startPhase = "start";
  }
  elapsed = startPhase === "breath_before"
    ? chunkOffset(startIndex) + startBreathSeconds
    : startPhase === "breath_after"
      ? speechOffset(startIndex) + audioChunkDurationEstimates[startIndex] + startBreathSeconds
      : speechOffset(startIndex) + startAtSeconds;
  const synthPromises = new Map();
  const batchPromises = new Map();
  let premiumProducerPromise = null;
  let premiumUnloadPromise = null;
  const chunkMetadata = (index) => ({
    ...metadata,
    narration_job_id: jobId,
    chunk_index: index,
    chunk_count: chunks.length,
    text_hash: fullTextHash,
    text_start_offset: audioChunkRanges[index]?.start ?? null,
    text_end_offset: audioChunkRanges[index]?.end ?? null,
    follow_mode: chunkFollowMode,
    normalized_tts_text: audioChunkMetadata[index]?.normalized_tts_text || chunks[index],
    paragraph_break_after: Boolean(audioChunkMetadata[index]?.paragraphBreakAfter),
    scene_break_after: Boolean(audioChunkMetadata[index]?.sceneBreakAfter),
    speaker_change_after: Boolean(audioChunkMetadata[index]?.speakerChangeAfter),
  });
  const premiumPayload = (index) => ({
    text: chunks[index],
    speed: Number(speed) || 1,
    voice: voice || "Serena",
    provider: "high_quality_local",
    voice_profile_id: metadata.voice_profile_id || "premium_female_narrator",
    ...chunkMetadata(index),
  });
  const startPremiumBatch = (index) => {
    const batchStart = premiumBatchStart(index);
    if (batchPromises.has(batchStart)) return batchPromises.get(batchStart);
    const indexes = Array.from({ length: Math.min(4, chunks.length - batchStart) }, (_value, offset) => batchStart + offset);
    const promise = api.synthesizeTTSBatch(indexes.map((chunkIndex) => premiumPayload(chunkIndex)))
      .then((responses) => {
        if (!Array.isArray(responses) || responses.length !== indexes.length) {
          throw new Error("Premium narration returned an incomplete batch.");
        }
        if (runId !== playbackId || stopRequested) return responses;
        indexes.forEach((chunkIndex, offset) => {
          const response = responses[offset];
          audioChunkResponses[chunkIndex] = response;
          response.pause_after_ms = Math.min(1500, Math.max(0, Number(response?.pause_after_ms) || 0));
          audioChunkPauseDurations[chunkIndex] = response.pause_after_ms / 1000;
          preloadAudioResponse(chunkIndex, response, speed);
          preloadBreathResponse(chunkIndex, response);
        });
        const effectiveProvider = responses[0]?.effective_provider || responses[0]?.provider;
        if (effectiveProvider === "kokoro") {
          onUpdate({ provider: "kokoro", statusMessage: "Qwen3-TTS unavailable; using Kokoro fallback" });
        }
        onUpdate(playbackRangeUpdate());
        if (indexes.at(-1) === chunks.length - 1 && !premiumUnloadPromise) {
          premiumUnloadPromise = api.unloadTTS({ provider: "high_quality_local" }).catch((error) => {
            reportTtsResilience("Premium worker unload failed", { error_message: error?.message || String(error) });
            return null;
          });
        }
        return responses;
      });
    batchPromises.set(batchStart, promise);
    return promise;
  };
  const startSynthesis = (index) => {
    if (index < 0 || index >= chunks.length) return null;
    if (synthPromises.has(index)) return synthPromises.get(index);
    const batchStart = premiumBatchStart(index);
    const source = premium
      ? startPremiumBatch(index).then((responses) => responses[index - batchStart])
      : synthesizeKokoroChunkWithRetry(
          {
            text: chunks[index],
            speed,
            voice,
            kokoroOptions,
            metadata: chunkMetadata(index),
          },
          { runId, index, chunkCount: chunks.length },
        );
    const promise = source
      .then((response) => {
        if (!response) return { response: null };
        if (runId !== playbackId || stopRequested) return { response: null };
        audioChunkResponses[index] = response;
        response.pause_after_ms = Math.min(1500, Math.max(0, Number(response?.pause_after_ms) || 0));
        audioChunkPauseDurations[index] = response.pause_after_ms / 1000;
        preloadAudioResponse(index, response, speed);
        preloadBreathResponse(index, response);
        onUpdate({
          ...playbackRangeUpdate(),
          provider: response.effective_provider || response.provider || provider,
          voice: response.effective_voice || voice || (premium ? "Serena" : "af_heart"),
          lastProviderUsed: response.effective_provider || response.provider || provider,
          lastVoiceUsed: response.effective_voice || voice || (premium ? "Serena" : "af_heart"),
          lastModelUsed: response.effective_model || null,
          lastGeneratedAudioFile: response.audio_url || null,
        });
        return { response };
      })
      .catch((error) => ({ error }));
    synthPromises.set(index, promise);
    return promise;
  };
  const startAhead = (index) => {
    if (!progressive) return;
    if (premium) return;
    for (let offset = 1; offset <= prebufferChunks; offset += 1) {
      startSynthesis(index + offset);
    }
  };

  if (premium) {
    premiumProducerPromise = (async () => {
      for (let batchStart = premiumBatchStart(startIndex); batchStart < chunks.length; batchStart += 4) {
        if (runId !== playbackId || stopRequested) return;
        await startPremiumBatch(batchStart);
      }
    })().catch((error) => {
      reportTtsResilience("Qwen ordered batch producer failed", {
        error_message: error?.message || String(error),
        narration_job_id: jobId,
      });
    });
  }

  onUpdate({
    isNarrating: true,
    isPaused: false,
    elapsedTime: elapsed,
    ...playbackRangeUpdate(),
    provider,
    voice: voice || (premium ? "Serena" : "af_heart"),
    statusMessage: startIndex > 0 || startAtSeconds > 0 ? "Resuming narration..." : premium ? "Preparing Qwen3-TTS 0.6B" : "Preparing Kokoro",
    narrationCursor: cursorForAudioChunk(startIndex, startAtSeconds, {
      performancePhase: startPhase === "start" ? "speech" : startPhase,
      breathElapsed: startBreathSeconds,
    }),
  });

  let index = startIndex;
  while (index < chunks.length) {
    if (runId !== playbackId || stopRequested) return;
    const synthPromise = startSynthesis(index);
    startAhead(index);
    onUpdate({
      isNarrating: true,
      isPaused: false,
      provider,
      voice: voice || (premium ? "Serena" : "af_heart"),
      ...playbackRangeUpdate(),
      statusMessage: index === 0 ? (premium ? "Preparing Qwen3-TTS 0.6B" : "Preparing Kokoro") : "Preloading next section",
      narrationCursor: cursorForAudioChunk(index, 0),
    });
    if (index > 0 && !audioPreloads.has(index)) {
      onUpdate({ statusMessage: "Buffering..." });
    }
    const result = await synthPromise;
    if (runId !== playbackId || stopRequested) return;
    if (result.error) throw result.error;
    const response = result.response;
    if (!response) return;
    if (index === 0) {
      onUpdate({ statusMessage: "First audio ready" });
    }
    startAhead(index);
    if (premium && index === 0 && chunks.length > 4) {
      if (!premiumBufferCoversNextBatch(audioChunkResponses.slice(0, 4))) {
        onUpdate({ isBuffering: true, statusMessage: "Buffering Qwen3-TTS 0.6B" });
        const nextBatch = await startSynthesis(4);
        if (nextBatch?.error) throw nextBatch.error;
      }
    }
    if (index === chunks.length - 1) {
      onUpdate({ statusMessage: "Full narration cached" });
    }
    if (response.cached) {
      onUpdate({ statusMessage: index === 0 ? "Cached" : "Preloading next section" });
    }
    let played = false;
    let playableResponse = response;
    const seekAtPlaybackStart = requestedAudioSeek?.chunkIndex === index ? requestedAudioSeek : null;
    if (seekAtPlaybackStart) requestedAudioSeek = null;
    let phase = seekAtPlaybackStart?.phase || (index === startIndex ? startPhase : "start");
    let breathOffset = seekAtPlaybackStart?.breathTime ?? (index === startIndex ? startBreathSeconds : 0);
    if (phase === "start" || phase === "breath_before") {
      const breathCompleted = await playBreathResponse(
        playableResponse,
        "breath_before",
        runId,
        index,
        phase === "breath_before" ? breathOffset : 0,
      );
      if (!breathCompleted) {
        if (requestedAudioSeek) {
          const seek = requestedAudioSeek;
          index = seek.chunkIndex;
          startAtSeconds = seek.chunkTime;
          startPhase = seek.phase;
          startBreathSeconds = seek.breathTime || 0;
          continue;
        }
        return;
      }
      phase = "speech";
      breathOffset = 0;
      persistNarrationCursor("before_breath_complete", {
        force: true,
        cursorOverride: cursorForAudioChunk(index, 0, { performancePhase: "speech" }),
      });
    }

    if (phase !== "breath_after" && phase !== "boundary_complete") {
      for (let playAttempt = 0; playAttempt < 2; playAttempt += 1) {
        try {
          played = await playAudioResponse({
            response: playableResponse,
            runId,
            speed,
            voice,
            statusMessage: "Narrating",
            chunkIndex: index,
            chunkCount: chunks.length,
            startAtSeconds: seekAtPlaybackStart?.chunkTime ?? (index === startIndex ? startAtSeconds : 0),
            autoplay: seekAtPlaybackStart?.autoplay ?? true,
          });
          break;
        } catch (error) {
          persistNarrationCursor("audio_playback_error", { force: true });
          reportTtsResilience("Chunk playback failed", {
            chunk_index: index,
            chunk_count: chunks.length,
            retry_attempt: playAttempt,
            error_message: error?.message || String(error),
            cached_chunk_existed: Boolean(playableResponse?.cached),
          });
          if (playAttempt >= 1 || runId !== playbackId || stopRequested) throw error;
          synthPromises.delete(index);
          onUpdate({ isBuffering: true, statusMessage: "Retrying cached narration..." });
          const retryResult = await startSynthesis(index);
          const resolved = await retryResult;
          if (resolved?.error) throw resolved.error;
          playableResponse = resolved?.response;
          if (!playableResponse) return;
        }
      }
      if (!played || runId !== playbackId || stopRequested) return;
    }
    if (requestedAudioSeek) {
      const seek = requestedAudioSeek;
      index = seek.chunkIndex;
      startAtSeconds = seek.chunkTime;
      startPhase = seek.phase;
      startBreathSeconds = seek.breathTime || 0;
      continue;
    }
    if (phase !== "boundary_complete") {
      const afterCompleted = await playBreathResponse(
        playableResponse,
        "breath_after",
        runId,
        index,
        phase === "breath_after" ? breathOffset : 0,
      );
      if (!afterCompleted) {
        if (requestedAudioSeek) {
          const seek = requestedAudioSeek;
          index = seek.chunkIndex;
          startAtSeconds = seek.chunkTime;
          startPhase = seek.phase;
          startBreathSeconds = seek.breathTime || 0;
          continue;
        }
        return;
      }
      if (index < chunks.length - 1) {
        boundaryCursorOverride = cursorForAudioChunk(index + 1, 0, { performancePhase: "start" });
      }
      if (shouldScheduleBreath(playableResponse, "breath_after", index) && boundaryCursorOverride) {
        persistNarrationCursor("after_breath_complete", {
          force: true,
          cursorOverride: boundaryCursorOverride,
        });
      }
    }
    const pauseCompleted = await waitForNarrationPause(playableResponse.pause_after_ms, runId, index);
    if (!pauseCompleted || runId !== playbackId || stopRequested) return;
    boundaryCursorOverride = null;
    if (requestedAudioSeek) {
      const seek = requestedAudioSeek;
      index = seek.chunkIndex;
      startAtSeconds = seek.chunkTime;
      startPhase = seek.phase;
      startBreathSeconds = seek.breathTime || 0;
      continue;
    }
    startAtSeconds = 0;
    startBreathSeconds = 0;
    startPhase = "start";
    index += 1;
  }
  await premiumProducerPromise;
  clearTimer();
  elapsed = 0;
  clearKokoroHealthWatchdog();
  clearNarrationCursor();
  onUpdate({ isNarrating: false, isPaused: false, elapsedTime: 0, duration: null, narrationCursor: null, statusMessage: "Ready" });
}

async function playKokoro({ text, provider = "kokoro", speed, voice, kokoroOptions = {}, chunkMode = "off", chunkSize = 1200, metadata = {} }) {
  stopBrowserSpeech();
  stopAudio();
  resetAudioPlan();
  elapsed = 0;
  audioChunks = narrationChunks(text);
  const runId = playbackId;
  if (provider === "kokoro") startKokoroHealthWatchdog(runId);
  if (provider === "high_quality_local") {
    await playKokoroChunked({
      text,
      provider,
      speed,
      voice: voice || "Serena",
      kokoroOptions,
      chunkSize,
      metadata,
      runId,
      progressive: true,
    });
    return;
  }
  const effectiveMode = effectiveKokoroChunkMode(text, chunkMode);
  if (effectiveMode === "first_chunk_fast" || effectiveMode === "progressive_chunks") {
    await playKokoroChunked({
      text,
      provider,
      speed,
      voice,
      kokoroOptions,
      chunkSize,
      metadata,
      runId,
      progressive: effectiveMode === "progressive_chunks",
    });
    return;
  }
  await playKokoroFull({ text, speed, voice, kokoroOptions, metadata, runId });
}

export const ttsController = {
  SPEED_OPTIONS,

  configure(callbacks = {}) {
    onUpdate = callbacks.onUpdate || onUpdate;
    onError = callbacks.onError || onError;
    ensureCursorLifecycleListeners();
  },

  async play({
    text,
    provider,
    speed,
    voice,
    kokoroOptions = {},
    chunkMode = "off",
    chunkSize = 1200,
    prebufferChunks = 2,
    followMode = "off",
    voiceProfileId = null,
    chunkingProfile = "natural",
    narrationPacing = "natural",
    dialoguePauseStrength = "medium",
    paragraphPauseStrength = "medium",
    dialogueNarrationStyle = "neutral",
    breathingMode = "natural",
    pronunciationDictionaryVersion = null,
    normalizationVersion = null,
    sessionId = null,
    sceneId = null,
    versionId = null,
    resumeCursor = null,
  }) {
    this.stop({ reason: "replace", preserveCursor: true });
    playbackId += 1;
    stopRequested = false;
    if (!text?.trim()) {
      throw new Error("Select a scene to narrate.");
    }
    const playbackProvider = provider;
    if (breathingMode !== "off") ttsBreathScheduler.prime().catch(() => {});
    const textHash = lightweightTextHash(text);
    const activeJobId = narrationJobId();
    activeCursorContext = {
      sessionId,
      sceneId,
      versionId,
      voice: voice || "default",
      voiceProfileId,
      speed: Number(speed) || 1,
      provider: playbackProvider,
      breathingMode,
      textHash,
      narrationJobId: activeJobId,
    };
    let savedCursor = resumeCursor || loadNarrationCursor(activeCursorContext);
    if (!savedCursor) {
      const legacyHash = legacyLightweightTextHash(text);
      if (legacyHash !== textHash) {
        savedCursor = loadNarrationCursor({ ...activeCursorContext, textHash: legacyHash });
      }
    }
    debugTts("cursor-resolved", {
      hasSavedCursor: Boolean(savedCursor),
      chunkIndex: savedCursor?.chunkIndex ?? null,
      chunkCurrentTime: savedCursor?.chunkCurrentTime ?? null,
      globalNarrationTime: savedCursor?.globalNarrationTime ?? null,
    });
    if (playbackProvider === "kokoro" || playbackProvider === "high_quality_local") {
      await playKokoro({
        text,
        provider: playbackProvider,
        speed,
        voice,
        kokoroOptions,
        chunkMode,
        chunkSize,
        metadata: {
          session_id: sessionId,
          scene_id: sceneId,
          version_id: versionId,
          narration_job_id: activeJobId,
          text_hash: textHash,
          prebuffer_chunks: prebufferChunks,
          follow_mode: normalizeTtsFollowMode(followMode, "off"),
          voice_profile_id: voiceProfileId,
          tts_chunking_profile: chunkingProfile,
          narration_pacing: narrationPacing,
          dialogue_pause_strength: dialoguePauseStrength,
          paragraph_pause_strength: paragraphPauseStrength,
          dialogue_narration_style: dialogueNarrationStyle,
          breathing_mode: breathingMode,
          pronunciation_dictionary_version: pronunciationDictionaryVersion,
          normalization_version: normalizationVersion,
          resume_cursor: savedCursor,
        },
      });
      return;
    }
    playBrowser({ text, speed, voice });
  },

  pause() {
    if (ttsBreathScheduler.snapshot()) {
      intentionallyPaused = true;
      playbackShouldBeActive = false;
      awaitingUserResume = false;
      ttsBreathScheduler.pause();
      persistNarrationCursor("pause_breath", { force: true });
      onUpdate({ isNarrating: true, isPaused: true, needsUserResume: false, statusMessage: "Paused", narrationCursor: currentNarrationCursor() });
      return;
    }
    if (audio) {
      intentionallyPaused = true;
      playbackShouldBeActive = false;
      awaitingUserResume = false;
      clearPlaybackWatchdog();
      audio.pause();
      onUpdate({ isNarrating: true, isPaused: true, needsUserResume: false, statusMessage: "Paused", narrationCursor: currentNarrationCursor() });
      return;
    }
    if (window.speechSynthesis?.speaking && !window.speechSynthesis.paused) {
      window.speechSynthesis.pause();
      clearTimer();
      elapsed = Math.max(0, (Date.now() - startedAt) / 1000);
      onUpdate({ isPaused: true, elapsedTime: elapsed });
    }
  },

  async resume() {
    const breath = ttsBreathScheduler.snapshot();
    if (breath?.paused) {
      intentionallyPaused = false;
      playbackShouldBeActive = true;
      awaitingUserResume = false;
      const resumed = await ttsBreathScheduler.resume();
      if (resumed) onUpdate({ isNarrating: true, isPaused: false, needsUserResume: false, statusMessage: "Narrating" });
      return resumed;
    }
    if (scheduledNarrationPause) {
      intentionallyPaused = false;
      playbackShouldBeActive = true;
      awaitingUserResume = false;
      onUpdate({ isNarrating: true, isPaused: false, needsUserResume: false, statusMessage: "Narrating" });
      return true;
    }
    if (audio) {
      const resumed = await requestAudioPlay({ runId: playbackId, reason: "user-resume", statusMessage: "Narrating" });
      if (resumed) persistNarrationCursor("resume", { force: true });
      return resumed;
    }
    if (window.speechSynthesis?.paused) {
      window.speechSynthesis.resume();
      startTimer();
      onUpdate({ isNarrating: true, isPaused: false });
      return true;
    }
    return false;
  },

  stop({ reason = "stop", preserveCursor = false } = {}) {
    const stoppingProvider = activeCursorContext?.provider;
    const stoppingJobId = activeCursorContext?.narrationJobId;
    if (preserveCursor) {
      persistNarrationCursor(reason, { force: true });
    } else if (activeCursorContext) {
      persistNarrationCursor(reason, { force: true });
    }
    stopRequested = true;
    playbackId += 1;
    clearTimer();
    stopBrowserSpeech();
    stopAudio();
    elapsed = 0;
    browserChunks = [];
    browserChunkIndex = 0;
    audioChunks = [];
    audioChunkIndex = null;
    resetAudioPlan();
    if (stoppingProvider === "high_quality_local") {
      api.cancelTTS({ provider: stoppingProvider, narration_job_id: stoppingJobId }).catch(() => {});
      api.unloadTTS({ provider: stoppingProvider }).catch(() => {});
    }
    if (!preserveCursor && reason === "completed") {
      clearNarrationCursor();
    }
    onUpdate({ isNarrating: false, isPaused: false, elapsedTime: 0, duration: null, narrationCursor: null, statusMessage: "Ready" });
    window.setTimeout(() => {
      suppressBrowserError = false;
    }, 0);
  },

  getSavedCursor({ text, provider, speed, voice, voiceProfileId = null, breathingMode = "natural", sessionId = null, sceneId = null, versionId = null }) {
    const context = {
      sessionId,
      sceneId,
      versionId,
      voice: voice || "default",
      voiceProfileId,
      speed: Number(speed) || 1,
      provider,
      breathingMode,
      textHash: lightweightTextHash(text || ""),
    };
    let savedCursor = loadNarrationCursor(context);
    if (!savedCursor) {
      const legacyHash = legacyLightweightTextHash(text || "");
      if (legacyHash !== context.textHash) {
        savedCursor = loadNarrationCursor({ ...context, textHash: legacyHash });
      }
    }
    if (ttsDebugEnabled()) {
      let availableKeys = [];
      try {
        availableKeys = Object.keys(window.localStorage || {}).filter(
          (key) => key.startsWith(`${CURSOR_STORAGE_PREFIX}:${sessionId}:`),
        );
      } catch {
        // Diagnostics remain optional when storage is unavailable.
      }
      debugTts("cursor-lookup", {
        expectedKey: cursorStorageKey(context),
        availableKeys,
        found: Boolean(savedCursor),
      });
    }
    return savedCursor;
  },

  skip(seconds) {
    const delta = Number(seconds) || 0;
    if (audioChunks.length) {
      this.seek(elapsed + delta);
      return;
    }
    if (browserChunks.length && window.speechSynthesis?.speaking) {
      const runId = playbackId;
      const direction = delta < 0 ? -1 : 1;
      browserChunkIndex = Math.min(
        browserChunks.length - 1,
        Math.max(0, browserChunkIndex + direction),
      );
      elapsed = Math.max(0, elapsed + delta);
      suppressBrowserError = true;
      window.speechSynthesis.cancel();
      window.setTimeout(() => {
        suppressBrowserError = false;
        if (runId === playbackId && !stopRequested) {
          speakBrowserChunk(runId);
        }
      }, 30);
      onUpdate({
        isNarrating: true,
        isPaused: false,
        elapsedTime: elapsed,
        narrationCursor: { chunkIndex: browserChunkIndex, text: browserChunks[browserChunkIndex] },
        statusMessage: "Narrating...",
      });
    }
  },

  seek(seconds) {
    if (!audioChunks.length) return false;
    const resolved = resolvePerformanceSeek(
      seconds,
      audioChunks.map((_chunk, index) => chunkDuration(index)),
      audioChunkBreathBeforeDurations,
      audioChunkBreathAfterDurations,
      audioChunkPauseDurations,
      audioChunkResponses.map((response) => Boolean(response?.audio_url)),
    );
    if (!resolved) return false;
    const {
      target,
      chunkIndex: targetIndex,
      phase,
      chunkTime: targetChunkTime,
      breathTime,
    } = resolved;
    const beforeDuration = chunkBreathBeforeDuration(targetIndex);
    const afterDuration = chunkBreathAfterDuration(targetIndex);
    const autoplay = playbackShouldBeActive && !intentionallyPaused && !awaitingUserResume;

    if (phase === "speech" && audio && audioChunkIndex === targetIndex) {
      const maximum = Number.isFinite(audio.duration) ? Math.max(0, audio.duration - 0.05) : targetChunkTime;
      audio.currentTime = Math.min(targetChunkTime, maximum);
      elapsed = speechOffset(targetIndex) + (audio.currentTime || 0);
      persistNarrationCursor("seek", { force: true });
      onUpdate({ ...playbackRangeUpdate(), elapsedTime: elapsed, narrationCursor: currentNarrationCursor() });
      return true;
    }

    requestedAudioSeek = { chunkIndex: targetIndex, chunkTime: targetChunkTime, breathTime, phase, autoplay };
    elapsed = target;
    const targetCursor = cursorForAudioChunk(targetIndex, targetChunkTime, {
      performancePhase: phase,
      breathElapsed: breathTime,
      breathDuration: phase === "breath_before" ? beforeDuration : phase === "breath_after" ? afterDuration : 0,
      breathEvent: audioChunkResponses[targetIndex]?.[phase] || null,
    });
    persistNarrationCursor("seek_chunk", { force: true, cursorOverride: targetCursor });
    ttsBreathScheduler.cancel();
    if (audio) {
      clearAudioHandlers(audio);
      audio.pause();
      resolveActiveChunk(true);
    }
    onUpdate({
      ...playbackRangeUpdate(),
      elapsedTime: elapsed,
      isBuffering: true,
      statusMessage: "Seeking...",
      narrationCursor: targetCursor,
    });
    return true;
  },

  setSpeed(speed) {
    const nextSpeed = Number(speed) || 1;
    if (audio) audio.playbackRate = nextSpeed;
    if (utterance && window.speechSynthesis?.speaking) {
      onError("Browser TTS speed changes apply on the next narration restart.");
    }
  },

  browserVoices() {
    return localBrowserVoices(window.speechSynthesis);
  },
};
