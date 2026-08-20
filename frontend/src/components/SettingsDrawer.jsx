import { motion } from "framer-motion";
import {
  CheckCircle2,
  Database,
  Gauge,
  Palette,
  Play,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  TriangleAlert,
  Type,
  Volume2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "../api.js";
import { DEFAULT_UI_SETTINGS } from "../services/displaySettings.js";
import { useAppStore } from "../store/useAppStore.js";
import { getUiPreset } from "../ui-presets/registry.js";
import { presetName } from "../ui-presets/utils.js";
import { NarrationDeviceDiagnostics, VoiceComparisonLab } from "./NarrationSettingsTools.jsx";
import CustomVoiceLibrary from "./CustomVoiceLibrary.jsx";
import BackgroundLibrarySettings from "./BackgroundLibrarySettings.jsx";
import { SettingLabel } from "./SettingHelp.jsx";

const EMPTY_ARRAY = Object.freeze([]);
const fieldClass =
  "sd-field min-h-10 w-full rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-100";
const labelClass = "mb-1.5 block text-xs font-medium uppercase tracking-wide text-muted";

const pronunciationEntriesToText = (entries = []) =>
  entries
    .filter((entry) => entry?.enabled !== false && entry?.written_form && entry?.spoken_form)
    .map((entry) => `${entry.written_form} => ${entry.spoken_form}`)
    .join("\n");

const parsePronunciationEntries = (value = "") =>
  String(value)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(/^(.+?)\s*(?:=>|->|=)\s*(.+)$/);
      if (!match) return null;
      const written = match[1].trim();
      const spoken = match[2].trim();
      if (!written || !spoken) return null;
      return {
        id: `${written.toLowerCase()}-${spoken.toLowerCase()}`
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "")
          .slice(0, 64),
        written_form: written,
        spoken_form: spoken,
        scope: "global",
        story_id: null,
        enabled: true,
        notes: "",
      };
    })
    .filter(Boolean);

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "Not recorded";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function Status({ ok, children, paused = false }) {
  const tone = ok || paused ? "border-moss/35 bg-moss/10 text-moss" : "border-ember/35 bg-ember/10 text-ember";
  return (
    <span className={`inline-flex min-h-7 shrink-0 items-center gap-1.5 rounded-full border px-2 text-xs ${tone}`}>
      {ok || paused ? <CheckCircle2 size={13} /> : <TriangleAlert size={13} />}
      {children}
    </span>
  );
}

function SettingSection({ children, icon: Icon, title }) {
  return (
    <section className="min-w-0 border-b border-line pb-5 last:border-b-0 last:pb-0">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-100">
        <Icon className="text-tide" size={16} />
        {title}
      </h3>
      {children}
    </section>
  );
}

function ServiceRow({ endpoint, name, ok, paused = false, statusLabel = null }) {
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-t border-line py-3 first:border-t-0 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <div className="text-sm font-medium text-zinc-200">{name}</div>
        <div className="safe-wrap mt-0.5 text-xs text-muted">{endpoint || "Not configured"}</div>
      </div>
      <Status ok={ok} paused={paused}>{statusLabel || (paused ? "Paused" : ok ? "Online" : "Offline")}</Status>
    </div>
  );
}

const SETTINGS_CATEGORIES = [
  ["general", "General"],
  ["writing", "Writing"],
  ["models", "Models"],
  ["narration", "Narration"],
  ["memory", "Memory & Continuity"],
  ["appearance", "Appearance"],
  ["privacy", "LAN & Privacy"],
  ["diagnostics", "Diagnostics"],
  ["about", "About"],
];

