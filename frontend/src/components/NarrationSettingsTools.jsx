import { Check, Clipboard, RefreshCw, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, api } from "../api.js";

const CURSOR_PREFIX = "storydriver_tts_cursor:";
const RATINGS_KEY = "storydriver_tts_comparison_ratings_v1";

const PASSAGES = {
  neutral: {
    label: "Neutral narration",
    text: "Morning light crossed the kitchen table as Lena checked the train times, folded the map, and listened for the kettle. Outside, the first buses moved through the rain.",
  },
  emotional: {
    label: "Quiet emotion",
    text: "Mara kept one hand on the unopened letter. She had imagined this moment for years, but now the room felt too still, and the words she had prepared would not come.",
  },
  dialogue: {
    label: "Dialogue",
    text: "You knew before we left, didn't you? Nia asked. Rowan set the brass key beside the ledger. I suspected, he said, but suspicion is not the same as proof.",
  },
  names: {
    label: "Names and numbers",
    text: "Dr. Aisling Zhao reached Platform 7 at 6:42 p.m. Her message mentioned Aeron, Qhapaq Nan, and coordinates 39.7392 north, 104.9903 west.",
  },
};

const RATING_FIELDS = [
  ["realism", "Realism"],
  ["pleasantness", "Pleasantness"],
  ["femaleVoice", "Female voice"],
  ["emotion", "Emotion"],
  ["dialogue", "Dialogue"],
  ["pronunciation", "Pronunciation"],
  ["comfort", "Long-listening comfort"],
  ["artifacts", "Artifact-free"],
  ["preference", "Overall preference"],
];

function absoluteAudioUrl(value) {
  if (!value) return "";
  try {
    return new URL(value, `${API_BASE_URL}/`).toString();
  } catch {
    return value;
  }
}

function shuffled(items) {
  const result = [...items];
  for (let index = result.length - 1; index > 0; index -= 1) {
    const random = new Uint32Array(1);
    window.crypto?.getRandomValues?.(random);
    const swapIndex = window.crypto?.getRandomValues ? random[0] % (index + 1) : Math.floor(Math.random() * (index + 1));
    [result[index], result[swapIndex]] = [result[swapIndex], result[index]];
  }
  return result;
}

function compactProfile(profile) {
  return {
    id: profile.id,
    displayName: profile.display_name || profile.name || profile.id,
    provider: profile.provider,
    model: profile.model || (profile.provider === "kokoro" ? "Kokoro" : "Local TTS"),
    voice: profile.voice_id || null,
  };
}

function readSavedCursor() {
  if (typeof window === "undefined") return null;
  const records = [];
  try {
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith(CURSOR_PREFIX)) continue;
      const value = JSON.parse(window.localStorage.getItem(key) || "null");
      if (value) records.push(value);
    }
  } catch {
    return null;
  }
  records.sort((left, right) => Number(right.updatedAtMs || 0) - Number(left.updatedAtMs || 0));
  const latest = records[0];
  return latest
    ? {
        count: records.length,
        updatedAt: latest.updatedAt || null,
        reason: latest.reason || null,
        chunkIndex: Number.isFinite(Number(latest.chunkIndex)) ? Number(latest.chunkIndex) : null,
        chunkCount: Number.isFinite(Number(latest.chunkCount)) ? Number(latest.chunkCount) : null,
        globalSeconds: Number.isFinite(Number(latest.globalNarrationTime)) ? Number(latest.globalNarrationTime) : null,
      }
    : { count: 0 };
}

function copyText(value) {
  const legacyCopy = () => {
    const textArea = document.createElement("textarea");
    textArea.value = value;
    textArea.style.position = "fixed";
    textArea.style.opacity = "0";
    document.body.appendChild(textArea);
    textArea.select();
    const copied = document.execCommand("copy");
    textArea.remove();
    if (!copied) throw new Error("Clipboard access is unavailable in this browser.");
  };
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    return navigator.clipboard.writeText(value).catch(() => legacyCopy());
  }
  legacyCopy();
  return Promise.resolve();
}

