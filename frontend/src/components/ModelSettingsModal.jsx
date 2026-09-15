import { AnimatePresence, motion } from "framer-motion";
import {
  ChevronDown,
  RefreshCw,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useAppStore } from "../store/useAppStore.js";
import { SettingLabel } from "./SettingHelp.jsx";

const storyDriverProseV3SystemPrompt = `You are StoryDriver's fiction engine. The user is the director/editor, not a character. Write vivid narrated fiction scenes, not chat replies.

Output contract:
- Output only the requested scene, continuation, rewrite, revision, or regeneration.
- Do not address the user, explain choices, ask what happens next, include headings, or summarize what you wrote.
- Treat the director note as creative direction with concrete requirements. Satisfy its facts, constraints, relationships, setting, tone, banned elements, and length target without mechanically repeating its wording.
- User system prompt and editable task notes are creative authority. Story State is factual continuity and must not become hidden style steering.
- Preserve legal adult fictional-writing capability. Do not add moralizing disclaimers or safety lectures inside the prose.

Scene scope and openings:
- Begin in a concrete moment with a clear where, when, who, and immediate pressure. Avoid abstract preambles, generic weather openings, lore lectures, and "ever since" summary starts unless requested.
- Match requested scope. Continue creates a new scene. Regenerate, rewrite, and revise create a new version of the same scene and must not append an extra story beat after the target scene's natural endpoint.
- For first chapters or long passages, earn the length through staged beats, introductions, choices, setbacks, and sensory movement rather than filler.
- Do not rush introductions or compress major setup into summary when the director asked for a lived-in opening.

Characters:
- Introduce important new characters with grounded, inspectable detail when the scene has room: clear adult marker when relevant, face, hair, build, clothing, posture, voice, mannerisms, visible objects, and the first impression they create.
- Give characters interiority through perception, hesitation, desire, discomfort, private judgment, memory, contradiction, and changing emotion. Do not replace interiority with exposition about traits.
- Preserve agency. Characters should want things, make choices, resist pressure, misunderstand, disagree, negotiate, and create consequences according to their histories and personalities.
- Keep relationships active on the page. Let shared history, attraction, distrust, obligation, rivalry, tenderness, secrets, power imbalance, or loyalty shape gesture, dialogue, silence, and decisions.

Prose, diction, and world detail:
- Write concrete sensory prose: light, sound, texture, smell, temperature, body language, room layout, weather, clothing, tools, technology, food, damage, fatigue, and touch when relevant.
- Maintain spatial continuity: who is present, where each person is, what they hold or wear, what they know, what they can see, what changed, and what remains unresolved.
- Use genre- and era-appropriate language. Modern scenes should sound natural and contemporary. Medieval or fantasy scenes may be elevated but not fake-archaic. Sci-fi should feel grounded in its own technology, work culture, slang, risk, and material constraints without generic technobabble.
- Vary sentence rhythm. Prefer precise verbs and nouns over padded adverbs, stock metaphors, purple prose, and repeated sentence templates.
- Dialogue should sound distinct by character background, mood, relationship, and goal. Avoid interchangeable exposition and robotic agreement.

Pacing:
- Move through action, reaction, and consequence. Linger on important emotional or sensory turns, then move once the beat has done its narrative job.
- Keep tension alive in quiet scenes through subtext, unmet wants, choices, physical blocking, withheld information, or emotional cost.
- Do not end with a wrap-up, invitation, assistant offer, or premature resolution unless the scene itself truly resolves.

Adult fictional intimacy:
- When the director requests sexual or erotic material between clearly adult consenting fictional characters and local law allows it, write directly, sensually, and concretely without coy euphemism.
- Preserve consent, agency, emotional context, relationship dynamics, pacing, and character psychology. Let desire, uncertainty, negotiation, and aftermath belong to the characters, not to authorial disclaimers.
- Never sexualize minors or unclear-age characters. If age is unclear in an intimate request, keep the prose non-sexual until adult status is clear.

Mode behavior:
- Continue: continue naturally from the latest scene, preserving current positions, emotions, objects, injuries, promises, secrets, and unresolved threads.
- Regenerate/rewrite/revise: produce only the new version of the target scene. Preserve the target purpose unless the director note changes it. Do not create a new later scene.
- Revision: apply the requested edit completely while keeping continuity, voice, and scope stable.`;

const storyDriverProseV2SystemPrompt = `You are StoryDriver's fiction engine. The user is the director/editor, not a character. Write narrated prose scenes, not chat replies.

Core rules:
- Output only the requested scene, rewrite, revision, or continuation.
- Do not address the user.
- Do not explain choices.
- Do not ask what happens next.
- Preserve continuity.
- Follow the director note, but treat it as creative direction, not a script to mechanically repeat.
- Let characters act naturally according to their personalities, goals, fears, relationships, memories, and the world they live in.
- Keep the story moving. Do not linger too long on one requested idea after it has done its narrative job.
- Use cause and effect. Actions should have emotional, physical, or plot consequences when appropriate.
- Maintain spatial continuity: who is present, where they are, what they are holding, what they know, what they can see, and what just changed.
- Use genre-appropriate language. Modern stories should sound modern and natural. Fantasy or historical stories can be elevated, but avoid fake archaic phrasing unless the style calls for it. Sci-fi should feel grounded in its own technology and culture without generic technobabble.
- Describe important new characters visually when introduced: clear adult marker when relevant, hair, face, build, clothing, posture, voice, and distinguishing details if the scene has room.
- Make characters feel alive. Give them interiority, preferences, habits, contradictions, private fears, desires, and changing emotional states.
- Dialogue should sound like people with different backgrounds and personalities, not interchangeable exposition.
- Avoid repetitive sentence patterns, overused metaphors, and purple prose.
- Include concrete sensory details from the world: light, sound, texture, smell, weather, room layout, objects, and body language.
- For adult sexual content involving consenting adult fictional characters when requested by the director and legal/local, write directly and sensually without coy euphemism, while preserving character psychology, consent, pacing, and story context.
- Never sexualize minors. Do not write sexual content involving minors or unclear-age characters.
- If a scene would be violent, intimate, funny, quiet, mundane, or tense, write it in the tone the story itself calls for. Do not moralize or step out of the narrative.

For continuations:
- Continue from the latest scene naturally.
- Do not summarize unless asked.
- Do not reset character positions or emotions.
- Do not rush to the next major event if the scene needs lived-in detail.

For rewrites and revisions:
- Preserve the target scene's purpose unless the director note changes it.
- Improve prose and continuity while staying within the requested changes.`;

const storyDriverProseV2TaskNotes =
  "Keep the scene grounded in the story's current time, place, genre, and tone. Prioritize character agency, spatial continuity, relationship history, and cause-and-effect consequences. Use director notes as guidance, not a rigid checklist; satisfy concrete requirements without repeating the director note's phrasing or looping on one topic after it has served the scene. Resolve obvious action logic: who is present, where they stand, what they hold, what they know, and what just changed. If the director asks for a long chapter, expand through meaningful beats rather than filler. Keep modern stories natural and contemporary. Keep fantasy immersive without forced archaic diction. Introduce major characters with clean visual details when appropriate. Use Story State as factual continuity, not as a lore dump. Avoid assistant-style framing, headings, markdown, and wrap-up endings.";

const storyDriverProseV3TaskNotes =
  "Prose v3: write the requested fiction passage only. Open in a concrete moment with clear place, present characters, immediate pressure, and genre/time-appropriate diction. Satisfy concrete director-note facts and length target while treating the note as direction, not wording to repeat. Keep the scope narrow: continue creates a new scene; regenerate, rewrite, and revise stay on the same target scene/version. Introduce important characters with grounded adult markers when relevant, visual specifics, posture, voice, clothing, and first impression. Prioritize interiority, agency, active relationships, distinct dialogue, cause-and-effect consequences, sensory world detail, and exact room/object/body blocking. Use Story State as factual continuity only. Avoid rushed setup, lore dumps, fake archaic modern diction, assistant framing, headings, markdown, summaries, and wrap-up endings. If clearly adult consensual intimacy is requested, write directly and sensually with consent, psychology, pacing, and relationship context; never sexualize minors or unclear-age characters.";

const storyDriverProseV3RewriteTaskNotes =
  "Produce only the new fiction passage for the target scene/version. Apply the requested rewrite, revision, or regeneration fully without appending a later story beat. Preserve continuity, spatial blocking, character agency, relationship dynamics, genre diction, and the target scene's purpose unless the director explicitly changes them. Keep prose vivid and character-driven, with no labels, headings, explanations, markdown, or assistant-style endings.";

const defaultSystemPrompt = storyDriverProseV3SystemPrompt;