export default function SettingsDrawer({ onClose, onOpenModels }) {
  const [activeCategory, setActiveCategory] = useState("general");
  const [settingsSearch, setSettingsSearch] = useState("");
  const {
    availableUiPresets,
    browserVoices,
    diagnostics,
    fetchCustomUiPresets,
    fetchDiagnostics,
    fetchKokoroVoices,
    fetchStorageMaintenance,
    fetchTTSStatus,
    isLoadingStorageMaintenance,
    kokoroVoices,
    loadBrowserVoices,
    narration,
    previewTTSVoice,
    saveTTSSettings,
    saveUiSettings,
    setUiPreset,
    storageMaintenance,
    testNarration,
    ttsSettings,
    ttsStatus,
    ttsVoicePreview,
    uiPresetId,
    uiSettings,
  } = useAppStore();

  useEffect(() => {
    loadBrowserVoices();
    fetchCustomUiPresets();
    fetchKokoroVoices();
    fetchTTSStatus();
    fetchDiagnostics();
    fetchStorageMaintenance();
    if (!window.speechSynthesis) return undefined;
    window.speechSynthesis.addEventListener?.("voiceschanged", loadBrowserVoices);
    return () => window.speechSynthesis.removeEventListener?.("voiceschanged", loadBrowserVoices);
  }, [fetchCustomUiPresets, fetchDiagnostics, fetchKokoroVoices, fetchStorageMaintenance, fetchTTSStatus, loadBrowserVoices]);

  const updateTTS = (patch) => {
    saveTTSSettings(patch).catch(() => {});
  };

  const updateUI = (patch) => {
    saveUiSettings(patch).catch(() => {});
  };

  const presets = availableUiPresets?.length ? availableUiPresets : [getUiPreset("default")];
  const voiceProfiles =
    ttsStatus?.tts_voice_profiles ||
    ttsStatus?.voice_profiles ||
    ttsSettings.tts_voice_profiles ||
    EMPTY_ARRAY;
  const activeProvider = ttsSettings.tts_provider || ttsStatus?.active_provider || "kokoro";
  const providerRegistry = ttsStatus?.provider_registry || {};
  const privacy = diagnostics?.privacy || {};
  const localOnly =
    privacy.mode === "local_only" &&
    privacy.external_endpoints_blocked !== false &&
    privacy.all_configured_endpoints_local !== false;
  const modelSettings = diagnostics?.model_settings || {};
  const modelRouting = diagnostics?.model_routing || {};
  const selectedModel = modelRouting.prose_generation?.model || diagnostics?.lm_studio?.selected_model || "Auto / loaded model";
  const generatedMedia = storageMaintenance?.generated_media || {};
  const databaseBytes = storageMaintenance?.database?.size_bytes ?? storageMaintenance?.database_bytes;
  const voiceOptions = activeProvider === "browser"
    ? browserVoices
    : activeProvider === "high_quality_local"
      ? [
          "Serena",
          "Vivian",
          "Ono_Anna",
          "Sohee",
          ...((ttsStatus?.custom_voices || []).filter((voice) => voice.enabled).map((voice) => ({
            id: voice.selection_id,
            display_name: voice.display_name,
          }))),
        ]
      : kokoroVoices;
  const pronunciationText = useMemo(
    () => pronunciationEntriesToText(ttsSettings.pronunciation_entries || EMPTY_ARRAY),
    [ttsSettings.pronunciation_entries],
  );

  const applyVoiceProfile = (profileId) => {
    const profile = voiceProfiles.find((item) => item.id === profileId);
    updateTTS({
      tts_provider: profile?.provider || ttsSettings.tts_provider,
      high_quality_local_enabled: profile?.provider === "high_quality_local" ? true : ttsSettings.high_quality_local_enabled,
      tts_quality_mode: profile?.provider === "high_quality_local" ? "premium" : "balanced",
      tts_voice_profile_id: profileId,
      tts_voice: profile?.voice_id || ttsSettings.tts_voice || null,
      tts_speed: Number(profile?.speed) || ttsSettings.tts_speed || 0.95,
      tts_chunking_profile: profile?.chunking_profile || ttsSettings.tts_chunking_profile || "natural",
      narration_pacing: profile?.narration_pacing || ttsSettings.narration_pacing || "natural",
    });
  };

  const applyProvider = (provider) => {
    const premium = provider === "high_quality_local";
    updateTTS({
      tts_provider: provider,
      high_quality_local_enabled: premium ? true : ttsSettings.high_quality_local_enabled,
      tts_quality_mode: premium ? "premium" : "balanced",
      tts_voice_profile_id: premium ? "premium_female_narrator" : ttsSettings.tts_voice_profile_id === "premium_female_narrator" ? "natural_female_narrator" : ttsSettings.tts_voice_profile_id,
      tts_voice: premium ? "Serena" : ttsSettings.tts_voice_profile_id === "premium_female_narrator" ? null : ttsSettings.tts_voice,
    });
  };

  const refreshAll = () => {
    fetchDiagnostics();
    fetchTTSStatus();
    fetchStorageMaintenance();
  };
  const showCategory = (id, keywords = "") => {
    const query = settingsSearch.trim().toLowerCase();
    if (!query) return activeCategory === id;
    const label = SETTINGS_CATEGORIES.find(([category]) => category === id)?.[1] || id;
    return `${label} ${keywords}`.toLowerCase().includes(query);
  };

  return (
    <motion.div
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-50 bg-black/55 backdrop-blur-sm"
      exit={{ opacity: 0 }}
      initial={{ opacity: 0 }}
    >
      <button aria-label="Dismiss settings" className="absolute inset-0" onClick={onClose} type="button" />
      <motion.aside
        animate={{ x: 0 }}
        aria-label="StoryDriver settings"
        className="sd-drawer absolute right-0 top-0 flex h-full w-full max-w-xl flex-col overflow-x-hidden border-l border-line bg-panel shadow-glow"
        exit={{ x: 640 }}
        initial={false}
        transition={{ type: "spring", stiffness: 340, damping: 32 }}
      >
        <header className="flex min-h-16 items-center justify-between border-b border-line px-4 sm:px-5">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Settings</h2>
            <p className="text-xs text-muted">Definitive local writing path</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              aria-label="Refresh settings status"
              className="sd-icon-button grid h-10 w-10 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200"
              onClick={refreshAll}
              title="Refresh status"
              type="button"
            >
              <RefreshCw size={16} />
            </button>
            <button
              aria-label="Close settings"
              className="sd-icon-button grid h-10 w-10 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200"
              onClick={onClose}
              title="Close"
              type="button"
            >
              <X size={17} />
            </button>
          </div>
        </header>

        <div className="shrink-0 border-b border-line p-3">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" size={15} />
            <input
              aria-label="Search settings"
              className={`${fieldClass} pl-9`}
              onChange={(event) => setSettingsSearch(event.target.value)}
              placeholder="Search settings"
              value={settingsSearch}
            />
          </label>
          <nav aria-label="Settings categories" className="story-scrollbar mt-3 flex gap-1.5 overflow-x-auto pb-1">
            {SETTINGS_CATEGORIES.map(([id, label]) => (
              <button
                className={`min-h-9 shrink-0 rounded-md border px-3 text-xs font-medium ${activeCategory === id && !settingsSearch ? "border-tide/40 bg-tide/10 text-tide" : "border-line bg-[#0d0e11] text-zinc-300"}`}
                key={id}
                onClick={() => { setActiveCategory(id); setSettingsSearch(""); }}
                type="button"
              >
                {label}
              </button>
            ))}
          </nav>
        </div>

        <div className="story-scrollbar min-h-0 flex-1 space-y-5 overflow-x-hidden overflow-y-auto p-4 sm:p-5">
          {showCategory("general", "local desktop data storydriver status") ? (
            <SettingSection icon={Gauge} title="General">
              <div className="grid gap-2 rounded-lg border border-line bg-[#0d0e11] p-3 text-sm">
                <div className="flex items-center justify-between gap-3"><span className="text-muted">Writing path</span><span className="text-zinc-200">Definitive local</span></div>
                <div className="flex items-center justify-between gap-3"><span className="text-muted">Images</span><span className="text-zinc-200">Paused</span></div>
                <div className="flex items-center justify-between gap-3"><span className="text-muted">Data</span><span className="safe-wrap text-right text-zinc-200">{storageMaintenance?.data_dir || diagnostics?.paths?.data_dir || "Local data root"}</span></div>
              </div>
            </SettingSection>
          ) : null}

          {showCategory("models", "provider model gguf llama cpp lm studio openai local routing") ? (
            <SettingSection icon={Server} title="Models">
              <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
                <div className="text-sm font-medium text-zinc-200">{selectedModel}</div>
                <div className="mt-1 text-xs text-muted">Provider, local model library, runtime controls, prompts, presets, and task routing.</div>
                <button className="sd-action-button mt-3 inline-flex min-h-10 items-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200" onClick={() => { onClose(); onOpenModels?.(); }} type="button">
                  <Server size={15} />
                  Open model settings
                </button>
              </div>
            </SettingSection>
          ) : null}

          {showCategory("memory", "story state continuity relationships inventory blocking accepted version") ? (
            <SettingSection icon={Database} title="Memory & Continuity">
              <div className="grid grid-cols-2 gap-x-3 gap-y-2 rounded-lg border border-line bg-[#0d0e11] p-3 text-xs text-muted">
                <span>Story State</span><span className="text-right text-zinc-200">Automatic</span>
                <span>Accepted versions</span><span className="text-right text-zinc-200">Canonical</span>
                <span>Scene blocking</span><span className="text-right text-zinc-200">Tracked</span>
                <span>Relationships and objects</span><span className="text-right text-zinc-200">Tracked</span>
              </div>
            </SettingSection>
          ) : null}

          {showCategory("writing", "pipeline planning review repair length prose prompt") ? <SettingSection icon={Gauge} title="Writing">
            <div className="grid gap-3 rounded-lg border border-line bg-[#0d0e11] p-3">
              <div className="flex min-w-0 items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-zinc-100">Definitive pipeline</div>
                  <div className="safe-wrap mt-1 text-xs text-muted">{selectedModel}</div>
                </div>
                <Status ok>Active</Status>
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs text-muted">
                <span>Scene plan</span><span className="text-right text-zinc-200">Required</span>
                <span>Continuity pack</span><span className="text-right text-zinc-200">Memory v3</span>
                <span>Review</span><span className="text-right text-zinc-200">Required</span>
                <span>Repair limit</span><span className="text-right text-zinc-200">One targeted pass</span>
              </div>
              {modelSettings.writing_path && modelSettings.writing_path !== "deliberate_pipeline" ? (
                <p className="rounded-lg border border-ember/30 bg-ember/10 px-3 py-2 text-xs text-ember">
                  Diagnostics report a stale writing path. Refresh the backend before generating.
                </p>
              ) : null}
            </div>
          </SettingSection> : null}

          {showCategory("privacy", "lan private network endpoints offline local") ? <SettingSection icon={ShieldCheck} title="LAN & Privacy">
            <div className="flex min-w-0 items-center justify-between gap-3 rounded-lg border border-line bg-[#0d0e11] p-3">
              <div className="min-w-0">
                <div className="text-sm font-medium text-zinc-200">Local endpoints only</div>
                <div className="safe-wrap mt-1 text-xs text-muted">External runtime endpoints are blocked by default.</div>
              </div>
              <Status ok={localOnly}>{localOnly ? "Private" : "Override"}</Status>
            </div>
          </SettingSection> : null}

          {showCategory("diagnostics", "services backend kokoro qwen lm studio status storage") ? <SettingSection icon={Server} title="Services">
            <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
              <ServiceRow endpoint={API_BASE_URL} name="StoryDriver backend" ok={diagnostics?.backend === "ok"} />
              <ServiceRow endpoint={diagnostics?.lm_studio?.base_url || "http://localhost:1234"} name="LM Studio" ok={Boolean(diagnostics?.lm_studio?.reachable)} />
              <ServiceRow endpoint={ttsSettings.kokoro_base_url || "http://localhost:8880"} name="Kokoro" ok={Boolean(ttsStatus?.kokoro?.reachable ?? diagnostics?.kokoro?.reachable)} />
              <ServiceRow
                endpoint="Local on demand"
                name="Qwen3-TTS 0.6B"
                ok={Boolean(ttsStatus?.qwen_premium?.loaded)}
                paused={!ttsStatus?.qwen_premium?.loaded}
                statusLabel={ttsStatus?.qwen_premium?.loaded ? "Loaded" : "On demand"}
              />
            </div>
          </SettingSection> : null}

          {showCategory("appearance", "theme reading font scale composer motion") ? <SettingSection icon={Palette} title="Appearance">
            <div className="grid gap-4">
              <label>
                <SettingLabel className={labelClass} name="Interface preset" />
                <select className={fieldClass} onChange={(event) => setUiPreset(event.target.value)} value={uiPresetId}>
                  {presets.map((preset) => (
                    <option key={preset.id} value={preset.id}>{presetName(preset)}</option>
                  ))}
                </select>
              </label>

              <div className="grid gap-3 border-t border-line pt-4 min-[500px]:grid-cols-2">
                <label>
                  <SettingLabel className={labelClass} name="Reading width" />
                  <select className={fieldClass} onChange={(event) => updateUI({ reading_width: event.target.value })} value={uiSettings.reading_width}>
                    <option value="narrow">Narrow</option>
                    <option value="comfortable">Comfortable</option>
                    <option value="wide">Wide</option>
                    <option value="full">Full</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Reading font" />
                  <select className={fieldClass} onChange={(event) => updateUI({ prose_font: event.target.value })} value={uiSettings.prose_font}>
                    <option value="serif">Serif</option>
                    <option value="sans">Sans</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Prose size" />
                  <input className={fieldClass} max="28" min="14" onChange={(event) => updateUI({ prose_font_size: Number(event.target.value) })} type="number" value={uiSettings.prose_font_size} />
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Line height" />
                  <input className={fieldClass} max="2.4" min="1.35" onChange={(event) => updateUI({ prose_line_height: Number(event.target.value) })} step="0.05" type="number" value={uiSettings.prose_line_height} />
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Paragraph spacing" />
                  <input className={fieldClass} max="2.5" min="0.5" onChange={(event) => updateUI({ paragraph_spacing: Number(event.target.value) })} step="0.1" type="number" value={uiSettings.paragraph_spacing} />
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Interface scale" />
                  <select className={fieldClass} onChange={(event) => updateUI({ ui_scale: Number(event.target.value) })} value={uiSettings.ui_scale}>
                    {[90, 100, 110, 120].map((value) => <option key={value} value={value}>{value}%</option>)}
                    {![90, 100, 110, 120].includes(uiSettings.ui_scale) ? <option value={uiSettings.ui_scale}>Custom {uiSettings.ui_scale}%</option> : null}
                  </select>
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Custom interface scale" />
                  <input className={fieldClass} max="125" min="90" onChange={(event) => updateUI({ ui_scale: Number(event.target.value) })} step="1" type="number" value={uiSettings.ui_scale} />
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Mobile scale" />
                  <select className={fieldClass} onChange={(event) => updateUI({ mobile_scale: event.target.value })} value={uiSettings.mobile_scale}>
                    <option value="follow">Follow global</option>
                    <option value="compact">Compact</option>
                    <option value="default">Default</option>
                    <option value="large">Large</option>
                  </select>
                </label>
              </div>

              <div className="grid gap-3 border-t border-line pt-4 min-[500px]:grid-cols-2">
                <label>
                  <SettingLabel className={labelClass} name="Composer size" />
                  <select className={fieldClass} onChange={(event) => updateUI({ composer_size: event.target.value })} value={uiSettings.composer_size}>
                    <option value="compact">Compact</option>
                    <option value="comfortable">Comfortable</option>
                    <option value="tall">Tall</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Composer text" />
                  <input className={fieldClass} max="22" min="14" onChange={(event) => updateUI({ composer_font_size: Number(event.target.value) })} type="number" value={uiSettings.composer_font_size} />
                </label>
                {uiSettings.composer_size === "custom" ? (
                  <>
                    <label>
                      <SettingLabel className={labelClass} name="Minimum height" />
                      <input className={fieldClass} max="360" min="56" onChange={(event) => updateUI({ composer_min_height: Number(event.target.value) })} type="number" value={uiSettings.composer_min_height} />
                    </label>
                    <label>
                      <SettingLabel className={labelClass} name="Maximum height" />
                      <input className={fieldClass} max="640" min="120" onChange={(event) => updateUI({ composer_max_height: Number(event.target.value) })} type="number" value={uiSettings.composer_max_height} />
                    </label>
                  </>
                ) : null}
                <label>
                  <SettingLabel className={labelClass} name="Director-note spacing" />
                  <input className={fieldClass} max="2.2" min="1.2" onChange={(event) => updateUI({ director_line_height: Number(event.target.value) })} step="0.05" type="number" value={uiSettings.director_line_height} />
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Motion" />
                  <select className={fieldClass} onChange={(event) => updateUI({ motion: event.target.value })} value={uiSettings.motion}>
                    <option value="off">Off</option>
                    <option value="subtle">Subtle</option>
                    <option value="full">Full</option>
                  </select>
                </label>
                <label className="grid min-h-10 grid-cols-[auto_minmax(0,1fr)] items-center gap-2 text-sm text-zinc-300">
                  <input checked={uiSettings.easter_eggs_enabled} onChange={(event) => updateUI({ easter_eggs_enabled: event.target.checked })} type="checkbox" />
                  <SettingLabel name="Easter eggs" />
                </label>
              </div>

              <button className="sd-action-button inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200" onClick={() => updateUI(DEFAULT_UI_SETTINGS)} type="button">
                <Type size={15} />
                Restore display defaults
              </button>
            </div>
          </SettingSection> : null}

          {showCategory("appearance", "background workspace image theme") ? <SettingSection icon={Palette} title="Workspace Backgrounds">
            <BackgroundLibrarySettings />
          </SettingSection> : null}

          {showCategory("narration", "tts kokoro qwen voice pronunciation breathing playback") ? <SettingSection icon={Volume2} title="Narration">
            <div className="grid gap-4">
              <div className="grid gap-3 min-[500px]:grid-cols-2">
                <label>
                  <SettingLabel className={labelClass} name="Provider" />
                  <select
                    className={fieldClass}
                    onChange={(event) => applyProvider(event.target.value)}
                    value={activeProvider}
                  >
                    <option value="kokoro">Kokoro local</option>
                    {providerRegistry.high_quality_local?.available && providerRegistry.high_quality_local?.enabled ? (
                      <option value="high_quality_local">Qwen3-TTS 0.6B</option>
                    ) : null}
                    <option value="browser">Browser fallback</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Narrator profile" />
                  <select
                    className={fieldClass}
                    onChange={(event) => applyVoiceProfile(event.target.value)}
                    value={ttsSettings.tts_voice_profile_id || "natural_female_narrator"}
                  >
                    {voiceProfiles.map((profile) => (
                      <option disabled={profile.available === false || profile.enabled === false} key={profile.id} value={profile.id}>
                        {profile.display_name || profile.name || profile.id}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="grid gap-3 min-[500px]:grid-cols-[minmax(0,1fr)_9rem]">
                <label>
                  <SettingLabel className={labelClass} name="Voice" />
                  <select
                    className={fieldClass}
                    onChange={(event) => updateTTS({ tts_voice: event.target.value || null })}
                    value={ttsSettings.tts_voice || ""}
                  >
                    <option value="">Profile default</option>
                    {voiceOptions.map((voice) => {
                      const id = typeof voice === "string" ? voice : voice.voice || voice.id || voice.name;
                      const name = typeof voice === "string" ? voice : voice.display_name || voice.name || id;
                      return <option key={id} value={id}>{name}</option>;
                    })}
                  </select>
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Speed" />
                  <input
                    className={fieldClass}
                    max="1.35"
                    min="0.7"
                    onChange={(event) => updateTTS({ tts_speed: Number(event.target.value) || 0.95 })}
                    step="0.05"
                    type="number"
                    value={ttsSettings.tts_speed || 0.95}
                  />
                </label>
              </div>

              <div className="grid gap-3 min-[500px]:grid-cols-2">
                <label>
                  <SettingLabel className={labelClass} name="Text follow" />
                  <select
                    className={fieldClass}
                    onChange={(event) => updateTTS({ tts_follow_mode: event.target.value, tts_follow_highlight: event.target.value })}
                    value={ttsSettings.tts_follow_mode || "phrase"}
                  >
                    <option value="off">Off</option>
                    <option value="phrase">Phrase</option>
                    <option value="sentence">Sentence</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className={labelClass} name="Prebuffer chunks" />
                  <input
                    className={fieldClass}
                    max="3"
                    min="1"
                    onChange={(event) => updateTTS({ tts_prebuffer_chunks: Number(event.target.value) || 2 })}
                    step="1"
                    type="number"
                    value={ttsSettings.tts_prebuffer_chunks || 2}
                  />
                </label>
              </div>

              <div>
                <SettingLabel className={labelClass} name="Breathing" />
                <div className="grid grid-cols-3 overflow-hidden rounded-lg border border-line" role="group" aria-label="Breathing">
                  {[{ id: "off", label: "Off" }, { id: "natural", label: "Natural" }, { id: "cinematic", label: "Cinematic" }].map((mode) => (
                    <button
                      className={`min-h-10 border-l border-line px-3 text-xs font-medium first:border-l-0 ${
                        (ttsSettings.breathing_mode || "natural") === mode.id
                          ? "bg-tide/15 text-tide"
                          : "bg-[#0d0e11] text-zinc-300 hover:bg-white/[0.04]"
                      }`}
                      key={mode.id}
                      onClick={() => updateTTS({ breathing_mode: mode.id })}
                      type="button"
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
              </div>

              <label>
                <SettingLabel className={labelClass} name="Pronunciation aliases" />
                <textarea
                  className={`${fieldClass} min-h-24 py-2 font-mono text-xs`}
                  defaultValue={pronunciationText}
                  key={ttsSettings.pronunciation_dictionary_version || pronunciationText}
                  onBlur={(event) => updateTTS({ pronunciation_entries: parsePronunciationEntries(event.target.value) })}
                  placeholder="Aeron => AIR-on"
                  spellCheck={false}
                />
              </label>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  className="sd-action-button inline-flex min-h-10 items-center gap-2 rounded-lg border border-tide/35 bg-tide/10 px-3 text-sm font-medium text-tide disabled:opacity-50"
                  disabled={ttsVoicePreview?.isLoading}
                  onClick={() => activeProvider === "browser" ? testNarration("browser") : previewTTSVoice({})}
                  type="button"
                >
                  {ttsVoicePreview?.isLoading ? <RefreshCw className="animate-spin" size={15} /> : <Play size={15} />}
                  Preview
                </button>
                <label className="inline-flex min-h-10 items-center gap-2 text-sm text-zinc-300">
                  <input
                    checked={Boolean(ttsSettings.allow_browser_fallback)}
                    onChange={(event) => updateTTS({ allow_browser_fallback: event.target.checked })}
                    type="checkbox"
                  />
                  Browser fallback
                </label>
                <Status ok={Boolean(ttsStatus?.kokoro?.reachable)}>{ttsStatus?.kokoro?.reachable ? "Ready" : "Unavailable"}</Status>
              </div>
              {ttsVoicePreview?.error ? <p className="text-xs text-ember">{ttsVoicePreview.error}</p> : null}
              <CustomVoiceLibrary
                onPreview={(voice) => previewTTSVoice({ profileId: voice.profile_id, voice: voice.selection_id })}
                onRefresh={fetchTTSStatus}
                onUse={(voice) => updateTTS({
                  tts_provider: "high_quality_local",
                  high_quality_local_enabled: true,
                  tts_quality_mode: "premium",
                  tts_voice_profile_id: voice.profile_id,
                  tts_voice: voice.selection_id,
                  tts_speed: 1,
                })}
                voices={ttsStatus?.custom_voices || EMPTY_ARRAY}
              />
              <VoiceComparisonLab profiles={voiceProfiles} saveTTSSettings={saveTTSSettings} />
              <NarrationDeviceDiagnostics
                backendReachable={diagnostics?.backend === "ok"}
                narration={narration}
                ttsStatus={ttsStatus}
              />
            </div>
          </SettingSection> : null}

          {showCategory("diagnostics", "storage database generated media deletion jobs") ? <SettingSection icon={Database} title="Storage">
            <div className="grid gap-2 rounded-lg border border-line bg-[#0d0e11] p-3 text-sm">
              <div className="flex items-center justify-between gap-3"><span className="text-muted">Database</span><span className="text-zinc-200">{formatBytes(databaseBytes)}</span></div>
              <div className="flex items-center justify-between gap-3"><span className="text-muted">Generated media</span><span className="text-zinc-200">{formatBytes(generatedMedia.total_bytes ?? generatedMedia.bytes)}</span></div>
              <div className="flex items-center justify-between gap-3"><span className="text-muted">Active deletion jobs</span><span className="text-zinc-200">{storageMaintenance?.delete_jobs?.active_count ?? 0}</span></div>
              <button
                className="sd-action-button mt-1 inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200 disabled:opacity-50"
                disabled={isLoadingStorageMaintenance}
                onClick={fetchStorageMaintenance}
                type="button"
              >
                <RefreshCw className={isLoadingStorageMaintenance ? "animate-spin" : ""} size={15} />
                Refresh storage
              </button>
            </div>
          </SettingSection> : null}

          {showCategory("about", "version architecture webview desktop license") ? (
            <SettingSection icon={ShieldCheck} title="About">
              <div className="grid gap-2 rounded-lg border border-line bg-[#0d0e11] p-3 text-sm">
                <div className="flex items-center justify-between gap-3"><span className="text-muted">StoryDriver</span><span className="text-zinc-200">1.0.0 RC</span></div>
                <div className="flex items-center justify-between gap-3"><span className="text-muted">Desktop shell</span><span className="text-zinc-200">Windows WebView2</span></div>
                <div className="flex items-center justify-between gap-3"><span className="text-muted">Runtime</span><span className="text-zinc-200">Local only</span></div>
              </div>
            </SettingSection>
          ) : null}

          {settingsSearch && !SETTINGS_CATEGORIES.some(([id]) => showCategory(id)) ? (
            <p className="py-8 text-center text-sm text-muted">No matching settings.</p>
          ) : null}
        </div>
      </motion.aside>
    </motion.div>
  );
}