export function VoiceComparisonLab({ profiles = [], saveTTSSettings }) {
  const candidates = useMemo(
    () => profiles
      .filter((profile) => ["natural_female_narrator", "premium_female_narrator"].includes(profile.id))
      .filter((profile) => profile.available !== false && profile.enabled !== false)
      .map(compactProfile),
    [profiles],
  );
  const [passageId, setPassageId] = useState("neutral");
  const [selected, setSelected] = useState(() => candidates.map((profile) => profile.id));
  const [samples, setSamples] = useState([]);
  const [ratings, setRatings] = useState({});
  const [revealed, setRevealed] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState("");
  const audioRefs = useRef({});

  useEffect(() => {
    setSelected((current) => {
      const valid = current.filter((id) => candidates.some((profile) => profile.id === id));
      return valid.length >= 2 ? valid : candidates.map((profile) => profile.id);
    });
  }, [candidates]);

  const toggleProfile = (profileId) => {
    setSelected((current) => current.includes(profileId)
      ? current.filter((id) => id !== profileId)
      : [...current, profileId]);
  };

  const generate = async () => {
    const chosen = candidates.filter((profile) => selected.includes(profile.id));
    if (chosen.length < 2) {
      setError("Choose at least two available profiles.");
      return;
    }
    setIsGenerating(true);
    setError("");
    setSamples([]);
    setRatings({});
    setRevealed(false);
    try {
      const generated = [];
      for (const profile of shuffled(chosen)) {
        const response = await api.previewTTS({
          sample_text: PASSAGES[passageId].text,
          voice_profile_id: profile.id,
          voice: profile.voice,
        });
        generated.push({
          label: `Sample ${String.fromCharCode(65 + generated.length)}`,
          profile,
          audioUrl: absoluteAudioUrl(response.audio_url),
          cached: Boolean(response.cached),
        });
      }
      setSamples(generated);
    } catch (generationError) {
      setError(generationError.message || "Voice comparison generation failed.");
    } finally {
      setIsGenerating(false);
    }
  };

  const setRating = (label, field, value) => {
    setRatings((current) => ({
      ...current,
      [label]: { ...(current[label] || {}), [field]: Number(value) || null },
    }));
  };

  const ratingsComplete = samples.length >= 2 && samples.every((sample) =>
    RATING_FIELDS.every(([field]) => Number(ratings[sample.label]?.[field]) >= 1));

  const reveal = () => {
    if (!ratingsComplete) return;
    const compactRatings = {
      passageId,
      ratedAt: new Date().toISOString(),
      samples: samples.map((sample) => ({ profileId: sample.profile.id, ratings: ratings[sample.label] })),
    };
    try {
      window.localStorage.setItem(RATINGS_KEY, JSON.stringify(compactRatings));
    } catch {
      // Ratings persistence is best effort and never contains story text.
    }
    setRevealed(true);
  };

  const useProfile = (profile) => {
    saveTTSSettings({
      tts_provider: profile.provider,
      tts_voice_profile_id: profile.id,
      tts_voice: profile.voice,
      high_quality_local_enabled: profile.provider === "high_quality_local" ? true : undefined,
      tts_quality_mode: profile.provider === "high_quality_local" ? "premium" : "balanced",
    }).catch(() => {});
  };

  return (
    <details className="rounded-lg border border-line bg-[#0d0e11]">
      <summary className="cursor-pointer px-3 py-3 text-sm font-medium text-zinc-200">Compare voices</summary>
      <div className="grid gap-3 border-t border-line p-3">
        <label>
          <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-muted">Synthetic passage</span>
          <select className="sd-field min-h-10 w-full rounded-lg border border-line bg-panel px-3 text-sm text-zinc-100" onChange={(event) => setPassageId(event.target.value)} value={passageId}>
            {Object.entries(PASSAGES).map(([id, passage]) => <option key={id} value={id}>{passage.label}</option>)}
          </select>
        </label>

        <fieldset className="grid gap-2">
          <legend className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Profiles</legend>
          {candidates.map((profile) => (
            <label className="flex min-h-10 items-center gap-2 text-sm text-zinc-300" key={profile.id}>
              <input checked={selected.includes(profile.id)} onChange={() => toggleProfile(profile.id)} type="checkbox" />
              {profile.displayName}
            </label>
          ))}
        </fieldset>

        <button className="sd-action-button inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-tide/35 bg-tide/10 px-3 text-sm font-medium text-tide disabled:opacity-50" disabled={isGenerating || selected.length < 2} onClick={generate} type="button">
          <RefreshCw className={isGenerating ? "animate-spin" : ""} size={15} />
          {isGenerating ? "Generating local samples..." : "Generate blind comparison"}
        </button>
        {error ? <p className="text-xs text-ember">{error}</p> : null}

        {samples.map((sample) => (
          <section className="grid gap-3 border-t border-line pt-3" key={sample.label}>
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-zinc-100">{sample.label}</span>
              <span className="text-xs text-muted">{sample.cached ? "Cached" : "Generated locally"}</span>
            </div>
            <div className="flex min-w-0 items-center gap-2">
              <audio className="h-10 min-w-0 flex-1" controls preload="metadata" ref={(node) => { audioRefs.current[sample.label] = node; }} src={sample.audioUrl} />
              <button aria-label={`Replay ${sample.label}`} className="sd-icon-button grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-line bg-panel text-zinc-200" onClick={() => { const player = audioRefs.current[sample.label]; if (player) { player.currentTime = 0; player.play().catch(() => {}); } }} title="Replay" type="button">
                <RotateCcw size={15} />
              </button>
            </div>
            <div className="grid gap-2 min-[500px]:grid-cols-3">
              {RATING_FIELDS.map(([field, label]) => (
                <label key={field}>
                  <span className="mb-1 block text-xs text-muted">{label}</span>
                  <select className="sd-field min-h-9 w-full rounded-lg border border-line bg-panel px-2 text-sm text-zinc-100" onChange={(event) => setRating(sample.label, field, event.target.value)} value={ratings[sample.label]?.[field] || ""}>
                    <option value="">Rate</option>
                    {[1, 2, 3, 4, 5].map((score) => <option key={score} value={score}>{score}</option>)}
                  </select>
                </label>
              ))}
            </div>
            {revealed ? (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line bg-panel px-3 py-2">
                <span className="safe-wrap text-xs text-zinc-300">{sample.profile.displayName} · {sample.profile.model} · {sample.profile.voice || "profile voice"}</span>
                <button className="sd-action-button inline-flex min-h-9 items-center gap-2 rounded-lg border border-moss/35 bg-moss/10 px-3 text-xs font-medium text-moss" onClick={() => useProfile(sample.profile)} type="button"><Check size={14} />Use profile</button>
              </div>
            ) : null}
          </section>
        ))}

        {samples.length ? (
          <button className="sd-action-button inline-flex min-h-10 items-center justify-center rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200 disabled:opacity-50" disabled={!ratingsComplete || revealed} onClick={reveal} type="button">
            {revealed ? "Profiles revealed" : "Save ratings and reveal profiles"}
          </button>
        ) : null}
      </div>
    </details>
  );
}

