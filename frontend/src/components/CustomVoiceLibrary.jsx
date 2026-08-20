import { Mic2, Play, RefreshCw, Trash2, Upload } from "lucide-react";
import { useMemo, useState } from "react";

import { API_BASE_URL, api } from "../api.js";

const STYLES = [
  { id: "normal", label: "Normal reference", required: true },
  { id: "soft", label: "Soft reference" },
  { id: "whisper", label: "Whisper reference" },
  { id: "heightened", label: "Heightened reference" },
];

const BREATHS = [
  { id: "normal_inhale", label: "Neutral inhale" },
  { id: "soft_inhale", label: "Soft inhale" },
  { id: "shaky_inhale", label: "Shaky breath" },
  { id: "quiet_exhale", label: "Quiet exhale" },
  { id: "recovering_breath", label: "Recovering breath" },
  { id: "gasp", label: "Gasp" },
];

const fieldClass = "sd-field min-h-10 w-full rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-100";

function readAudio(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The reference audio could not be read."));
    reader.onload = () => resolve(String(reader.result || ""));
    reader.readAsDataURL(file);
  });
}

export default function CustomVoiceLibrary({ voices = [], onPreview, onRefresh, onUse }) {
  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [references, setReferences] = useState({});
  const [authorized, setAuthorized] = useState(false);
  const [singleSpeaker, setSingleSpeaker] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [breathDrafts, setBreathDrafts] = useState({});
  const [breathConfirmations, setBreathConfirmations] = useState({});
  const enabledVoices = useMemo(() => voices.filter((voice) => voice.enabled), [voices]);

  const updateReference = (style, patch) => {
    setReferences((current) => ({ ...current, [style]: { ...(current[style] || {}), ...patch } }));
  };

  const createVoice = async () => {
    const normal = references.normal || {};
    if (!displayName.trim() || !normal.file || !normal.transcript?.trim() || !authorized || !singleSpeaker) {
      setMessage("Name, normal reference, exact transcript, and both confirmations are required.");
      return;
    }
    setBusy("create");
    setMessage("Normalizing references and building the local Qwen voice prompt...");
    try {
      const payloadReferences = {};
      for (const style of STYLES) {
        const reference = references[style.id];
        if (!reference?.file) continue;
        payloadReferences[style.id] = {
          filename: reference.file.name,
          audio_base64: await readAudio(reference.file),
          transcript: String(reference.transcript || "").trim(),
        };
      }
      await api.createCustomVoice({
        display_name: displayName.trim(),
        language: "English",
        authorization_confirmed: authorized,
        dominant_speaker_confirmed: singleSpeaker,
        references: payloadReferences,
        build_prompt: true,
      });
      setDisplayName("");
      setReferences({});
      setAuthorized(false);
      setSingleSpeaker(false);
      setOpen(false);
      setMessage("Custom voice is ready.");
      await onRefresh?.();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  };

  const runAction = async (key, action) => {
    setBusy(key);
    setMessage("");
    try {
      await action();
      await onRefresh?.();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  };

  const updateBreathDraft = (voiceId, breathType, file) => {
    setBreathDrafts((current) => ({
      ...current,
      [voiceId]: { ...(current[voiceId] || {}), [breathType]: file || null },
    }));
  };

  const updateBreathConfirmation = (voiceId, key, checked) => {
    setBreathConfirmations((current) => ({
      ...current,
      [voiceId]: { ...(current[voiceId] || {}), [key]: checked },
    }));
  };

  const saveBreath = async (voice, breathType) => {
    const file = breathDrafts[voice.id]?.[breathType];
    const confirmation = breathConfirmations[voice.id] || {};
    if (!file || !confirmation.authorized || !confirmation.isolated) {
      setMessage("Choose one isolated breath and confirm authorization and sample contents.");
      return;
    }
    await runAction(`breath:${voice.id}:${breathType}`, async () => {
      await api.saveCustomBreath(voice.id, breathType, {
        filename: file.name,
        audio_base64: await readAudio(file),
        authorization_confirmed: true,
        isolated_breath_confirmed: true,
      });
      updateBreathDraft(voice.id, breathType, null);
      setMessage("Custom breath reference is ready.");
    });
  };

  const previewBreath = (voice, breath, sentenceContext = false) => {
    if (!breath?.audio_url) return;
    const element = new Audio(`${API_BASE_URL}${breath.audio_url}`);
    element.preload = "auto";
    element.playsInline = true;
    if (sentenceContext) element.onended = () => onPreview?.(voice);
    element.play().catch((error) => setMessage(error.message));
  };

  return (
    <div className="border-t border-line pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Mic2 className="text-tide" size={15} />
            Custom voice library
          </div>
          <p className="mt-1 text-xs text-muted">Authorized local references only. WAV, FLAC, or MP3.</p>
        </div>
        <button
          className="sd-action-button inline-flex min-h-9 items-center gap-2 rounded-lg border border-line px-3 text-xs font-medium text-zinc-200"
          onClick={() => setOpen((value) => !value)}
          type="button"
        >
          <Upload size={14} />
          {open ? "Close" : "Add voice"}
        </button>
      </div>

      {open ? (
        <div className="mt-3 grid gap-3 rounded-lg border border-line bg-[#101216]/70 p-3">
          <label className="grid gap-1 text-xs text-muted">
            Voice name
            <input className={fieldClass} maxLength={120} onChange={(event) => setDisplayName(event.target.value)} value={displayName} />
          </label>
          {STYLES.map((style) => (
            <div className="grid gap-2 border-t border-line pt-3 first:border-t-0 first:pt-0" key={style.id}>
              <div className="text-xs font-medium text-zinc-300">{style.label}{style.required ? " *" : ""}</div>
              <input
                accept="audio/wav,audio/x-wav,audio/flac,audio/mpeg,audio/mp3,.wav,.flac,.mp3"
                className="block w-full text-xs text-muted file:mr-3 file:rounded-md file:border-0 file:bg-tide/15 file:px-3 file:py-2 file:text-tide"
                onChange={(event) => updateReference(style.id, { file: event.target.files?.[0] || null })}
                type="file"
              />
              <textarea
                className={`${fieldClass} min-h-16 py-2`}
                onChange={(event) => updateReference(style.id, { transcript: event.target.value })}
                placeholder="Exact words spoken in this reference"
                value={references[style.id]?.transcript || ""}
              />
            </div>
          ))}
          <label className="flex items-start gap-2 text-xs leading-5 text-zinc-300">
            <input checked={authorized} className="mt-1" onChange={(event) => setAuthorized(event.target.checked)} type="checkbox" />
            I own these recordings or have explicit permission to use this voice.
          </label>
          <label className="flex items-start gap-2 text-xs leading-5 text-zinc-300">
            <input checked={singleSpeaker} className="mt-1" onChange={(event) => setSingleSpeaker(event.target.checked)} type="checkbox" />
            Each reference contains one dominant consenting adult speaker.
          </label>
          <button
            className="sd-button-primary inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 disabled:opacity-50"
            disabled={Boolean(busy)}
            onClick={createVoice}
            type="button"
          >
            {busy === "create" ? <RefreshCw className="animate-spin" size={15} /> : <Upload size={15} />}
            Process voice locally
          </button>
        </div>
      ) : null}

      <div className="mt-3 grid gap-2">
        {voices.map((voice) => (
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-t border-line py-3 first:border-t-0" key={voice.id}>
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-zinc-200">{voice.display_name}</div>
              <div className="mt-0.5 text-xs text-muted">
                {voice.validation_status === "ready" ? `Ready - revision ${voice.revision}` : voice.validation_status.replaceAll("_", " ")}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button className="sd-icon-button" onClick={() => onPreview?.(voice)} title="Preview voice" type="button"><Play size={14} /></button>
              <button className="sd-action-button min-h-8 rounded-md border border-line px-2 text-xs text-zinc-200" onClick={() => onUse?.(voice)} type="button">Use</button>
              <button
                className="sd-action-button min-h-8 rounded-md border border-line px-2 text-xs text-zinc-200"
                disabled={Boolean(busy)}
                onClick={() => runAction(`toggle:${voice.id}`, () => api.updateCustomVoice(voice.id, { enabled: !voice.enabled }))}
                type="button"
              >
                {voice.enabled ? "Disable" : "Enable"}
              </button>
              {voice.validation_status !== "ready" ? (
                <button className="sd-icon-button" disabled={Boolean(busy)} onClick={() => runAction(`build:${voice.id}`, () => api.buildCustomVoice(voice.id))} title="Rebuild voice prompt" type="button"><RefreshCw className={busy === `build:${voice.id}` ? "animate-spin" : ""} size={14} /></button>
              ) : null}
              <button
                className="sd-icon-button text-ember"
                disabled={Boolean(busy)}
                onClick={() => {
                  if (window.confirm(`Delete ${voice.display_name}'s local references and cached prompt? Generated narration stays cached.`)) {
                    runAction(`delete:${voice.id}`, () => api.deleteCustomVoice(voice.id, false));
                  }
                }}
                title="Delete voice references and prompt"
                type="button"
              >
                <Trash2 size={14} />
              </button>
            </div>
            <details className="col-span-2 border-t border-line/70 pt-2">
              <summary className="cursor-pointer text-xs font-medium text-zinc-300">Custom breath references</summary>
              <div className="mt-3 grid gap-3">
                <p className="text-[11px] leading-5 text-muted">
                  Optional 0.4-1.5 second isolated samples from the same authorized speaker and microphone. No speech, music, or reverb.
                </p>
                <div className="grid gap-2 min-[560px]:grid-cols-2">
                  {BREATHS.map((breath) => {
                    const saved = voice.breaths?.[breath.id];
                    const actionKey = `breath:${voice.id}:${breath.id}`;
                    return (
                      <div className="grid gap-2 border-t border-line/60 py-2" key={breath.id}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-medium text-zinc-300">{breath.label}</span>
                          <span className="text-[11px] text-muted">{saved ? `${saved.duration_seconds.toFixed(2)}s - r${saved.revision}` : "Not set"}</span>
                        </div>
                        <input
                          accept="audio/wav,audio/x-wav,audio/flac,audio/mpeg,audio/mp3,.wav,.flac,.mp3"
                          className="block w-full text-[11px] text-muted file:mr-2 file:rounded-md file:border-0 file:bg-tide/15 file:px-2 file:py-1.5 file:text-tide"
                          onChange={(event) => updateBreathDraft(voice.id, breath.id, event.target.files?.[0] || null)}
                          type="file"
                        />
                        <div className="flex flex-wrap items-center gap-1.5">
                          <button
                            className="sd-action-button min-h-8 rounded-md border border-line px-2 text-xs text-zinc-200 disabled:opacity-50"
                            disabled={Boolean(busy) || !breathDrafts[voice.id]?.[breath.id]}
                            onClick={() => saveBreath(voice, breath.id)}
                            type="button"
                          >
                            {busy === actionKey ? "Processing..." : saved ? "Replace" : "Add"}
                          </button>
                          {saved ? (
                            <>
                              <button className="sd-icon-button" onClick={() => previewBreath(voice, saved)} title={`Preview ${breath.label}`} type="button"><Play size={14} /></button>
                              <button className="sd-action-button min-h-8 rounded-md border border-line px-2 text-xs text-zinc-200" onClick={() => previewBreath(voice, saved, true)} type="button">Test in context</button>
                              <button
                                className="sd-icon-button text-ember"
                                disabled={Boolean(busy)}
                                onClick={() => runAction(`remove-breath:${voice.id}:${breath.id}`, () => api.deleteCustomBreath(voice.id, breath.id))}
                                title={`Remove ${breath.label}`}
                                type="button"
                              ><Trash2 size={14} /></button>
                            </>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <label className="flex items-start gap-2 text-xs leading-5 text-zinc-300">
                  <input checked={Boolean(breathConfirmations[voice.id]?.authorized)} className="mt-1" onChange={(event) => updateBreathConfirmation(voice.id, "authorized", event.target.checked)} type="checkbox" />
                  I own these recordings or have explicit permission to use this voice.
                </label>
                <label className="flex items-start gap-2 text-xs leading-5 text-zinc-300">
                  <input checked={Boolean(breathConfirmations[voice.id]?.isolated)} className="mt-1" onChange={(event) => updateBreathConfirmation(voice.id, "isolated", event.target.checked)} type="checkbox" />
                  Each file is one isolated breath with no speech, music, or reverb.
                </label>
              </div>
            </details>
          </div>
        ))}
        {!voices.length ? <p className="text-xs text-muted">No custom voices have been added.</p> : null}
      </div>
      {message ? <p className={`mt-2 text-xs ${message.includes("ready") ? "text-moss" : "text-ember"}`}>{message}</p> : null}
      {enabledVoices.length ? <p className="mt-2 text-[11px] text-muted">{enabledVoices.length} local custom {enabledVoices.length === 1 ? "voice" : "voices"} available.</p> : null}
    </div>
  );
}