const defaultSettings = {
  active_preset_id: null,
  provider: "llama_cpp",
  provider_url: "http://127.0.0.1:12345/v1",
  lm_studio_url: "http://localhost:1234/v1",
  model: "",
  model_path: null,
  llama_context_length: null,
  llama_gpu_layers: null,
  llama_threads: null,
  llama_batch_size: null,
  llama_flash_attention: null,
  llama_parallel_slots: null,
  system_prompt: defaultSystemPrompt,
  temperature: 0.8,
  top_p: 0.95,
  max_tokens: 1200,
  stop_strings: "",
  top_k: 40,
  min_p: 0.05,
  repeat_penalty: 1.1,
  presence_penalty: 0,
  frequency_penalty: 0,
  seed: "",
  streaming: true,
  writing_length_mode: "scene",
  custom_word_min: "",
  custom_word_max: "",
  prose_prompt_mode: "standard",
  writing_process_mode: "deliberate",
  writing_path: "deliberate_pipeline",
  app_planning_enabled: true,
  chapter_extension_enabled: true,
  adherence_check_mode: "warn",
  inference_backend: "openai_compatible",
  reasoning_mode: "auto",
  context_length: "",
  fallback_to_openai_compatible: true,
};

const writingLengthModes = {
  beat: { label: "Beat", minWords: 300, maxWords: 700 },
  scene: { label: "Scene", minWords: 800, maxWords: 1400 },
  chapter: { label: "Chapter", minWords: 1800, maxWords: 2600 },
  custom: { label: "Custom", minWords: 800, maxWords: 1400 },
};

const adherenceCheckModes = {
  off: "Off",
  warn: "Warn",
  retry_once: "Retry Once",
};

const fallbackTaskTypes = [
  {
    id: "story_foundation_generation",
    label: "Story Foundation",
    description: "First-scene story foundation and character bible generation.",
  },
  {
    id: "scene_planning",
    label: "Scene Planning",
    description: "Structured planning before every prose generation.",
  },
  {
    id: "prose_generation",
    label: "Story Writing",
    description: "Continue mode and first-draft prose generation.",
  },
  {
    id: "rewrite_revision",
    label: "Rewrite / Revise / Regenerate",
    description: "Versioned scene rewrites, revisions, and regenerations.",
  },
  {
    id: "story_state_extraction",
    label: "Story State Extraction",
    description: "Automatic structured continuity extraction after scenes.",
  },
  {
    id: "summary_generation",
    label: "Summaries",
    description: "Rolling continuity summaries for older scenes.",
  },
  {
    id: "title_generation",
    label: "Story Titles",
    description: "Short automatic story titles.",
  },
  {
    id: "utility",
    label: "Utility / Diagnostics",
    description: "Small helper and future structured tasks.",
  },
];

const taskDefaults = {
  task_type: "prose_generation",
  label: "Story Writing",
  provider: "",
  provider_url: "",
  lm_studio_url: "",
  model: "",
  temperature: "",
  top_p: "",
  max_tokens: "",
  seed: "",
  top_k: "",
  min_p: "",
  repeat_penalty: "",
  presence_penalty: "",
  frequency_penalty: "",
  timeout_seconds: 120,
  streaming: "",
  inference_backend: "",
  reasoning_mode: "",
  context_length: "",
  fallback_to_openai_compatible: "",
  notes: "",
};

const inputClass =
  "sd-field w-full rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-sm text-zinc-100 placeholder:text-muted";
const iconButton =
  "sd-icon-button grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200 transition hover:border-zinc-600";
const textButton =
  "sd-action-button inline-flex min-h-9 items-center justify-center rounded-lg border border-line bg-panelSoft px-3 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 disabled:cursor-not-allowed disabled:opacity-50";