export function NarrationDeviceDiagnostics({ backendReachable, narration, ttsStatus }) {
  const [audioContextState, setAudioContextState] = useState("unsupported");
  const [serviceWorkerState, setServiceWorkerState] = useState("not registered");
  const [copied, setCopied] = useState(false);
  const savedCursor = useMemo(() => readSavedCursor(), [narration?.elapsedTime, narration?.currentNarrationVersionId]);

  useEffect(() => {
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextConstructor) return undefined;
    const context = new AudioContextConstructor();
    const syncState = () => setAudioContextState(context.state);
    syncState();
    context.addEventListener?.("statechange", syncState);
    return () => {
      context.removeEventListener?.("statechange", syncState);
      context.close().catch(() => {});
    };
  }, []);

  useEffect(() => {
    if (!navigator.serviceWorker) return;
    navigator.serviceWorker.getRegistration().then((registration) => {
      setServiceWorkerState(navigator.serviceWorker.controller || registration?.active ? "active" : registration ? "registered" : "not registered");
    }).catch(() => setServiceWorkerState("unavailable"));
  }, []);

  const providerHealth = {
    selected: ttsStatus?.active_provider || "unknown",
    kokoro: ttsStatus?.kokoro?.reachable ? "online" : "offline",
    premium: ttsStatus?.qwen_premium?.loaded ? "loaded" : ttsStatus?.qwen_premium?.reachable ? "reachable" : "on demand",
  };
  const generatedRange = {
    startSeconds: Number(narration?.bufferedStartTime || 0),
    endSeconds: Number(narration?.bufferedEndTime || 0),
    generatedChunks: Number(narration?.generatedChunkCount || 0),
  };
  const summary = {
    capturedAt: new Date().toISOString(),
    hostname: window.location.hostname,
    backend: backendReachable ? "online" : "offline",
    providers: providerHealth,
    audioContext: audioContextState,
    mediaSession: navigator.mediaSession ? navigator.mediaSession.playbackState || "supported" : "unsupported",
    savedCursor,
    generatedRange,
    serviceWorker: serviceWorkerState,
  };

  const copySummary = () => {
    copyText(JSON.stringify(summary, null, 2)).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    }).catch(() => setCopied(false));
  };

  return (
    <details className="rounded-lg border border-line bg-[#0d0e11]">
      <summary className="cursor-pointer px-3 py-3 text-sm font-medium text-zinc-200">Device diagnostics</summary>
      <div className="grid gap-2 border-t border-line p-3 text-xs">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">Hostname</span><span className="safe-wrap text-right text-zinc-200">{summary.hostname}</span></div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">Backend</span><span className="text-zinc-200">{summary.backend}</span></div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">Providers</span><span className="text-right text-zinc-200">{providerHealth.selected}; Kokoro {providerHealth.kokoro}; Qwen3-TTS 0.6B {providerHealth.premium}</span></div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">Audio context</span><span className="text-zinc-200">{audioContextState}</span></div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">Media session</span><span className="text-zinc-200">{summary.mediaSession}</span></div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">Saved cursor</span><span className="text-right text-zinc-200">{savedCursor?.count ? `${savedCursor.count}; chunk ${(savedCursor.chunkIndex ?? 0) + 1}/${savedCursor.chunkCount || "?"}` : "none"}</span></div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">Generated range</span><span className="text-zinc-200">{generatedRange.startSeconds.toFixed(1)}–{generatedRange.endSeconds.toFixed(1)} sec</span></div>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3"><span className="text-muted">PWA worker</span><span className="text-zinc-200">{serviceWorkerState}</span></div>
        <button className="sd-action-button mt-1 inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200" onClick={copySummary} type="button">
          {copied ? <Check size={15} /> : <Clipboard size={15} />}
          {copied ? "Copied" : "Copy diagnostic summary"}
        </button>
      </div>
    </details>
  );
}