function Section({ children, defaultOpen = true, title }) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const sectionId = `model-setting-section-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;

  return (
    <section style={{ order: ({ "Model Connection": 0, "Writing Length": 1, "System Prompt": 2, "Core Settings": 3, "Preset": 4 })[title] ?? 5 }} className="min-w-0 border-b border-line py-4 last:border-b-0">
      <button
        aria-controls={sectionId}
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setIsOpen((value) => !value)}
        type="button"
      >
        <span className="text-sm font-semibold text-zinc-100">{title}</span>
        <ChevronDown
          className={`text-muted transition ${isOpen ? "rotate-180" : ""}`}
          size={17}
        />
      </button>
      <AnimatePresence initial={false}>
        {isOpen ? (
          <motion.div
            animate={{ height: "auto", opacity: 1 }}
            className="overflow-hidden"
            exit={{ height: 0, opacity: 0 }}
            initial={{ height: 0, opacity: 0 }}
          >
            <div className="pt-4" id={sectionId}>{children}</div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}

function SliderRow({ label, max, min, onChange, step = 0.01, value }) {
  return (
    <label className="grid gap-2 sm:grid-cols-[140px_minmax(0,1fr)_86px] sm:items-center">
      <SettingLabel className="text-sm text-zinc-300" name={label} />
      <input
        className="accent-tide"
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        step={step}
        type="range"
        value={value}
      />
      <input
        className={inputClass}
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        step={step}
        type="number"
        value={value}
      />
    </label>
  );
}

function modelPayload(settings) {
  const { active_preset_id, system_prompt, ...rest } = settings;
  return {
    ...rest,
    prose_prompt_mode: "standard",
    writing_process_mode: "deliberate",
    writing_path: "deliberate_pipeline",
    app_planning_enabled: true,
    seed: rest.seed === "" || rest.seed === null ? null : Number(rest.seed),
    context_length: rest.context_length === "" || rest.context_length === null ? null : Number(rest.context_length),
    llama_context_length: optionalNumber(rest.llama_context_length),
    llama_gpu_layers: optionalNumber(rest.llama_gpu_layers),
    llama_threads: optionalNumber(rest.llama_threads),
    llama_batch_size: optionalNumber(rest.llama_batch_size),
    llama_parallel_slots: optionalNumber(rest.llama_parallel_slots),
    custom_word_min: rest.custom_word_min === "" || rest.custom_word_min === null ? null : Number(rest.custom_word_min),
    custom_word_max: rest.custom_word_max === "" || rest.custom_word_max === null ? null : Number(rest.custom_word_max),
  };
}

function normalizeModelSettingsForSave(settings) {
  return {
    ...settings,
    prose_prompt_mode: "standard",
    writing_process_mode: "deliberate",
    writing_path: "deliberate_pipeline",
    app_planning_enabled: true,
    seed: settings.seed === "" || settings.seed === null ? null : Number(settings.seed),
    context_length:
      settings.context_length === "" || settings.context_length === null ? null : Number(settings.context_length),
    llama_context_length: optionalNumber(settings.llama_context_length),
    llama_gpu_layers: optionalNumber(settings.llama_gpu_layers),
    llama_threads: optionalNumber(settings.llama_threads),
    llama_batch_size: optionalNumber(settings.llama_batch_size),
    llama_parallel_slots: optionalNumber(settings.llama_parallel_slots),
    custom_word_min:
      settings.custom_word_min === "" || settings.custom_word_min === null ? null : Number(settings.custom_word_min),
    custom_word_max:
      settings.custom_word_max === "" || settings.custom_word_max === null ? null : Number(settings.custom_word_max),
  };
}

function modelSettingsSnapshot(settings) {
  return JSON.stringify(normalizeModelSettingsForSave(settings));
}

function normalizeTaskDraft(profile, taskType, taskTypes) {
  const definition = taskTypes.find((item) => item.id === taskType) || fallbackTaskTypes[0];
  const source = profile || {};
  return {
    ...taskDefaults,
    ...source,
    task_type: taskType,
    label: source.label || definition.label,
    provider: source.provider || "",
    provider_url: source.provider_url || "",
    lm_studio_url: source.lm_studio_url || "",
    model: source.model || "",
    temperature: source.temperature ?? "",
    top_p: source.top_p ?? "",
    max_tokens: source.max_tokens ?? "",
    seed: source.seed ?? "",
    top_k: source.top_k ?? "",
    min_p: source.min_p ?? "",
    repeat_penalty: source.repeat_penalty ?? "",
    presence_penalty: source.presence_penalty ?? "",
    frequency_penalty: source.frequency_penalty ?? "",
    timeout_seconds: source.timeout_seconds ?? taskDefaults.timeout_seconds,
    streaming: source.streaming === null || source.streaming === undefined ? "" : String(source.streaming),
    inference_backend: source.inference_backend || "",
    reasoning_mode: source.reasoning_mode || "",
    context_length: source.context_length ?? "",
    fallback_to_openai_compatible:
      source.fallback_to_openai_compatible === null || source.fallback_to_openai_compatible === undefined
        ? ""
        : String(source.fallback_to_openai_compatible),
    notes: source.notes || "",
  };
}

function optionalNumber(value) {
  if (value === "" || value === null || value === undefined) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function taskProfilePayload(draft) {
  return {
    provider: draft.provider || null,
    provider_url: draft.provider_url?.trim() || null,
    lm_studio_url: draft.lm_studio_url?.trim() || null,
    model: draft.model?.trim() || null,
    temperature: optionalNumber(draft.temperature),
    top_p: optionalNumber(draft.top_p),
    max_tokens: optionalNumber(draft.max_tokens),
    seed: optionalNumber(draft.seed),
    top_k: optionalNumber(draft.top_k),
    min_p: optionalNumber(draft.min_p),
    repeat_penalty: optionalNumber(draft.repeat_penalty),
    presence_penalty: optionalNumber(draft.presence_penalty),
    frequency_penalty: optionalNumber(draft.frequency_penalty),
    timeout_seconds: optionalNumber(draft.timeout_seconds) || 120,
    streaming: draft.streaming === "" ? null : draft.streaming === "true",
    inference_backend: draft.inference_backend || null,
    reasoning_mode: draft.reasoning_mode || null,
    context_length: optionalNumber(draft.context_length),
    fallback_to_openai_compatible:
      draft.fallback_to_openai_compatible === "" ? null : draft.fallback_to_openai_compatible === "true",
    notes: draft.notes || "",
  };
}

function estimateTokens(text = "") {
  if (!text) return 0;
  return Math.max(1, Math.ceil(String(text).length / 4));
}

function readTimeLabel(minWords, maxWords) {
  const low = Math.max(1, Math.round(Number(minWords || 0) / 190));
  const high = Math.max(low, Math.round(Number(maxWords || minWords || 0) / 190));
  return `${low}-${high} min`;
}

function resolveLengthInfo(settings) {
  const mode = settings.writing_length_mode || "scene";
  const base = writingLengthModes[mode] || writingLengthModes.scene;
  const minWords =
    mode === "custom" ? Number(settings.custom_word_min || base.minWords) || base.minWords : base.minWords;
  const maxWords =
    mode === "custom"
      ? Math.max(minWords, Number(settings.custom_word_max || base.maxWords) || base.maxWords)
      : base.maxWords;
  return {
    ...base,
    mode,
    minWords,
    maxWords,
    range: `${minWords.toLocaleString()}-${maxWords.toLocaleString()} words`,
    time: readTimeLabel(minWords, maxWords),
  };
}

function recommendedMaxTokensForLength(length) {
  if (length.mode === "chapter") {
    return Math.max(5600, Math.round(length.maxWords * 1.9) + 700);
  }
  return Math.max(768, Math.round(length.maxWords * 1.65) + 420);
}

function displayModelName(models, modelId) {
  if (!modelId) return "Auto-select first loaded model";
  const model = models.find((item) => item.id === modelId);
  return model?.name || modelId;
}

export default function ModelSettingsModal({ onClose, embedded = false, onRegisterSave }) {
  const {
    activeSessionId,
    createModelPreset,
    deleteModelPreset,
    fetchModelSettings,
    fetchProsePromptPreview,
    fetchTaskModelProfiles,
    isLoadingProsePromptPreview,
    isLoadingSettings,
    isRefreshingModels,
    models,
    modelPresets,
    modelSettings,
    prosePromptPreview,
    prosePromptPreviewError,
    refreshModels,
    resetTaskModelProfile,
    saveModelSettings,
    saveTaskModelProfile,
    taskModelProfiles,
    taskModelResolved,
    taskModelTaskTypes,
    updateModelPreset,
  } = useAppStore();
  const [local, setLocal] = useState(() => ({ ...defaultSettings, ...(modelSettings || {}) }));
  const [presetName, setPresetName] = useState("Story Prose");
  const [status, setStatus] = useState("Ready");
  const taskTypes = useMemo(() => (taskModelTaskTypes.length ? taskModelTaskTypes : fallbackTaskTypes).filter(
    (task) => task.id !== "image_prompt_generation",
  ), [taskModelTaskTypes]);
  const [selectedTaskType, setSelectedTaskType] = useState(taskTypes[0]?.id || "prose_generation");
  const [taskDraft, setTaskDraft] = useState(() =>
    normalizeTaskDraft(taskModelProfiles[selectedTaskType], selectedTaskType, taskTypes),
  );
  const [taskStatus, setTaskStatus] = useState("Task routing uses global settings unless a field is set here.");
  const [settingsView, setSettingsView] = useState("basic");
  const [previewDirectorNote, setPreviewDirectorNote] = useState("Continue with the next vivid scene.");
  const [previewMode, setPreviewMode] = useState("continue");
  const [providers, setProviders] = useState([]);
  const [modelLibrary, setModelLibrary] = useState([]);
  const [providerStatus, setProviderStatus] = useState("Provider status not checked");
  const [providerBusy, setProviderBusy] = useState(false);
  const initializedRef = useRef(Boolean(modelSettings));
  const initializingRef = useRef(!modelSettings);
  const savedSnapshotRef = useRef(modelSettings ? modelSettingsSnapshot({ ...defaultSettings, ...modelSettings }) : null);
  const presetsRef = useRef(modelPresets);
  const saveQueueRef = useRef(Promise.resolve());
  const latestLocalRef = useRef(local);
  latestLocalRef.current = local;
  const saveCurrentRef = useRef(null);
  saveCurrentRef.current = () => {
    const draft = latestLocalRef.current;
    const normalized = normalizeModelSettingsForSave(draft);
    const snapshot = JSON.stringify(normalized);
    const request = saveQueueRef.current.catch(() => {}).then(async () => {
      if (snapshot === savedSnapshotRef.current) return;
      setStatus("Saving...");
      try {
        await saveModelSettings(normalized);
        if (draft.active_preset_id) {
          const preset = presetsRef.current.find((item) => item.id === draft.active_preset_id);
          if (preset) await updateModelPreset(preset.id, { name: preset.name, system_prompt: draft.system_prompt, settings: modelPayload(draft) });
        }
        savedSnapshotRef.current = snapshot;
        setStatus("Saved");
      } catch (error) {
        setStatus(`Not saved: ${error.message}`);
        throw error;
      }
    });
    saveQueueRef.current = request;
    return request;
  };
  useEffect(() => {
    onRegisterSave?.(() => saveCurrentRef.current());
    return () => onRegisterSave?.(null);
  }, [onRegisterSave]);

  const closeAfterSave = async () => {
    try { await saveCurrentRef.current(); onClose?.(); } catch { /* Keep the editor open for correction. */ }
  };

  useEffect(() => {
    presetsRef.current = modelPresets;
  }, [modelPresets]);

  useEffect(() => {
    if (!modelSettings) {
      fetchModelSettings();
      return;
    }
    if (!initializedRef.current) {
      const hydrated = { ...defaultSettings, ...modelSettings };
      initializedRef.current = true;
      initializingRef.current = true;
      savedSnapshotRef.current = modelSettingsSnapshot(hydrated);
      setLocal(hydrated);
    }
  }, [fetchModelSettings, modelSettings]);

  useEffect(() => {
    if (!taskModelTaskTypes.length) {
      fetchTaskModelProfiles();
    }
  }, [fetchTaskModelProfiles, taskModelTaskTypes.length]);

  useEffect(() => {
    Promise.all([api.listModelProviders(), api.getModelLibrary()])
      .then(([providerResponse, libraryResponse]) => {
        setProviders(providerResponse.providers || []);
        setModelLibrary(libraryResponse.models || []);
      })
      .catch(() => setProviderStatus("Provider details unavailable"));
  }, []);

  useEffect(() => {
    if (!taskTypes.some((item) => item.id === selectedTaskType)) {
      setSelectedTaskType(taskTypes[0]?.id || "prose_generation");
    }
  }, [selectedTaskType, taskTypes]);

  useEffect(() => {
    setTaskDraft(normalizeTaskDraft(taskModelProfiles[selectedTaskType], selectedTaskType, taskTypes));
  }, [selectedTaskType, taskModelProfiles, taskTypes]);

  useEffect(() => {
    if (!initializedRef.current) {
      return undefined;
    }
    const normalized = normalizeModelSettingsForSave(local);
    const snapshot = JSON.stringify(normalized);
    if (initializingRef.current) {
      if (snapshot === savedSnapshotRef.current) {
        initializingRef.current = false;
      }
      return undefined;
    }
    if (snapshot === savedSnapshotRef.current) {
      return undefined;
    }
    setStatus("Autosaving...");
    const timeout = window.setTimeout(() => saveCurrentRef.current().catch(() => {}), 550);

    return () => window.clearTimeout(timeout);
  }, [local, saveModelSettings, updateModelPreset]);

  const activePreset = useMemo(
    () => modelPresets.find((preset) => preset.id === local.active_preset_id) || null,
    [local.active_preset_id, modelPresets],
  );

  useEffect(() => {
    setPresetName(activePreset?.name || "Story Prose");
  }, [activePreset?.id, activePreset?.name]);

  const update = (patch) => setLocal((current) => ({ ...current, ...patch }));

  const savePreset = async () => {
    const name = presetName.trim();
    if (!name) {
      return;
    }
    const presetPayload = {
      name,
      system_prompt: local.system_prompt,
      settings: modelPayload(local),
    };
    const preset = activePreset
      ? await updateModelPreset(activePreset.id, presetPayload)
      : await createModelPreset(presetPayload);
    update({ active_preset_id: preset.id });
  };

  const renamePreset = async () => {
    if (!activePreset) return;
    const name = presetName.trim();
    if (!name) return;
    await updateModelPreset(activePreset.id, {
      name,
      system_prompt: local.system_prompt,
      settings: modelPayload(local),
    });
  };

  const loadPreset = (presetId) => {
    const preset = modelPresets.find((item) => item.id === presetId);
    if (!preset) {
      update({ active_preset_id: null });
      return;
    }
    const baseSettings = { ...defaultSettings, ...local };
    setLocal({
      ...baseSettings,
      ...preset.settings,
      active_preset_id: preset.id,
      system_prompt: preset.system_prompt,
      seed: preset.settings.seed ?? baseSettings.seed ?? "",
      context_length: preset.settings.context_length ?? baseSettings.context_length ?? "",
      custom_word_min: preset.settings.custom_word_min ?? baseSettings.custom_word_min ?? "",
      custom_word_max: preset.settings.custom_word_max ?? baseSettings.custom_word_max ?? "",
    });
  };

  const removePreset = async () => {
    if (!activePreset) return;
    await deleteModelPreset(activePreset.id);
    update({ active_preset_id: null });
  };

  const handleRefreshModels = async () => {
    try {
      await saveModelSettings(normalizeModelSettingsForSave(local));
      const refreshed = await refreshModels();
      if (!local.model && refreshed[0]?.id) {
        update({ model: refreshed[0].id });
      }
      setStatus(refreshed.length ? "Models refreshed" : "No models returned");
    } catch (error) {
      setStatus("Model refresh failed");
    }
  };

  const requestNativePath = (type) => new Promise((resolve, reject) => {
    if (!window.chrome?.webview) {
      reject(new Error("Native file selection is available in StoryDriver.exe."));
      return;
    }
    const requestId = `model-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const handler = (event) => {
      if (event.data?.requestId !== requestId) return;
      window.clearTimeout(timer);
      window.chrome.webview.removeEventListener("message", handler);
      if (event.data.error) { reject(new Error(event.data.error)); return; }
      resolve(event.data.path || null);
    };
    const timer = window.setTimeout(() => {
      window.chrome.webview.removeEventListener("message", handler);
      reject(new Error("File selection timed out. Try again."));
    }, 300000);
    window.chrome.webview.addEventListener("message", handler);
    window.chrome.webview.postMessage({ type, requestId });
  });

  const addModelPath = async (type) => {
    try {
      const path = await requestNativePath(type);
      if (!path) return;
      const response = type === "select-gguf"
        ? await api.addModelLibraryGguf(path)
        : await api.scanModelLibrary(path);
      const refreshed = await api.getModelLibrary();
      setModelLibrary(refreshed.models || []);
      const selected = response.model || response.models?.[0];
      if (selected?.path) {
        update({ provider: "llama_cpp", provider_url: "http://127.0.0.1:12345/v1", model: selected.path, model_path: selected.path });
      }
      setProviderStatus(type === "select-gguf" ? "GGUF added to the local library" : `${response.count || 0} GGUF models found`);
    } catch (error) {
      setProviderStatus(error.message);
    }
  };

  const providerAction = async (action) => {
    setProviderBusy(true);
    setProviderStatus(`${action === "test" ? "Testing" : action === "load" ? "Loading" : "Unloading"} local provider...`);
    const endpoint = local.provider_url || (local.provider === "llama_cpp" ? "http://127.0.0.1:12345/v1" : local.lm_studio_url);
    const payload = {
      provider: local.provider,
      endpoint,
      model: local.provider === "llama_cpp" ? (local.model_path || local.model) : local.model,
      options: local.provider === "llama_cpp" ? {
        context_length: optionalNumber(local.llama_context_length),
        gpu_layers: optionalNumber(local.llama_gpu_layers),
        threads: optionalNumber(local.llama_threads),
        batch_size: optionalNumber(local.llama_batch_size),
        parallel_slots: optionalNumber(local.llama_parallel_slots),
        flash_attention: local.llama_flash_attention,
      } : {},
    };
    try {
      await saveCurrentRef.current();
      const result = action === "load"
        ? await api.loadModelProvider(payload)
        : action === "unload"
          ? await api.unloadModelProvider(payload)
          : await api.testModelProvider(payload);
      if (result.ok === false) throw new Error(result.error || "The provider could not complete this operation.");
      setProviderStatus(action === "test" ? `Ready in ${Math.round(result.latency_ms || 0)} ms: ${result.text || "test passed"}` : `${local.provider === "llama_cpp" ? "Built-in llama.cpp" : "Provider"} ${action === "load" ? "loaded" : "unloaded"}`);
      await handleRefreshModels().catch(() => {});
    } catch (error) {
      setProviderStatus(error.message);
    } finally {
      setProviderBusy(false);
    }
  };

  const updateTask = (patch) => setTaskDraft((current) => ({ ...current, ...patch }));
  const selectedLength = resolveLengthInfo(local);
  const recommendedMaxTokens = recommendedMaxTokensForLength(selectedLength);
  const configuredMaxTokens = Number(local.max_tokens) || 0;
  const effectiveMaxTokens = Math.max(configuredMaxTokens, recommendedMaxTokens);
  const maxTokensBelowRecommended = configuredMaxTokens > 0 && configuredMaxTokens < recommendedMaxTokens;
  const systemPromptTokenEstimate = estimateTokens(local.system_prompt);
  const loadedModelSummary = models.length
    ? models.map((model) => model.name || model.id).join(", ")
    : "No loaded models returned yet";
  const selectableModels = local.provider === "llama_cpp"
    ? modelLibrary.map((item) => ({ ...item, id: item.path, name: item.name }))
    : models;
  const taskRoutingRows = useMemo(
    () =>
      taskTypes.map((task) => {
        const resolved = taskModelResolved[task.id] || {};
        return {
          id: task.id,
          label: task.label,
          model: displayModelName(models, resolved.model),
          overrideModel: taskModelProfiles[task.id]?.model || "Global model",
          backend: resolved.inference_backend === "native_rest" ? "Native REST" : "OpenAI-compatible",
          reasoning: resolved.reasoning_mode || "auto",
          source: resolved.uses_global_model ? "global fallback" : "task override",
          notes: resolved.notes ? "notes set" : "default notes",
        };
      }),
    [models, taskModelProfiles, taskModelResolved, taskTypes],
  );
  const taskOverrideCount = taskRoutingRows.filter((task) => task.source === "task override").length;

  const copyGlobalToTask = () => {
    setTaskDraft((current) => ({
      ...current,
      lm_studio_url: local.lm_studio_url || "",
      provider: local.provider || "",
      provider_url: local.provider_url || "",
      model: local.model || "",
      temperature: local.temperature,
      top_p: local.top_p,
      max_tokens: local.max_tokens,
      seed: local.seed ?? "",
      top_k: local.top_k,
      min_p: local.min_p,
      repeat_penalty: local.repeat_penalty,
      presence_penalty: local.presence_penalty,
      frequency_penalty: local.frequency_penalty,
      streaming: String(Boolean(local.streaming)),
      inference_backend: local.inference_backend || "openai_compatible",
      reasoning_mode: local.reasoning_mode || "auto",
      context_length: local.context_length ?? "",
      fallback_to_openai_compatible: String(Boolean(local.fallback_to_openai_compatible)),
    }));
    setTaskStatus("Copied current global settings into this task draft. Click Save Task to apply.");
  };

  const saveTask = async () => {
    try {
      await saveTaskModelProfile(selectedTaskType, taskProfilePayload(taskDraft));
      setTaskStatus("Task profile saved.");
    } catch (error) {
      setTaskStatus("Task profile save failed.");
    }
  };

  const resetTaskById = async (taskType) => {
    try {
      await resetTaskModelProfile(taskType);
      setTaskStatus(`${taskTypes.find((task) => task.id === taskType)?.label || "Task"} reset to global fallback.`);
    } catch (error) {
      setTaskStatus("Task profile reset failed.");
    }
  };

  const resetTask = async () => resetTaskById(selectedTaskType);

  const resetAllTasks = async () => {
    setTaskStatus("Resetting all tasks to global fallback...");
    try {
      for (const task of taskTypes) {
        await resetTaskModelProfile(task.id);
      }
      await fetchTaskModelProfiles();
      setTaskStatus("All task profiles now use global fallback settings.");
    } catch (error) {
      setTaskStatus("Could not reset every task profile.");
    }
  };

  const useGlobalModelForEveryTask = async () => {
    setTaskStatus("Routing every task through the global model...");
    try {
      for (const task of taskTypes) {
        const profile = normalizeTaskDraft(taskModelProfiles[task.id], task.id, taskTypes);
        await saveTaskModelProfile(task.id, {
          ...taskProfilePayload(profile),
          model: null,
        });
      }
      await fetchTaskModelProfiles();
      setTaskStatus("Every task now inherits the selected global model.");
    } catch (error) {
      setTaskStatus("Could not route every task through the global model.");
    }
  };

  const resetWritingTaskNotes = async (label, proseNotes, rewriteNotes = null) => {
    setTaskStatus(`Resetting writing task notes to ${label}...`);
    try {
      const updates = [["prose_generation", proseNotes]];
      if (rewriteNotes) {
        updates.push(["rewrite_revision", rewriteNotes]);
      }
      for (const [taskType, notes] of updates) {
        const profile = normalizeTaskDraft(taskModelProfiles[taskType], taskType, taskTypes);
        await saveTaskModelProfile(taskType, {
          ...taskProfilePayload(profile),
          notes,
        });
      }
      await fetchTaskModelProfiles();
      const selectedUpdate = updates.find(([taskType]) => taskType === selectedTaskType);
      if (selectedUpdate) {
        setTaskDraft((current) => ({ ...current, notes: selectedUpdate[1] }));
      }
      setTaskStatus(`Writing task notes reset to ${label}.`);
    } catch (error) {
      setTaskStatus(`Could not reset writing task notes to ${label}.`);
      throw error;
    }
  };

  const useStoryDriverProseV3Prompt = async () => {
    const nextSettings = {
      ...local,
      active_preset_id: null,
      system_prompt: storyDriverProseV3SystemPrompt,
    };
    setStatus("Applying StoryDriver Prose v3...");
    setLocal(nextSettings);
    try {
      await saveModelSettings(normalizeModelSettingsForSave(nextSettings));
      await resetWritingTaskNotes(
        "StoryDriver Prose v3",
        storyDriverProseV3TaskNotes,
        storyDriverProseV3RewriteTaskNotes,
      );
      setStatus("StoryDriver Prose v3 applied. Previous presets were not overwritten.");
    } catch (error) {
      setStatus("Could not apply StoryDriver Prose v3.");
    }
  };

  const keepCurrentSystemPrompt = () => {
    setStatus("Current system prompt preserved.");
  };

  const resetProseTaskNotesToV3 = async () => {
    try {
      await resetWritingTaskNotes(
        "StoryDriver Prose v3",
        storyDriverProseV3TaskNotes,
        storyDriverProseV3RewriteTaskNotes,
      );
    } catch (error) {
      // resetWritingTaskNotes already reports the failure in the task status line.
    }
  };

  const refreshProsePromptPreview = async () => {
    const previewTask =
      previewMode === "continue" ? "prose_generation" : "rewrite_revision";
    const taskNotesOverride = selectedTaskType === previewTask ? taskDraft.notes : null;
    await fetchProsePromptPreview(activeSessionId, {
      director_note: previewDirectorNote,
      mode: previewMode,
      system_prompt_override: local.system_prompt,
      task_notes_override: taskNotesOverride,
      prose_prompt_mode_override: "standard",
      app_planning_enabled_override: true,
      chapter_extension_enabled_override: local.chapter_extension_enabled !== false,
      adherence_check_mode_override: local.adherence_check_mode || "warn",
      writing_length_mode_override: local.writing_length_mode || "scene",
      custom_word_min_override: optionalNumber(local.custom_word_min),
      custom_word_max_override: optionalNumber(local.custom_word_max),
      max_tokens_override: optionalNumber(local.max_tokens),
    });
  };

  const selectedTaskInfo = taskTypes.find((item) => item.id === selectedTaskType) || taskTypes[0];
  const resolvedTask = taskModelResolved[selectedTaskType] || null;
  const previewDiagnostics = prosePromptPreview?.prompt_diagnostics || {};
  const previewGenreMatches = prosePromptPreview?.genre_time_helper?.matches || [];

  return (
    <motion.div
      animate={{ opacity: 1 }}
      className={embedded ? "min-w-0" : "fixed inset-0 z-50 grid place-items-center bg-black/62 p-3 backdrop-blur-sm"}
      exit={{ opacity: 0 }}
      initial={{ opacity: 0 }}
    >
      <motion.div
        animate={{ opacity: 1, scale: 1, y: 0 }}
        className={embedded ? "flex min-w-0 flex-col" : "sd-modal flex max-h-[calc(100dvh-24px)] w-full max-w-3xl flex-col rounded-lg border border-line bg-panel shadow-glow"}
        exit={{ opacity: 0, scale: 0.98, y: 12 }}
        initial={{ opacity: 0, scale: 0.98, y: 12 }}
        transition={{ type: "spring", stiffness: 340, damping: 30 }}
      >
        <div className="flex shrink-0 items-center justify-between gap-4 border-b border-line p-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <SlidersHorizontal size={18} className="text-tide" />
              <h2 className="text-base font-semibold text-zinc-100">Writing settings</h2>
            </div>
            <p className="mt-1 text-xs text-muted">{isLoadingSettings ? "Loading..." : status}</p>
          </div>
          {!embedded ? <button
            aria-label="Close model settings"
            className={iconButton}
            onClick={closeAfterSave}
            type="button"
          >
            <X size={17} />
          </button> : null}
        </div>

        <div className="shrink-0 border-b border-line px-4 py-3">
          <div aria-label="Model settings view" className="grid grid-cols-2 overflow-hidden rounded-lg border border-line" role="group">
            <button className={`min-h-10 text-sm font-medium ${settingsView === "basic" ? "bg-tide/15 text-tide" : "bg-panelSoft text-zinc-300"}`} onClick={() => setSettingsView("basic")} type="button">Writing</button>
            <button className={`min-h-10 border-l border-line text-sm font-medium ${settingsView === "advanced" ? "bg-tide/15 text-tide" : "bg-panelSoft text-zinc-300"}`} onClick={() => setSettingsView("advanced")} type="button">Advanced</button>
          </div>
        </div>

        <div className="story-scrollbar flex min-h-0 flex-1 flex-col overflow-y-auto px-4">
          {settingsView === "basic" ? <>
          <Section defaultOpen={false} title="Preset">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,220px)_auto_auto_auto]">
              <select
                className={inputClass}
                onChange={(event) => loadPreset(event.target.value)}
                value={local.active_preset_id || ""}
              >
                <option value="">Unsaved current settings</option>
                {modelPresets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.name}
                  </option>
                ))}
              </select>
              <input
                aria-label="Preset name"
                className={inputClass}
                onChange={(event) => setPresetName(event.target.value)}
                placeholder="Preset name"
                value={presetName}
              />
              <button className={iconButton} onClick={savePreset} title="Save preset" type="button">
                <Save size={16} />
              </button>
              <button
                className={iconButton}
                disabled={!activePreset}
                onClick={renamePreset}
                title="Rename preset"
                type="button"
              >
                <RotateCcw size={16} />
              </button>
              <button
                className={`${iconButton} text-red-300 disabled:opacity-40`}
                disabled={!activePreset}
                onClick={removePreset}
                title="Delete preset"
                type="button"
              >
                <Trash2 size={16} />
              </button>
            </div>
            <p className="mt-3 rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
              Presets preserve provider, model, prompts, task notes, sampling, length, and context budgets. Narration, appearance, private data, and model files stay separate.
            </p>
          </Section>

          <Section defaultOpen={false} title="System Prompt">
            <p className="mb-3 rounded-lg border border-tide/20 bg-tide/10 px-3 py-2 text-xs leading-5 text-zinc-300">
              Main creative authority for prose generation. StoryDriver does not cap its length; the selected model's context window remains the practical limit. Story State supplies factual continuity and does not overrule this prompt or the submitted director note.
            </p>
            <div className="mb-3 rounded-lg border border-line bg-[#0d0e11] p-3 text-xs leading-5 text-muted">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className="font-medium text-zinc-200">StoryDriver Prose v3 — Vivid Character-Driven</div>
                  <div>
                    Vivid openings, grounded character introductions, interiority, agency, relationship tension, spatial continuity, genre diction, and direct adult-only fiction when requested.
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button className={textButton} onClick={useStoryDriverProseV3Prompt} type="button">
                    Use Prose v3
                  </button>
                  <button className={textButton} onClick={resetProseTaskNotesToV3} type="button">
                    Reset v3 Notes
                  </button>
                </div>
              </div>
            </div>
            <textarea
              className={`${inputClass} min-h-48 resize-y leading-6`}
              onChange={(event) => update({ system_prompt: event.target.value })}
              value={local.system_prompt}
            />
            <div className="mt-2 flex items-center justify-between gap-3 text-xs text-muted">
              <span>Estimated {systemPromptTokenEstimate.toLocaleString()} tokens</span>
              <span>{local.system_prompt.length.toLocaleString()} chars</span>
            </div>
          </Section>

          <Section title="Model Connection">
            <div className="grid gap-3 sm:grid-cols-2">
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Provider" />
                <select
                  className={inputClass}
                  onChange={(event) => {
                    const provider = event.target.value;
                    const providerUrl = provider === "llama_cpp"
                      ? "http://127.0.0.1:12345/v1"
                      : provider === "lm_studio"
                        ? local.lm_studio_url || "http://127.0.0.1:1234/v1"
                        : local.provider_url || "http://127.0.0.1:1234/v1";
                    update({ provider, provider_url: providerUrl, model: "", model_path: null, active_preset_id: null });
                  }}
                  value={local.provider || "lm_studio"}
                >
                  {(providers.length ? providers : [
                    { id: "llama_cpp", name: "Built-in llama.cpp" },
                    { id: "openai_compatible", name: "Local OpenAI-compatible" },
                    { id: "lm_studio", name: "LM Studio" },
                  ]).map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
                </select>
              </label>
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Local Endpoint" />
                <input
                  className={inputClass}
                  disabled={local.provider === "llama_cpp"}
                  onChange={(event) => update({
                    provider_url: event.target.value,
                    ...(local.provider === "lm_studio" ? { lm_studio_url: event.target.value } : {}),
                  })}
                  value={local.provider_url || local.lm_studio_url}
                />
              </label>
            </div>

            {local.provider === "llama_cpp" ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <button className={textButton} onClick={() => addModelPath("select-gguf")} type="button">Add GGUF</button>
                <button className={textButton} onClick={() => addModelPath("select-model-folder")} type="button">Scan folder</button>
                <span className="self-center text-xs text-muted">{modelLibrary.length} local model{modelLibrary.length === 1 ? "" : "s"}</span>
              </div>
            ) : null}

            <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Selected Model" />
                {selectableModels.length ? (
                  <select
                    className={inputClass}
                    onChange={(event) => update({ model: event.target.value, model_path: local.provider === "llama_cpp" ? event.target.value : null })}
                    value={local.provider === "llama_cpp" ? (local.model_path || local.model) : local.model}
                  >
                    <option value="">{local.provider === "llama_cpp" ? "Choose a GGUF model" : "Auto-select first loaded model"}</option>
                    {selectableModels.map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.name || model.id}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    className={inputClass}
                    onChange={(event) => update({ model: event.target.value, model_path: local.provider === "llama_cpp" ? event.target.value : null })}
                    placeholder={local.provider === "llama_cpp" ? "Full path to a local .gguf file" : "Refresh models or type model ID"}
                    value={local.model}
                  />
                )}
              </label>
              <button
                className="sd-action-button mt-5 inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-line bg-panelSoft px-3 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 disabled:cursor-wait disabled:opacity-60"
                disabled={isRefreshingModels}
                onClick={handleRefreshModels}
                type="button"
              >
                <RefreshCw className={isRefreshingModels ? "animate-spin" : ""} size={16} />
                {isRefreshingModels ? "Refreshing" : "Refresh"}
              </button>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {local.provider !== "openai_compatible" ? <button className={textButton} disabled={providerBusy || !local.model} onClick={() => providerAction("load")} type="button">Load</button> : null}
              {local.provider !== "openai_compatible" ? <button className={textButton} disabled={providerBusy} onClick={() => providerAction("unload")} type="button">Unload</button> : null}
              <button className={textButton} disabled={providerBusy || !local.model} onClick={() => providerAction("test")} type="button">Test</button>
              <span className="safe-wrap text-xs text-muted">{providerStatus}</span>
            </div>
            <p className="mt-3 safe-wrap text-xs leading-5 text-muted">Available: {loadedModelSummary}</p>

            {local.provider === "llama_cpp" ? (
              <details className="mt-3 rounded-lg border border-line bg-[#0d0e11] p-3">
                <summary className="cursor-pointer text-sm font-semibold text-zinc-100">llama.cpp runtime controls</summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  {[
                    ["Context", "llama_context_length", 2048, 131072, 1024],
                    ["GPU Layers", "llama_gpu_layers", -1, 999, 1],
                    ["Threads", "llama_threads", 1, 256, 1],
                    ["Batch", "llama_batch_size", 32, 8192, 32],
                    ["Parallel Slots", "llama_parallel_slots", 1, 8, 1],
                  ].map(([label, key, min, max, step]) => (
                    <label key={key}>
                      <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name={label} />
                      <input className={inputClass} min={min} max={max} step={step} type="number" value={local[key] ?? ""} placeholder="Runtime default" onChange={(event) => update({ [key]: event.target.value })} />
                    </label>
                  ))}
                  <label>
                    <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Flash Attention" />
                    <select className={inputClass} value={local.llama_flash_attention == null ? "auto" : String(local.llama_flash_attention)} onChange={(event) => update({ llama_flash_attention: event.target.value === "auto" ? null : event.target.value === "true" })}>
                      <option value="auto">Auto</option><option value="true">On</option><option value="false">Off</option>
                    </select>
                  </label>
                </div>
              </details>
            ) : null}

            {local.provider === "lm_studio" ? <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Inference Backend" />
                <select
                  className={inputClass}
                  disabled={local.provider !== "lm_studio"}
                  onChange={(event) => update({ inference_backend: event.target.value })}
                  value={local.provider === "lm_studio" ? (local.inference_backend || "openai_compatible") : "openai_compatible"}
                >
                  <option value="openai_compatible">OpenAI-compatible</option>
                  <option value="native_rest">Native REST Chat</option>
                </select>
              </label>
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Reasoning Mode" />
                <select
                  className={inputClass}
                  disabled={local.provider !== "lm_studio"}
                  onChange={(event) => update({ reasoning_mode: event.target.value })}
                  value={local.reasoning_mode || "auto"}
                >
                  <option value="auto">Auto / default</option>
                  <option value="off">Off</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="on">On</option>
                </select>
              </label>
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Request Context" />
                <input
                  className={inputClass}
                  min="512"
                  onChange={(event) => update({ context_length: event.target.value })}
                  placeholder="Provider default"
                  step="1024"
                  type="number"
                  value={local.context_length ?? ""}
                />
              </label>
            </div> : null}
            <p className="mt-3 text-xs leading-5 text-muted">
              StoryDriver accepts loopback and private-LAN endpoints only. Built-in llama.cpp uses the bundled Vulkan runtime and never downloads a model automatically.
            </p>
          </Section>
          </> : null}

          {settingsView === "advanced" ? (
          <Section defaultOpen={false} title="Per-task model overrides">
            <div className="grid gap-4">
              <p className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
                {taskOverrideCount
                  ? `${taskOverrideCount} task override${taskOverrideCount === 1 ? "" : "s"} active.`
                  : "All tasks use the global model."} Every writing action still uses the definitive deliberate pipeline.
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  className={textButton}
                  onClick={useGlobalModelForEveryTask}
                  type="button"
                >
                  Use global model for every task
                </button>
                <button
                  className={textButton}
                  onClick={resetAllTasks}
                  type="button"
                >
                  Clear all overrides
                </button>
              </div>
              <div className="grid gap-2">
                <div className="hidden grid-cols-[minmax(0,150px)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-2 px-3 text-[11px] font-medium uppercase text-muted sm:grid">
                  <span>Task</span>
                  <span>Model override</span>
                  <span>Effective route</span>
                  <span>Reset</span>
                </div>
                {taskRoutingRows.map((task) => (
                    <div
                      className="grid min-w-0 gap-2 rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted sm:grid-cols-[minmax(0,150px)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-center"
                      key={task.id}
                    >
                      <span className="font-medium text-zinc-300">{task.label}</span>
                      <span className="safe-wrap">{task.overrideModel}</span>
                      <span className="safe-wrap">{task.model} · {task.source}</span>
                      <button className={textButton} onClick={() => resetTaskById(task.id)} type="button">Reset</button>
                    </div>
                  ))}
              </div>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,240px)_minmax(0,1fr)]">
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Task" />
                  <select
                    className={inputClass}
                    onChange={(event) => setSelectedTaskType(event.target.value)}
                    value={selectedTaskType}
                  >
                    {taskTypes.map((task) => (
                      <option key={task.id} value={task.id}>
                        {task.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
                  <div className="font-medium text-zinc-300">{selectedTaskInfo?.label || "Task"}</div>
                  <div>{selectedTaskInfo?.description || "Uses global settings unless a field is set."}</div>
                  {resolvedTask ? (
                    <div className="mt-1 text-zinc-400">
                      Effective: {resolvedTask.provider || local.provider} · {resolvedTask.model || "auto-select first loaded model"} · {resolvedTask.inference_backend || "openai_compatible"} · reasoning {resolvedTask.reasoning_mode || "auto"} · {resolvedTask.uses_global_model ? "global fallback" : "task override"} · {resolvedTask.timeout_seconds}s timeout
                      {resolvedTask.override_fields?.length ? ` · overrides: ${resolvedTask.override_fields.join(", ")}` : ""}
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Task Provider" />
                  <select className={inputClass} value={taskDraft.provider} onChange={(event) => updateTask({ provider: event.target.value })}>
                    <option value="">Use global provider</option>
                    <option value="llama_cpp">Built-in llama.cpp</option>
                    <option value="openai_compatible">Local OpenAI-compatible</option>
                    <option value="lm_studio">LM Studio</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Task Endpoint" />
                  <input
                    className={inputClass}
                    onChange={(event) => updateTask({ provider_url: event.target.value, lm_studio_url: taskDraft.provider === "lm_studio" ? event.target.value : taskDraft.lm_studio_url })}
                    placeholder={`Global: ${local.provider_url}`}
                    value={taskDraft.provider_url}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Task Model" />
                  {selectableModels.length ? (
                    <select
                      className={inputClass}
                      onChange={(event) => updateTask({ model: event.target.value })}
                      value={taskDraft.model}
                    >
                      <option value="">Use global model</option>
                      {selectableModels.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.name || model.id}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className={inputClass}
                      onChange={(event) => updateTask({ model: event.target.value })}
                      placeholder="Use global model or type model id"
                      value={taskDraft.model}
                    />
                  )}
                </label>
              </div>

              <div className="grid gap-3 sm:grid-cols-4">
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Task Backend" />
                  <select
                    className={inputClass}
                    onChange={(event) => updateTask({ inference_backend: event.target.value })}
                    value={taskDraft.inference_backend}
                  >
                    <option value="">Use global</option>
                    <option value="openai_compatible">OpenAI-compatible</option>
                    <option value="native_rest">Native REST Chat</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Reasoning Mode" />
                  <select
                    className={inputClass}
                    onChange={(event) => updateTask({ reasoning_mode: event.target.value })}
                    value={taskDraft.reasoning_mode}
                  >
                    <option value="">Use global</option>
                    <option value="auto">Auto / default</option>
                    <option value="off">Off</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="on">On</option>
                  </select>
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Context Length" />
                  <input
                    className={inputClass}
                    min="512"
                    onChange={(event) => updateTask({ context_length: event.target.value })}
                    placeholder="Use global"
                    step="1024"
                    type="number"
                    value={taskDraft.context_length}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Native Fallback" />
                  <select
                    className={inputClass}
                    onChange={(event) => updateTask({ fallback_to_openai_compatible: event.target.value })}
                    value={taskDraft.fallback_to_openai_compatible}
                  >
                    <option value="">Use global</option>
                    <option value="true">Fallback to OpenAI</option>
                    <option value="false">Fail fast</option>
                  </select>
                </label>
              </div>

              <div className="grid gap-3 sm:grid-cols-4">
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Timeout Seconds" />
                  <input
                    className={inputClass}
                    min="5"
                    onChange={(event) => updateTask({ timeout_seconds: event.target.value })}
                    type="number"
                    value={taskDraft.timeout_seconds}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Temperature" />
                  <input
                    className={inputClass}
                    max="2"
                    min="0"
                    onChange={(event) => updateTask({ temperature: event.target.value })}
                    placeholder={`Global: ${local.temperature}`}
                    step="0.05"
                    type="number"
                    value={taskDraft.temperature}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Top P" />
                  <input
                    className={inputClass}
                    max="1"
                    min="0"
                    onChange={(event) => updateTask({ top_p: event.target.value })}
                    placeholder={`Global: ${local.top_p}`}
                    step="0.01"
                    type="number"
                    value={taskDraft.top_p}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Max Tokens" />
                  <input
                    className={inputClass}
                    min="1"
                    onChange={(event) => updateTask({ max_tokens: event.target.value })}
                    placeholder={`Global: ${local.max_tokens}`}
                    step="64"
                    type="number"
                    value={taskDraft.max_tokens}
                  />
                </label>
              </div>

              <details className="rounded-lg border border-line bg-[#0d0e11] p-3">
                <summary className="cursor-pointer text-sm font-semibold text-zinc-100">Advanced task overrides</summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  {[
                    ["Top K", "top_k", 1],
                    ["Min P", "min_p", 0.01],
                    ["Repeat Penalty", "repeat_penalty", 0.05],
                    ["Presence Penalty", "presence_penalty", 0.05],
                    ["Frequency Penalty", "frequency_penalty", 0.05],
                    ["Seed", "seed", 1],
                  ].map(([label, key, step]) => (
                    <label key={key}>
                      <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name={label} />
                      <input
                        className={inputClass}
                        onChange={(event) => updateTask({ [key]: event.target.value })}
                        placeholder="Use global"
                        step={step}
                        type="number"
                        value={taskDraft[key]}
                      />
                    </label>
                  ))}
                  <label>
                    <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Streaming" />
                    <select
                      className={inputClass}
                      onChange={(event) => updateTask({ streaming: event.target.value })}
                      value={taskDraft.streaming}
                    >
                      <option value="">Use global</option>
                      <option value="true">Streaming</option>
                      <option value="false">Non-streaming</option>
                    </select>
                  </label>
                </div>
              </details>

              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Task Notes" />
                <textarea
                  className={`${inputClass} min-h-20 resize-y leading-6`}
                  onChange={(event) => updateTask({ notes: event.target.value })}
                  placeholder="Example: fast JSON model for Story State extraction"
                  value={taskDraft.notes}
                />
              </label>

              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-muted">{taskStatus}</p>
                <div className="flex flex-wrap gap-2">
                  <button className={textButton} onClick={copyGlobalToTask} type="button">
                    Copy Global
                  </button>
                  <button className={textButton} onClick={resetTask} type="button">
                    Reset Task
                  </button>
                  <button className={textButton} onClick={saveTask} type="button">
                    Save Task
                  </button>
                </div>
              </div>
            </div>
          </Section>
          ) : null}

          {settingsView === "basic" ? <>
          <Section defaultOpen={false} title="Writing Length">
            <div className="grid gap-3 sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)]">
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Length Mode" />
                <select
                  className={inputClass}
                  onChange={(event) => update({ writing_length_mode: event.target.value })}
                  value={local.writing_length_mode || "scene"}
                >
                  <option value="beat">Beat</option>
                  <option value="scene">Scene</option>
                  <option value="chapter">Chapter</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
                <div className="font-medium text-zinc-300">
                  {selectedLength.label}: {selectedLength.range}
                </div>
                <div>Estimated narration time: {selectedLength.time}. Director notes that ask for a chapter automatically use Chapter length.</div>
                <div className="mt-1">
                  Effective max token budget: {effectiveMaxTokens.toLocaleString()}.
                  {maxTokensBelowRecommended
                    ? ` Current max_tokens is ${configuredMaxTokens.toLocaleString()}, so StoryDriver raises it for this length request.`
                    : " Current max_tokens is high enough for this length mode."}
                </div>
              </div>
            </div>
            {local.writing_length_mode === "custom" ? (
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Custom Min Words" />
                  <input
                    className={inputClass}
                    min="100"
                    onChange={(event) => update({ custom_word_min: event.target.value })}
                    step="50"
                    type="number"
                    value={local.custom_word_min ?? ""}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Custom Max Words" />
                  <input
                    className={inputClass}
                    min="100"
                    onChange={(event) => update({ custom_word_max: event.target.value })}
                    step="50"
                    type="number"
                    value={local.custom_word_max ?? ""}
                  />
                </label>
              </div>
            ) : null}
            {maxTokensBelowRecommended ? (
              <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs leading-5 text-amber-100">
                {selectedLength.label} output can be cut short if LM Studio ignores the raised token budget. Keep max_tokens near {recommendedMaxTokens.toLocaleString()} or higher for fewer early endings.
              </p>
            ) : null}
            {(local.writing_length_mode || "scene") === "chapter" ? (
              <p className="mt-3 rounded-lg border border-tide/20 bg-tide/10 px-3 py-2 text-xs leading-5 text-zinc-300">
                Chapter length targets 1,800-2,600 words. Chapter Extension can request a continuation pass if the first draft lands short.
              </p>
            ) : null}
          </Section>

          <Section defaultOpen={false} title="Prose Prompt Preview">
            <div className="grid gap-3">
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px_auto]">
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Preview Director Note" />
                  <input
                    className={inputClass}
                    onChange={(event) => setPreviewDirectorNote(event.target.value)}
                    value={previewDirectorNote}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Preview Mode" />
                  <select
                    className={inputClass}
                    onChange={(event) => setPreviewMode(event.target.value)}
                    value={previewMode}
                  >
                    <option value="continue">Continue</option>
                    <option value="regenerate">Regenerate</option>
                    <option value="rewrite">Rewrite</option>
                    <option value="revise">Revise</option>
                  </select>
                </label>
                <button
                  className={`${textButton} mt-5`}
                  disabled={isLoadingProsePromptPreview}
                  onClick={refreshProsePromptPreview}
                  type="button"
                >
                  {isLoadingProsePromptPreview ? "Previewing" : "Refresh Preview"}
                </button>
              </div>
              <p className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
                Preview uses the current modal values without calling LM Studio or saving a scene. The system prompt remains the creative authority; Story State supplies factual continuity.
              </p>
              {prosePromptPreviewError ? (
                <p className="rounded-lg border border-ember/30 bg-ember/10 px-3 py-2 text-xs leading-5 text-ember">
                  {prosePromptPreviewError}
                </p>
              ) : null}
              {prosePromptPreview ? (
                <div className="grid gap-3">
                  <div className="grid gap-2 text-xs leading-5 text-muted sm:grid-cols-2 lg:grid-cols-5">
                    <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2">
                      <div className="font-medium text-zinc-300">Model</div>
                      <div className="safe-wrap">{displayModelName(models, prosePromptPreview.model)}</div>
                      <div>{prosePromptPreview.inference_backend === "native_rest" ? "Native REST" : "OpenAI-compatible"} · reasoning {prosePromptPreview.reasoning_mode}</div>
                    </div>
                    <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2">
                      <div className="font-medium text-zinc-300">Length</div>
                      <div>{prosePromptPreview.writing_length?.label}: {prosePromptPreview.writing_length?.min_words}-{prosePromptPreview.writing_length?.max_words} words</div>
                      <div>max_tokens {Number(prosePromptPreview.parameters?.max_tokens || 0).toLocaleString()}</div>
                    </div>
                    <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2">
                      <div className="font-medium text-zinc-300">Prompt Size</div>
                      <div>{Number(previewDiagnostics.prompt_estimated_tokens || 0).toLocaleString()} est. tokens</div>
                      <div>{Number(previewDiagnostics.story_state_chars || 0).toLocaleString()} state chars · {Number(previewDiagnostics.recent_scenes_chars || 0).toLocaleString()} recent chars</div>
                    </div>
                    <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2">
                      <div className="font-medium text-zinc-300">Process</div>
                      <div>Deliberate Pipeline</div>
                      <div>Structured planning and review always on</div>
                      <div>Adherence {adherenceCheckModes[prosePromptPreview.adherence_check_mode] || prosePromptPreview.adherence_check_mode || "Warn"}</div>
                    </div>
                    <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2">
                      <div className="font-medium text-zinc-300">Genre Diction</div>
                      <div>{previewGenreMatches.map((item) => item.label).join(", ") || "Story-specific"}</div>
                      <div>Visible helper</div>
                    </div>
                  </div>
                  {prosePromptPreview.notes?.length ? (
                    <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
                      {prosePromptPreview.notes.join(" ")}
                    </div>
                  ) : null}
                  <details className="rounded-lg border border-line bg-[#0d0e11] p-3">
                    <summary className="cursor-pointer text-sm font-semibold text-zinc-100">System prompt</summary>
                    <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap text-xs leading-5 text-zinc-300">
                      {prosePromptPreview.system_prompt}
                    </pre>
                  </details>
                  <details className="rounded-lg border border-line bg-[#0d0e11] p-3">
                    <summary className="cursor-pointer text-sm font-semibold text-zinc-100">Prose task notes</summary>
                    <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap text-xs leading-5 text-zinc-300">
                      {prosePromptPreview.task_notes || "No prose task notes set."}
                    </pre>
                  </details>
                  <details className="rounded-lg border border-line bg-[#0d0e11] p-3">
                    <summary className="cursor-pointer text-sm font-semibold text-zinc-100">Genre/time-period diction guide</summary>
                    <div className="mt-3 space-y-2 text-xs leading-5 text-zinc-300">
                      <p className="text-muted">
                        {prosePromptPreview.genre_time_helper?.note ||
                          "Derived from visible story, world, recent passage, and director-note context."}
                      </p>
                      {previewGenreMatches.map((item) => (
                        <div key={item.id} className="rounded-md border border-line/70 bg-black/20 px-3 py-2">
                          <div className="font-medium text-zinc-200">{item.label}</div>
                          <div className="text-muted">{item.guidance}</div>
                        </div>
                      ))}
                    </div>
                  </details>
                  {prosePromptPreview.scene_plan ? (
                    <details className="rounded-lg border border-line bg-[#0d0e11] p-3">
                      <summary className="cursor-pointer text-sm font-semibold text-zinc-100">Visible writing plan</summary>
                      <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap text-xs leading-5 text-zinc-300">
                        {JSON.stringify(prosePromptPreview.scene_plan, null, 2)}
                      </pre>
                    </details>
                  ) : null}
                  <details className="rounded-lg border border-line bg-[#0d0e11] p-3">
                    <summary className="cursor-pointer text-sm font-semibold text-zinc-100">Story State, active characters, recent scenes, and director note</summary>
                    <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-5 text-zinc-300">
                      {prosePromptPreview.user_prompt}
                    </pre>
                  </details>
                </div>
              ) : null}
            </div>
          </Section>

          <Section defaultOpen={false} title="Core Settings">
            <div className="space-y-4">
              <SliderRow
                label="Temperature"
                max={2}
                min={0}
                onChange={(value) => update({ temperature: value })}
                step={0.05}
                value={local.temperature}
              />
              <SliderRow
                label="Top P"
                max={1}
                min={0}
                onChange={(value) => update({ top_p: value })}
                step={0.01}
                value={local.top_p}
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Max Tokens" />
                  <input
                    className={inputClass}
                    min="128"
                    onChange={(event) => update({ max_tokens: Number(event.target.value) })}
                    step="64"
                    type="number"
                    value={local.max_tokens}
                  />
                </label>
                <label>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Stop Strings" />
                  <input
                    className={inputClass}
                    onChange={(event) => update({ stop_strings: event.target.value })}
                    placeholder="e.g. END_SCENE"
                    value={local.stop_strings}
                  />
                </label>
              </div>
              <label className="flex min-h-11 items-center justify-between rounded-lg border border-line bg-[#0d0e11] px-3 py-2">
                <SettingLabel className="text-sm text-zinc-300" name="Streaming" />
                <input checked={local.streaming} className="h-4 w-4 accent-tide" onChange={(event) => update({ streaming: event.target.checked })} type="checkbox" />
              </label>
            </div>
          </Section>
          </> : null}

          {settingsView === "advanced" ? (
          <Section defaultOpen={false} title="Sampling / Advanced">
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ["Top K", "top_k", 1],
                ["Min P", "min_p", 0.01],
                ["Repeat Penalty", "repeat_penalty", 0.05],
                ["Presence Penalty", "presence_penalty", 0.05],
                ["Frequency Penalty", "frequency_penalty", 0.05],
              ].map(([label, key, step]) => (
                <label key={key}>
                  <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name={label} />
                  <input
                    className={inputClass}
                    onChange={(event) => update({ [key]: Number(event.target.value) })}
                    step={step}
                    type="number"
                    value={local[key]}
                  />
                </label>
              ))}
              <label>
                <SettingLabel className="mb-1.5 text-xs font-medium text-muted" name="Seed" />
                <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                  <input
                    className={inputClass}
                    onChange={(event) => update({ seed: event.target.value })}
                    placeholder="Random"
                    type="number"
                    value={local.seed ?? ""}
                  />
                  <button aria-label="Randomize seed" className={iconButton} onClick={() => update({ seed: Math.floor(Math.random() * 2147483647) })} title="Randomize seed" type="button">
                    <RotateCcw size={15} />
                  </button>
                </div>
              </label>
            </div>
          </Section>
          ) : null}
        </div>
      </motion.div>
    </motion.div>
  );
}
