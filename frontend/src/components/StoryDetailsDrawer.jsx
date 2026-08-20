import { AnimatePresence, motion } from "framer-motion";
import {
  BookOpen,
  BrainCircuit,
  Check,
  ChevronDown,
  Globe2,
  Link2,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  Unlink,
  Unlock,
  UserRound,
  UsersRound,
  Volume2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useAppStore } from "../store/useAppStore.js";
import ConfirmDialog from "./ConfirmDialog.jsx";

const visualProfileDefaults = {
  base_visual_description: "",
  face_description: "",
  hair: "",
  body_build: "",
  age_marker: "",
  default_outfit: "",
  distinctive_marks: "",
  color_palette: "",
  preferred_voice: "",
};

const characterDefaults = {
  name: "",
  role: "",
  personality: "",
  appearance: "",
  relationships: "",
  current_state: "",
  voice: "",
  private_notes: "",
  visual_profile: visualProfileDefaults,
};

const worldDefaults = {
  setting: "",
  tone: "",
  rules: "",
  locations: "",
  factions: "",
  conflicts: "",
  history: "",
};

const characterGroups = [
  {
    title: "Core",
    fields: [
      ["name", "Name", "input"],
      ["role", "Role", "input"],
      ["personality", "Personality", "textarea"],
      ["appearance", "Appearance (Brief Summary Only)", "textarea"],
    ],
  },
  {
    title: "Story State",
    fields: [
      ["relationships", "Relationships", "textarea"],
      ["current_state", "Current State", "textarea"],
    ],
  },
  {
    title: "Voice",
    fields: [["voice", "Voice", "input"]],
  },
  {
    title: "Private Notes",
    fields: [["private_notes", "Private Notes", "textarea"]],
  },
];
const characterPayloadFields = [
  "name",
  "role",
  "personality",
  "appearance",
  "relationships",
  "current_state",
  "voice",
  "private_notes",
];

const visualProfileFields = [
  ["base_visual_description", "Stable Appearance", "textarea"],
  ["face_description", "Face Description", "textarea"],
  ["hair", "Hair", "input"],
  ["body_build", "Body / Build", "input"],
  ["age_marker", "Age / Adult Marker", "input"],
  ["default_outfit", "Default Outfit", "textarea"],
  ["distinctive_marks", "Distinctive Marks", "textarea"],
  ["color_palette", "Color Palette", "input"],
  ["preferred_voice", "Preferred Voice", "input"],
];

const worldFields = [
  ["setting", "Setting"],
  ["tone", "Tone"],
  ["rules", "Rules"],
  ["locations", "Locations"],
  ["factions", "Factions"],
  ["conflicts", "Conflicts"],
  ["history", "History"],
];

const qualityNoteTypes = ["writing", "state", "character", "tts", "ui", "speed", "other"];
const qualitySeverities = ["low", "medium", "high"];
const qualityStatuses = ["open", "fixed", "ignored"];

const fieldClass =
  "sd-field w-full rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-sm text-zinc-100 placeholder:text-muted";
const labelClass = "mb-1.5 block text-xs font-medium uppercase tracking-wide text-muted";
const iconButton =
  "sd-icon-button grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line bg-panelSoft text-zinc-200 transition hover:border-zinc-600 disabled:cursor-not-allowed disabled:opacity-45";
const textButton =
  "sd-action-button inline-flex min-h-9 items-center justify-center gap-2 rounded-lg border border-line bg-panelSoft px-3 text-sm font-medium text-zinc-200 transition hover:border-zinc-600 disabled:cursor-not-allowed disabled:opacity-45";
const EMPTY_ARRAY = Object.freeze([]);

const cleanPayload = (values) =>
  Object.fromEntries(Object.entries(values).map(([key, value]) => [key, String(value || "").trim()]));

const pronunciationEntriesToText = (entries = [], storyId = null) =>
  (entries || [])
    .filter((entry) => {
      if (!entry?.written_form || !entry?.spoken_form || entry.enabled === false) return false;
      if (!storyId) return entry.scope !== "story";
      return entry.scope === "story" && entry.story_id === storyId;
    })
    .map((entry) => `${entry.written_form} => ${entry.spoken_form}`)
    .join("\n");

const parseStoryPronunciationEntries = (value = "", storyId) =>
  String(value || "")
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
        id: `story-${storyId}-${written.toLowerCase()}-${spoken.toLowerCase()}`
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "")
          .slice(0, 80),
        written_form: written,
        spoken_form: spoken,
        scope: "story",
        story_id: storyId,
        enabled: true,
        notes: "",
      };
    })
    .filter(Boolean);

const mergeStoryPronunciationEntries = (entries = [], storyId, draft = "") => [
  ...(entries || []).filter((entry) => !(entry?.scope === "story" && entry?.story_id === storyId)),
  ...parseStoryPronunciationEntries(draft, storyId),
];

const characterPayload = (values) => {
  const base = Object.fromEntries(
    characterPayloadFields.map((key) => [key, String(values[key] || "").trim()]),
  );
  base.visual_profile = Object.fromEntries(
    Object.entries({ ...visualProfileDefaults, ...(values.visual_profile || {}) })
      .filter(([key]) => Object.prototype.hasOwnProperty.call(visualProfileDefaults, key))
      .map(([key, value]) => [key, String(value || "").trim()]),
  );
  return base;
};

const hasWorldNotes = (worldNotes) =>
  worldFields.some(([field]) => Boolean(worldNotes?.[field]?.trim()));

const hasStoryFoundation = (foundation) =>
  Boolean(foundation?.foundation && Object.keys(foundation.foundation || {}).length);

const foundationSectionPaths = ["overview", "characters", "relationships", "world", "opening_scope"];

const sourceBadge = (source) => (
  <span className="rounded-full border border-line px-2 py-0.5 text-[11px] uppercase text-muted">
    {source || "model"}
  </span>
);

const listItems = (value, limit = 8) => {
  if (!value) return [];
  if (Array.isArray(value)) return value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).filter(Boolean).slice(0, limit);
  return String(value)
    .split(/\r?\n|;\s*/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, limit);
};

const lockLabel = (path) =>
  ({
    overview: "Overview",
    characters: "Characters",
    relationships: "Relationships",
    world: "World",
    opening_scope: "Opening",
  })[path] || path;

function Section({ children, defaultOpen = true, title }) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <section className="border-t border-line first:border-t-0">
      <button
        className="flex w-full items-center justify-between gap-3 py-3 text-left"
        onClick={() => setIsOpen((value) => !value)}
        type="button"
      >
        <span className="text-sm font-semibold text-zinc-100">{title}</span>
        <ChevronDown className={`text-muted transition ${isOpen ? "rotate-180" : ""}`} size={17} />
      </button>
      <AnimatePresence initial={false}>
        {isOpen ? (
          <motion.div
            animate={{ height: "auto", opacity: 1 }}
            className="overflow-hidden"
            exit={{ height: 0, opacity: 0 }}
            initial={{ height: 0, opacity: 0 }}
          >
            <div className="grid gap-3 pb-4">{children}</div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}

function Field({ field, label, onChange, type, value }) {
  return (
    <label className="min-w-0">
      <span className={labelClass}>{label}</span>
      {type === "textarea" ? (
        <textarea
          className={`${fieldClass} min-h-24 resize-y leading-6`}
          onChange={(event) => onChange(field, event.target.value)}
          value={value}
        />
      ) : (
        <input
          className={fieldClass}
          onChange={(event) => onChange(field, event.target.value)}
          value={value}
        />
      )}
    </label>
  );
}

function CharacterEditor({ activeSession, character, onCancel, onSaved }) {
  const createCharacter = useAppStore((state) => state.createCharacter);
  const updateCharacter = useAppStore((state) => state.updateCharacter);
  const repairCharacterVisualProfile = useAppStore((state) => state.repairCharacterVisualProfile);
  const storyState = useAppStore((state) => state.storyStateBySession[activeSession?.id] || null);
  const selectedSceneId = useAppStore((state) => state.selectedSceneId);
  const selectedVersionId = useAppStore((state) => state.selectedVersionId);
  const buildForm = (source) => ({
    ...characterDefaults,
    ...(source || {}),
    visual_profile: {
      ...visualProfileDefaults,
      ...(source?.visual_profile || {}),
    },
  });
  const [form, setForm] = useState(buildForm(character));
  const [attachToStory, setAttachToStory] = useState(Boolean(!character && activeSession));
  const [isActive, setIsActive] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [visualRepairStatus, setVisualRepairStatus] = useState("");
  const [isVisualRepairing, setIsVisualRepairing] = useState(false);

  useEffect(() => {
    setForm(buildForm(character));
    setAttachToStory(Boolean(!character && activeSession));
    setIsActive(true);
    setVisualRepairStatus("");
  }, [activeSession, character]);

  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  const updateProfile = (field, value) =>
    setForm((current) => ({
      ...current,
      visual_profile: {
        ...visualProfileDefaults,
        ...(current.visual_profile || {}),
        [field]: value,
      },
    }));
  const liveVisualState = useMemo(() => {
    const name = character?.name || form.name;
    if (!name) return [];
    const visualKeys = ["outfit", "cloak", "clothing", "injury", "wound", "scar", "bandage", "dirt", "wet", "location"];
    return (storyState?.character_live_state || [])
      .filter((item) => (item.character_name || "").toLowerCase() === name.toLowerCase())
      .filter((item) =>
        visualKeys.some((key) => String(item.key || "").toLowerCase().includes(key)),
      )
      .slice(0, 8);
  }, [character?.name, form.name, storyState?.character_live_state]);
  const runVisualRepair = async (mode) => {
    if (!character?.id) {
      setVisualRepairStatus("Save this character before repairing visual fields.");
      return;
    }
    setIsVisualRepairing(true);
    setVisualRepairStatus(
      mode === "extract"
        ? "Extracting visual profile..."
        : mode === "clear_contaminated"
          ? "Clearing contaminated appearance..."
          : "Repairing visual profile...",
    );
    try {
      const result = await repairCharacterVisualProfile(character.id, {
        mode,
        session_id: activeSession?.id || null,
        scene_id: selectedSceneId || null,
        version_id: selectedVersionId || null,
        apply_changes: true,
      });
      setForm(buildForm(result.character));
      const changeCount = Object.keys(result.changes || {}).length;
      if (result.warnings?.length) {
        setVisualRepairStatus(result.warnings.join(" "));
      } else if (changeCount) {
        setVisualRepairStatus("Visual fields updated. Original values were backed up.");
      } else {
        setVisualRepairStatus("No high-confidence visual repair was needed.");
      }
    } catch (error) {
      setVisualRepairStatus(error?.message || "Visual repair failed.");
    } finally {
      setIsVisualRepairing(false);
    }
  };

  const save = async () => {
    const payload = characterPayload(form);
    if (!payload.name) return;
    setIsSaving(true);
    try {
      if (character) {
        await updateCharacter(character.id, payload);
      } else {
        await createCharacter({
          ...payload,
          attach_to_session: Boolean(activeSession && attachToStory),
          session_id: activeSession?.id || null,
          is_active: isActive,
        });
      }
      onSaved();
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <motion.section
      animate={{ opacity: 1, y: 0 }}
      className="sd-settings-panel rounded-lg border border-line bg-panelSoft p-4"
      initial={{ opacity: 0, y: 8 }}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-zinc-100">
            {character ? "Edit Character" : "New Character"}
          </h3>
          <p className="mt-1 text-sm text-muted">Keep stable identity concise; live outfit, wounds, and objects come from Story State.</p>
        </div>
        <button className={iconButton} onClick={onCancel} type="button">
          <X size={16} />
        </button>
      </div>

      {characterGroups.map((group, index) => (
        <Section defaultOpen={index === 0} key={group.title} title={group.title}>
          {group.fields.map(([field, label, type]) => (
            <Field
              field={field}
              key={field}
              label={label}
              onChange={update}
              type={type}
              value={form[field] || ""}
            />
          ))}
        </Section>
      ))}

      <Section defaultOpen={false} title="Appearance Profile">
        <p className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
          Keep stable physical traits here. Changing details such as current outfit, wounds, dirt, carried objects, or location belong to live Story State.
        </p>
        <div className="grid gap-2 sm:grid-cols-3">
          <button
            className={textButton}
            disabled={!character?.id || isVisualRepairing}
            onClick={() => runVisualRepair("extract")}
            type="button"
          >
            <BrainCircuit size={15} />
            Extract Appearance
          </button>
          <button
            className={textButton}
            disabled={!character?.id || isVisualRepairing}
            onClick={() => runVisualRepair("repair")}
            type="button"
          >
            <RefreshCw size={15} />
            Repair Appearance
          </button>
          <button
            className={textButton}
            disabled={!character?.id || isVisualRepairing}
            onClick={() => runVisualRepair("clear_contaminated")}
            type="button"
          >
            <Trash2 size={15} />
            Clear Prose
          </button>
        </div>
        {visualRepairStatus ? (
          <p className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
            {visualRepairStatus}
          </p>
        ) : null}
        {liveVisualState.length ? (
          <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
            <div className="mb-1 font-medium text-zinc-300">Current appearance state</div>
            {liveVisualState.map((item) => (
              <div key={item.id}>
                {item.key?.replaceAll("_", " ")}: {item.value}
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-lg border border-line bg-[#0d0e11] px-3 py-2 text-xs leading-5 text-muted">
            Current appearance state will appear here after Story State tracks outfit, injury, location, or carried-object changes.
          </div>
        )}
        {visualProfileFields.map(([field, label, type]) => (
          <Field
            field={field}
            key={field}
            label={label}
            onChange={updateProfile}
            type={type}
            value={form.visual_profile?.[field] || ""}
          />
        ))}
      </Section>

      {!character && activeSession ? (
        <div className="mt-3 grid gap-2 border-t border-line pt-3 sm:grid-cols-2">
          <label className="flex min-h-10 items-center justify-between gap-3 rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-200">
            <span>Attach to current story</span>
            <input
              checked={attachToStory}
              className="h-4 w-4 accent-tide"
              onChange={(event) => setAttachToStory(event.target.checked)}
              type="checkbox"
            />
          </label>
          <label className="flex min-h-10 items-center justify-between gap-3 rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-200">
            <span>Active in prompt</span>
            <input
              checked={isActive}
              className="h-4 w-4 accent-moss"
              disabled={!attachToStory}
              onChange={(event) => setIsActive(event.target.checked)}
              type="checkbox"
            />
          </label>
        </div>
      ) : null}

      <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button className={textButton} onClick={onCancel} type="button">
          Cancel
        </button>
        <button
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
          disabled={isSaving || !form.name.trim()}
          onClick={save}
          type="button"
        >
          <Save size={16} />
          {isSaving ? "Saving..." : "Save"}
        </button>
      </div>
    </motion.section>
  );
}

function CharacterCard({ activeSession, character, link, onDelete, onEdit }) {
  const attachCharacter = useAppStore((state) => state.attachCharacter);
  const detachCharacter = useAppStore((state) => state.detachCharacter);
  const updateSessionCharacter = useAppStore((state) => state.updateSessionCharacter);
  const isAttached = Boolean(link);
  const isActive = Boolean(link?.is_active);
  const visualProfile = character.visual_profile || {};
  const hasVisualDetail = [
    character.appearance,
    visualProfile.base_visual_description,
    visualProfile.face_description,
    visualProfile.hair,
    visualProfile.body_build,
    visualProfile.default_outfit,
  ].some((value) => String(value || "").trim());
  const needsDetail =
    character.auto_created &&
    !hasVisualDetail;

  return (
    <article className="rounded-lg border border-line bg-panelSoft p-4">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-base font-semibold text-zinc-100">{character.name}</h3>
            <span
              className={`rounded-full border px-2 py-0.5 text-xs ${
                isAttached
                  ? isActive
                    ? "border-moss/35 bg-moss/10 text-moss"
                    : "border-line bg-[#0d0e11] text-muted"
                  : "border-line bg-[#0d0e11] text-muted"
              }`}
            >
              {isAttached ? (isActive ? "Active" : "Inactive") : "Unattached"}
            </span>
            {character.auto_created ? (
              <span className="rounded-full border border-tide/30 bg-tide/10 px-2 py-0.5 text-xs text-tide">
                Auto-created
              </span>
            ) : null}
            {needsDetail ? (
              <span className="rounded-full border border-amber-300/30 bg-amber-300/10 px-2 py-0.5 text-xs text-amber-100">
                Needs detail
              </span>
            ) : null}
          </div>
          {character.role ? <p className="mt-1 text-sm text-muted">{character.role}</p> : null}
        </div>
        <div className="flex shrink-0 gap-2">
          <button className={iconButton} onClick={() => onEdit(character)} title="Edit character" type="button">
            <Pencil size={15} />
          </button>
          <button
            className={`${iconButton} text-red-300`}
            onClick={() => onDelete(character)}
            title="Delete character"
            type="button"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      {character.personality || character.current_state ? (
        <div className="mt-3 grid gap-2 text-sm leading-6 text-zinc-300">
          {character.personality ? <p>{character.personality}</p> : null}
          {character.current_state ? <p className="text-muted">State: {character.current_state}</p> : null}
        </div>
      ) : null}

      {activeSession ? (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
          {isAttached ? (
            <>
              <label className="flex min-h-9 items-center gap-2 rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-200">
                <input
                  checked={isActive}
                  className="h-4 w-4 accent-moss"
                  onChange={(event) =>
                    updateSessionCharacter(activeSession.id, character.id, {
                      is_active: event.target.checked,
                    })
                  }
                  type="checkbox"
                />
                Include in prompt
              </label>
              <button
                className={textButton}
                onClick={() => detachCharacter(activeSession.id, character.id)}
                type="button"
              >
                <Unlink size={15} />
                Detach
              </button>
            </>
          ) : (
            <button
              className={textButton}
              onClick={() => attachCharacter(activeSession.id, character.id, true)}
              type="button"
            >
              <Link2 size={15} />
              Attach Active
            </button>
          )}
        </div>
      ) : null}
    </article>
  );
}

function FoundationList({ items }) {
  const rows = listItems(items);
  if (!rows.length) return <p className="text-sm text-muted">Unset</p>;
  return (
    <ul className="grid gap-1.5 text-sm leading-6 text-zinc-300">
      {rows.map((item, index) => (
        <li className="rounded-md border border-line bg-[#0d0e11] px-3 py-2" key={`${item}-${index}`}>
          {item}
        </li>
      ))}
    </ul>
  );
}

function FoundationPanel({ activeSession }) {
  const sessionId = activeSession?.id || null;
  const foundation = useAppStore((state) => state.foundationsBySession[sessionId] || null);
  const isRefreshingFoundation = useAppStore((state) => state.isRefreshingFoundation);
  const fetchStoryFoundation = useAppStore((state) => state.fetchStoryFoundation);
  const refreshStoryFoundation = useAppStore((state) => state.refreshStoryFoundation);
  const updateStoryFoundation = useAppStore((state) => state.updateStoryFoundation);
  const [status, setStatus] = useState("Ready");
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const data = foundation?.foundation || {};
  const overview = data.overview || {};
  const world = data.world || {};
  const opening = data.opening_scope || {};
  const lockedPaths = foundation?.locked_paths || [];

  useEffect(() => {
    if (sessionId && foundation === undefined) {
      fetchStoryFoundation(sessionId).catch(() => {});
    }
  }, [fetchStoryFoundation, foundation, sessionId]);

  useEffect(() => {
    setDraft(JSON.stringify(data || {}, null, 2));
  }, [foundation?.updated_at, sessionId]);

  const refresh = async () => {
    if (!sessionId) return;
    setStatus("Refreshing...");
    try {
      await refreshStoryFoundation(sessionId, {
        director_note: foundation?.source_director_note || "",
        apply_to_cards: true,
        force: true,
      });
      setStatus("Refreshed");
    } catch (error) {
      setStatus(error?.message || "Refresh failed");
    }
  };

  const save = async () => {
    if (!sessionId) return;
    let parsed;
    try {
      parsed = JSON.parse(draft || "{}");
    } catch {
      setStatus("JSON is invalid");
      return;
    }
    setStatus("Saving...");
    try {
      await updateStoryFoundation(sessionId, {
        foundation: parsed,
        locked_paths: lockedPaths,
        apply_to_cards: true,
      });
      setIsEditing(false);
      setStatus("Saved");
    } catch (error) {
      setStatus(error?.message || "Save failed");
    }
  };

  const toggleLock = async (path) => {
    if (!sessionId) return;
    const nextLocked = lockedPaths.includes(path)
      ? lockedPaths.filter((item) => item !== path)
      : [...lockedPaths, path];
    setStatus(nextLocked.includes(path) ? "Locked" : "Unlocked");
    try {
      await updateStoryFoundation(sessionId, {
        foundation: data,
        locked_paths: nextLocked,
        apply_to_cards: false,
      });
    } catch (error) {
      setStatus(error?.message || "Lock update failed");
    }
  };

  if (!activeSession) {
    return (
      <section className="rounded-lg border border-line bg-panelSoft p-4 text-sm leading-6 text-muted">
        Select a story to view its foundation and character bible.
      </section>
    );
  }

  return (
    <div className="grid gap-4">
      <section className="rounded-lg border border-line bg-panelSoft p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-zinc-100">Story Foundation</h3>
            <p className="mt-1 text-sm leading-6 text-muted">
              Story-scoped base identity, relationships, world setup, and opening scope for this story.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <span className="rounded-full border border-line px-2 py-0.5 text-[11px] uppercase text-muted">
                {hasStoryFoundation(foundation) ? foundation.status || "ready" : "not set"}
              </span>
              {foundation?.generation_model ? (
                <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">
                  {foundation.generation_model}
                </span>
              ) : null}
              {foundation?.generation_error ? (
                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-200">
                  fallback
                </span>
              ) : null}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">{isRefreshingFoundation ? "Refreshing..." : status}</span>
            <button className={textButton} disabled={isRefreshingFoundation} onClick={refresh} type="button">
              <RefreshCw size={15} />
              Refresh Suggested Values
            </button>
            <button className={textButton} onClick={() => setIsEditing((value) => !value)} type="button">
              <Pencil size={15} />
              Edit
            </button>
          </div>
        </div>

        {isEditing ? (
          <div className="mt-4 grid gap-3">
            <textarea
              className={`${fieldClass} min-h-96 resize-y font-mono text-xs leading-5`}
              onChange={(event) => {
                setDraft(event.target.value);
                setStatus("Unsaved");
              }}
              value={draft}
            />
            <div className="flex justify-end gap-2">
              <button className={textButton} onClick={() => setDraft(JSON.stringify(data || {}, null, 2))} type="button">
                Reset
              </button>
              <button className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white" onClick={save} type="button">
                <Save size={16} />
                Save
              </button>
            </div>
          </div>
        ) : null}

        <div className="mt-4 flex flex-wrap gap-2">
          {foundationSectionPaths.map((path) => {
            const locked = lockedPaths.includes(path);
            const Icon = locked ? Lock : Unlock;
            return (
              <button className={textButton} key={path} onClick={() => toggleLock(path)} type="button">
                <Icon size={14} />
                {locked ? "Locked" : "Unlocked"} {lockLabel(path)}
              </button>
            );
          })}
        </div>
      </section>

      {!hasStoryFoundation(foundation) ? (
        <div className="rounded-lg border border-line bg-panelSoft p-5 text-sm leading-6 text-muted">
          No foundation has been generated yet. The next first-scene generation creates one automatically; refresh can also create suggested values from existing cards and world notes.
        </div>
      ) : (
        <>
          <section className="rounded-lg border border-line bg-panelSoft p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-zinc-100">Overview</h3>
              {sourceBadge(overview.source)}
            </div>
            <div className="grid gap-3 text-sm leading-6 text-zinc-300">
              <p>{overview.premise || "Unset"}</p>
              <div className="grid gap-2 sm:grid-cols-3">
                <div><span className={labelClass}>Genre</span>{overview.genre || "Unset"}</div>
                <div><span className={labelClass}>Time</span>{overview.time_period || "Unset"}</div>
                <div><span className={labelClass}>Tone</span>{overview.tone || "Unset"}</div>
              </div>
              <FoundationList items={overview.story_rules} />
            </div>
          </section>

          <section className="rounded-lg border border-line bg-panelSoft p-4">
            <h3 className="mb-3 text-sm font-semibold text-zinc-100">Main Characters</h3>
            <div className="grid gap-3">
              {(data.characters || []).map((character, index) => (
                <article className="rounded-lg border border-line bg-[#0d0e11] p-3" key={`${character.name || "character"}-${index}`}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <h4 className="text-sm font-semibold text-zinc-100">{character.name || "Unnamed"}</h4>
                      <p className="mt-1 text-xs text-muted">{character.role || "Role unset"}</p>
                    </div>
                    {sourceBadge(character.source)}
                  </div>
                  <div className="mt-3 grid gap-2 text-sm leading-6 text-zinc-300">
                    <p>{character.appearance || "Appearance unset"}</p>
                    <p>{character.personality || "Personality unset"}</p>
                    <p className="text-muted">{character.voice || "Voice unset"}</p>
                    <FoundationList items={character.relationships} />
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="rounded-lg border border-line bg-panelSoft p-4">
            <h3 className="mb-3 text-sm font-semibold text-zinc-100">Relationships</h3>
            <div className="grid gap-2">
              {(data.relationships || []).map((relationship, index) => (
                <div className="rounded-lg border border-line bg-[#0d0e11] p-3 text-sm leading-6 text-zinc-300" key={index}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-zinc-100">{listItems(relationship.characters, 4).join(" / ") || "Relationship"}</span>
                    {sourceBadge(relationship.source)}
                  </div>
                  <p className="mt-1">{[relationship.type, relationship.dynamic, relationship.tension].filter(Boolean).join(" - ") || "Unset"}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-lg border border-line bg-panelSoft p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-zinc-100">World Foundation</h3>
              {sourceBadge(world.source)}
            </div>
            <div className="grid gap-3">
              <FoundationList items={world.rules} />
              <FoundationList items={world.locations} />
              <FoundationList items={world.conflicts} />
              {world.technology_magic ? <p className="text-sm leading-6 text-zinc-300">{world.technology_magic}</p> : null}
            </div>
          </section>

          <section className="rounded-lg border border-line bg-panelSoft p-4">
            <h3 className="mb-3 text-sm font-semibold text-zinc-100">Opening Scope</h3>
            <div className="grid gap-3 text-sm leading-6 text-zinc-300">
              <p>{[opening.start_location, opening.start_time].filter(Boolean).join(" - ") || "Start unset"}</p>
              <p>{opening.initial_pressure || "Initial pressure unset"}</p>
              <FoundationList items={opening.first_scene_jobs} />
              <FoundationList items={opening.boundaries} />
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function CharactersPanel({ activeSession }) {
  const characters = useAppStore((state) => state.characters);
  const deleteCharacter = useAppStore((state) => state.deleteCharacter);
  const sessionCharacters = useAppStore(
    (state) => state.sessionCharactersBySession[activeSession?.id] || EMPTY_ARRAY,
  );
  const [editing, setEditing] = useState(null);
  const [isCreating, setIsCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const linkByCharacterId = useMemo(
    () => new Map(sessionCharacters.map((link) => [link.character_id, link])),
    [sessionCharacters],
  );
  const characterById = useMemo(
    () => new Map(characters.map((character) => [character.id, character])),
    [characters],
  );
  const storyCharacters = useMemo(
    () =>
      sessionCharacters
        .map((link) => {
          const character = link.character || characterById.get(link.character_id);
          return character ? { character, link } : null;
        })
        .filter(Boolean)
        .sort((a, b) => a.character.name.localeCompare(b.character.name, undefined, { sensitivity: "base" })),
    [characterById, sessionCharacters],
  );
  const libraryCharacters = useMemo(
    () =>
      activeSession
        ? characters.filter((character) => !linkByCharacterId.has(character.id))
        : characters,
    [activeSession, characters, linkByCharacterId],
  );

  return (
    <div className="grid gap-4">
      {isCreating || editing ? (
        <CharacterEditor
          activeSession={activeSession}
          character={editing}
          onCancel={() => {
            setIsCreating(false);
            setEditing(null);
          }}
          onSaved={() => {
            setIsCreating(false);
            setEditing(null);
          }}
        />
      ) : (
        <button
          className="inline-flex min-h-10 w-fit items-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white"
          onClick={() => setIsCreating(true)}
          type="button"
        >
          <Plus size={16} />
          {activeSession ? "New Story Character" : "New Character"}
        </button>
      )}

      {activeSession ? (
        <section className="grid gap-3">
          <div>
            <h3 className="text-sm font-semibold text-zinc-100">Current Story Characters</h3>
            <p className="mt-1 text-xs leading-5 text-muted">
              Only these attached characters feed this story's prose and automatic state.
            </p>
          </div>
          {storyCharacters.length ? (
            <div className="grid gap-3">
              {storyCharacters.map(({ character, link }) => (
                <CharacterCard
                  activeSession={activeSession}
                  character={character}
                  key={link.character_id}
                  link={link}
                  onDelete={setDeleteTarget}
                  onEdit={(nextCharacter) => {
                    setIsCreating(false);
                    setEditing(nextCharacter);
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-line bg-panelSoft p-5 text-sm leading-6 text-muted">
              No characters are attached to this story yet. Create one here, or attach a reusable card from the library below.
            </div>
          )}
          {libraryCharacters.length ? (
            <details className="rounded-lg border border-line bg-panelSoft">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-zinc-200">
                Unattached / Library ({libraryCharacters.length})
              </summary>
              <div className="grid gap-3 border-t border-line p-3">
                {libraryCharacters.map((character) => (
                  <CharacterCard
                    activeSession={activeSession}
                    character={character}
                    key={character.id}
                    link={undefined}
                    onDelete={setDeleteTarget}
                    onEdit={(nextCharacter) => {
                      setIsCreating(false);
                      setEditing(nextCharacter);
                    }}
                  />
                ))}
              </div>
            </details>
          ) : null}
        </section>
      ) : characters.length ? (
        <section className="grid gap-3">
          <div>
            <h3 className="text-sm font-semibold text-zinc-100">Character Library</h3>
            <p className="mt-1 text-xs leading-5 text-muted">Select a story to see only the characters attached to it.</p>
          </div>
          {characters.map((character) => (
            <CharacterCard
              activeSession={activeSession}
              character={character}
              key={character.id}
              link={undefined}
              onDelete={setDeleteTarget}
              onEdit={(nextCharacter) => {
                setIsCreating(false);
                setEditing(nextCharacter);
              }}
            />
          ))}
        </section>
      ) : (
        <div className="rounded-lg border border-line bg-panelSoft p-5 text-sm leading-6 text-muted">
          No character cards yet. Create one inside a story to attach it automatically.
        </div>
      )}

      <AnimatePresence>
        {deleteTarget ? (
          <ConfirmDialog
            body={`This deletes "${deleteTarget.name}" and removes it from any stories where it is attached.`}
            confirmLabel="Delete Character"
            onCancel={() => setDeleteTarget(null)}
            onConfirm={async () => {
              await deleteCharacter(deleteTarget.id);
              setDeleteTarget(null);
              if (editing?.id === deleteTarget.id) {
                setEditing(null);
              }
            }}
            title="Delete this character?"
          />
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function WorldPanel({ activeSession }) {
  const saveWorldNotes = useAppStore((state) => state.saveWorldNotes);
  const worldNotes = useAppStore((state) => state.worldNotesBySession[activeSession?.id]);
  const [form, setForm] = useState(worldDefaults);
  const [status, setStatus] = useState("Ready");

  useEffect(() => {
    setForm({ ...worldDefaults, ...(worldNotes || {}) });
    setStatus("Ready");
  }, [worldNotes?.id, activeSession?.id]);

  const update = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
    setStatus("Unsaved");
  };

  const save = async () => {
    if (!activeSession) return;
    setStatus("Saving...");
    try {
      await saveWorldNotes(activeSession.id, cleanPayload(form));
      setStatus("Saved");
    } catch {
      setStatus("Save failed");
    }
  };

  return (
    <section className="rounded-lg border border-line bg-panelSoft p-4">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-base font-semibold text-zinc-100">Current Story World Notes</h3>
          <p className="mt-1 text-sm text-muted">Only this story uses these notes for continuity and prose.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">{status}</span>
          <button
            className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white disabled:opacity-50"
            disabled={!activeSession || status === "Saving..."}
            onClick={save}
            type="button"
          >
            <Save size={16} />
            Save
          </button>
        </div>
      </div>

      <div className="grid gap-3">
        {worldFields.map(([field, label]) => (
          <label className="min-w-0" key={field}>
            <span className={labelClass}>{label}</span>
            <textarea
              className={`${fieldClass} min-h-24 resize-y leading-6`}
              onChange={(event) => update(field, event.target.value)}
              value={form[field] || ""}
            />
          </label>
        ))}
      </div>
    </section>
  );
}

function QualityPanel({ activeSession }) {
  const sessionId = activeSession?.id || null;
  const selectedSceneId = useAppStore((state) => state.selectedSceneId);
  const selectedVersionId = useAppStore((state) => state.selectedVersionId);
  const notes = useAppStore((state) => state.qualityNotesBySession[sessionId] || EMPTY_ARRAY);
  const hints = useAppStore((state) => state.qualityHintsBySession[sessionId] || null);
  const fetchQualityNotes = useAppStore((state) => state.fetchQualityNotes);
  const fetchQualityHints = useAppStore((state) => state.fetchQualityHints);
  const addQualityNote = useAppStore((state) => state.addQualityNote);
  const updateQualityNote = useAppStore((state) => state.updateQualityNote);
  const deleteQualityNote = useAppStore((state) => state.deleteQualityNote);
  const [form, setForm] = useState({
    note_type: "writing",
    severity: "medium",
    note_text: "",
    target_selected: true,
  });
  const [noteFilter, setNoteFilter] = useState("open");
  const [status, setStatus] = useState("Ready");

  useEffect(() => {
    if (!sessionId) return;
    fetchQualityNotes(sessionId);
    fetchQualityHints(sessionId);
  }, [fetchQualityHints, fetchQualityNotes, sessionId]);

  const openNotes = notes.filter((note) => note.status === "open");
  const fixedNotes = notes.filter((note) => note.status !== "open");
  const filteredNotes = notes.filter((note) => noteFilter === "all" || note.status === noteFilter);
  const selectedTargetLabel =
    form.target_selected && selectedSceneId
      ? selectedVersionId
        ? "Attach to selected scene/version"
        : "Attach to selected scene"
      : "Story-level note";

  const saveNote = async () => {
    const noteText = form.note_text.trim();
    if (!sessionId || !noteText) return;
    setStatus("Saving...");
    try {
      await addQualityNote(sessionId, {
        note_type: form.note_type,
        severity: form.severity,
        note_text: noteText,
        scene_id: form.target_selected ? selectedSceneId : null,
        version_id: form.target_selected ? selectedVersionId : null,
      });
      setForm((current) => ({ ...current, note_text: "" }));
      setStatus("Saved");
    } catch (error) {
      setStatus(error?.message || "Save failed");
    }
  };

  const logHintAsNote = async (hint) => {
    if (!sessionId || !hint?.message) return;
    setStatus("Saving hint...");
    try {
      await addQualityNote(sessionId, {
        note_type: hint.hint_type || "other",
        severity: hint.severity || "low",
        note_text: `${hint.action_label ? `${hint.action_label}: ` : ""}${hint.message}`,
        scene_id: hint.scene_id || null,
        version_id: hint.version_id || null,
      });
      setNoteFilter("open");
      setStatus("Saved");
    } catch (error) {
      setStatus(error?.message || "Save failed");
    }
  };

  if (!activeSession) {
    return <EmptyState>Select a story to keep human-test notes.</EmptyState>;
  }

  return (
    <div className="grid gap-4">
      <section className="rounded-lg border border-line bg-panelSoft p-4">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 className="text-base font-semibold text-zinc-100">Human Test Notes</h3>
            <p className="mt-1 text-sm leading-6 text-muted">
              Local-only issue notes for this story. Nothing is sent outside StoryDriver.
            </p>
          </div>
          <button className={textButton} onClick={() => fetchQualityHints(sessionId)} type="button">
            <RefreshCw size={15} />
            Refresh hints
          </button>
        </div>

        <div className="grid gap-3 rounded-lg border border-line bg-[#0d0e11] p-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="min-w-0">
              <span className={labelClass}>Type</span>
              <select
                className={fieldClass}
                onChange={(event) => setForm((current) => ({ ...current, note_type: event.target.value }))}
                value={form.note_type}
              >
                {qualityNoteTypes.map((type) => (
                  <option key={type} value={type}>
                    {type.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
            <label className="min-w-0">
              <span className={labelClass}>Severity</span>
              <select
                className={fieldClass}
                onChange={(event) => setForm((current) => ({ ...current, severity: event.target.value }))}
                value={form.severity}
              >
                {qualitySeverities.map((severity) => (
                  <option key={severity} value={severity}>
                    {severity.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex min-h-10 items-center justify-between gap-3 rounded-lg border border-line bg-panelSoft px-3 text-sm text-zinc-200 sm:mt-6">
              <span className="min-w-0 truncate">{selectedTargetLabel}</span>
              <input
                checked={form.target_selected}
                className="h-4 w-4 accent-tide"
                disabled={!selectedSceneId}
                onChange={(event) => setForm((current) => ({ ...current, target_selected: event.target.checked }))}
                type="checkbox"
              />
            </label>
          </div>
          <label className="min-w-0">
            <span className={labelClass}>Note</span>
            <textarea
              className={`${fieldClass} min-h-24 resize-y leading-6`}
              onChange={(event) => setForm((current) => ({ ...current, note_text: event.target.value }))}
              placeholder="Bad state, weak prompt, TTS hiccup, UI annoyance..."
              value={form.note_text}
            />
          </label>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <span className="text-xs text-muted">{status}</span>
            <button
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white disabled:opacity-50"
              disabled={!form.note_text.trim()}
              onClick={saveNote}
              type="button"
            >
              <Plus size={15} />
              Add note
            </button>
          </div>
        </div>
      </section>

      <section className="rounded-lg border border-line bg-panelSoft p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-zinc-100">Automatic QA Hints</h3>
            <p className="mt-1 text-xs leading-5 text-muted">Quiet local checks for obvious test-session problems.</p>
          </div>
          <span className="rounded-full border border-line bg-[#0d0e11] px-2 py-0.5 text-xs text-muted">
            {hints?.hints?.length || 0}
          </span>
        </div>
        {hints?.hints?.length ? (
          <div className="grid gap-2">
            {hints.hints.map((hint) => (
              <article
                className={`rounded-lg border p-3 text-sm leading-6 ${
                  hint.severity === "high"
                    ? "border-ember/35 bg-ember/10 text-ember"
                    : hint.severity === "medium"
                      ? "border-amber-300/30 bg-amber-300/10 text-amber-100"
                      : "border-line bg-[#0d0e11] text-muted"
                }`}
                key={hint.id}
              >
                <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-wide">
                  <span>{hint.hint_type}</span>
                  <span>{hint.severity}</span>
                  {hint.action_label ? <span className="normal-case tracking-normal text-muted">{hint.action_label}</span> : null}
                </div>
                <p className="mt-1 safe-wrap">{hint.message}</p>
                <button className={`${textButton} mt-2`} onClick={() => logHintAsNote(hint)} type="button">
                  <Plus size={13} />
                  Log note
                </button>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState>No automatic QA hints right now.</EmptyState>
        )}
      </section>

      <section className="rounded-lg border border-line bg-panelSoft p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-zinc-100">Notes</h3>
            <span className="rounded-full border border-line bg-[#0d0e11] px-2 py-0.5 text-xs text-muted">
              {filteredNotes.length}
            </span>
          </div>
          <select
            className="min-h-8 rounded-lg border border-line bg-[#0d0e11] px-2 text-xs text-zinc-200"
            onChange={(event) => setNoteFilter(event.target.value)}
            value={noteFilter}
          >
            <option value="open">Open</option>
            <option value="fixed">Fixed</option>
            <option value="ignored">Ignored</option>
            <option value="all">All</option>
          </select>
        </div>
        {filteredNotes.length ? (
          <div className="grid gap-2">
            {filteredNotes.map((note) => (
              <QualityNoteCard
                key={note.id}
                note={note}
                onDelete={() => deleteQualityNote(sessionId, note.id)}
                onUpdate={(patch) => updateQualityNote(sessionId, note.id, patch)}
              />
            ))}
          </div>
        ) : (
          <EmptyState>No {noteFilter === "all" ? "" : noteFilter} human-test notes for this story.</EmptyState>
        )}
      </section>

      {fixedNotes.length && noteFilter === "open" ? (
        <details className="rounded-lg border border-line bg-panelSoft">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-zinc-200">
            Fixed / Ignored Notes ({fixedNotes.length})
          </summary>
          <div className="grid gap-2 border-t border-line p-3">
            {fixedNotes.map((note) => (
              <QualityNoteCard
                key={note.id}
                note={note}
                onDelete={() => deleteQualityNote(sessionId, note.id)}
                onUpdate={(patch) => updateQualityNote(sessionId, note.id, patch)}
              />
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function QualityNoteCard({ note, onDelete, onUpdate }) {
  return (
    <article className="rounded-lg border border-line bg-[#0d0e11] p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-muted">
            <span className="text-tide">{note.note_type}</span>
            <span>{note.severity}</span>
            {note.scene_id ? <span className="normal-case tracking-normal">scene note</span> : <span className="normal-case tracking-normal">story note</span>}
          </div>
          <p className="mt-2 safe-wrap text-sm leading-6 text-zinc-200">{note.note_text}</p>
          <p className="mt-2 text-[11px] text-muted">{note.created_at}</p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <select
            className="min-h-9 rounded-lg border border-line bg-panelSoft px-2 text-xs text-zinc-200"
            onChange={(event) => onUpdate({ status: event.target.value })}
            value={note.status}
          >
            {qualityStatuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
          <button
            className="grid h-9 w-9 place-items-center rounded-lg border border-line bg-panelSoft text-muted transition hover:border-red-400/50 hover:text-red-200"
            onClick={onDelete}
            title="Delete local note"
            type="button"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
    </article>
  );
}

const relationshipTypes = [
  "unknown",
  "parent",
  "child",
  "sibling",
  "spouse",
  "lover",
  "close_friend",
  "mentor",
  "rival",
  "enemy",
  "betrayer",
  "ally",
  "acquaintance",
  "stranger",
];
const emotionalMemoryTypes = ["emotional", "grief", "trauma", "promise", "betrayal", "conflict", "guilt", "shame", "fear", "protection"];
const relationshipWeightFields = [
  ["emotional_importance", "Importance"],
  ["closeness_weight", "Closeness"],
  ["trust_weight", "Trust"],
  ["conflict_weight", "Conflict"],
  ["protective_weight", "Protective"],
  ["grief_weight", "Grief"],
  ["romantic_weight", "Romantic"],
  ["family_weight", "Family"],
  ["betrayal_weight", "Betrayal"],
  ["respect_weight", "Respect"],
  ["fear_weight", "Fear"],
];

const stateDisplayValue = (item) => item.memory_text || item.value || item.content || item.description || item.status || "";
const stateTitle = (item) => {
  if (item.memory_type) return item.memory_type.replaceAll("_", " ");
  if (item.character_a_name || item.character_b_name) {
    return [item.character_a_name, item.character_b_name].filter(Boolean).join(" / ") || "Relationship";
  }
  return item.key?.replaceAll("_", " ") || item.title || item.name || item.object_key || item.thread_key || "State";
};
const formatWeight = (value) => `${Math.round((Number(value) || 0) * 100)}%`;
const listToText = (value) => (Array.isArray(value) ? value.join(", ") : String(value || ""));
const textToList = (value) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 12);
const stateStatus = (item) => {
  if (item.disabled) return "Disabled";
  if (item.archived || item.status === "archived") return "Archived";
  if (item.status === "resolved") return "Resolved";
  return "Active";
};

function StateRow({
  disabled = false,
  item,
  itemType,
  needsReview = false,
  onArchive,
  onDisable,
  onRestore,
  onUpdate,
}) {
  const sourceScene = item.source_scene_id || item.scene_id || "";
  const sourceVersion = item.source_version_id || item.version_id || "";
  const [isEditing, setIsEditing] = useState(false);
  const [form, setForm] = useState({
    value: stateDisplayValue(item),
    confidence: item.confidence ?? 0,
    owner_character_name: item.owner_character_name || "",
    status: item.status || "active",
    relationship_type: item.relationship_type || "unknown",
    manually_pinned: Boolean(item.manually_pinned),
    memory_type: item.memory_type || "emotional",
    emotional_weight: item.emotional_weight ?? 0,
    themes: listToText(item.themes),
    related_characters: listToText(item.related_characters),
    trigger_conditions: item.trigger_conditions || "",
    cooldown_scenes: item.cooldown_scenes ?? 3,
    ...Object.fromEntries(relationshipWeightFields.map(([field]) => [field, item[field] ?? 0])),
  });

  useEffect(() => {
    setForm({
      value: stateDisplayValue(item),
      confidence: item.confidence ?? 0,
      owner_character_name: item.owner_character_name || "",
      status: item.status || "active",
      relationship_type: item.relationship_type || "unknown",
      manually_pinned: Boolean(item.manually_pinned),
      memory_type: item.memory_type || "emotional",
      emotional_weight: item.emotional_weight ?? 0,
      themes: listToText(item.themes),
      related_characters: listToText(item.related_characters),
      trigger_conditions: item.trigger_conditions || "",
      cooldown_scenes: item.cooldown_scenes ?? 3,
      ...Object.fromEntries(relationshipWeightFields.map(([field]) => [field, item[field] ?? 0])),
    });
  }, [
    item.id,
    item.value,
    item.content,
    item.memory_text,
    item.confidence,
    item.owner_character_name,
    item.status,
    item.relationship_type,
    item.manually_pinned,
    item.memory_type,
    item.emotional_weight,
    item.themes,
    item.related_characters,
    item.trigger_conditions,
    item.cooldown_scenes,
  ]);

  const currentStatus = stateStatus(item);
  const salienceParts =
    itemType === "relationship"
      ? relationshipWeightFields
          .map(([field, label]) => [label, Number(item[field]) || 0])
          .filter(([, value]) => value >= 0.05)
          .slice(0, 6)
      : [];
  const save = async () => {
    if (!itemType || !onUpdate) return;
    const payload = {
      value: form.value,
      confidence: Number(form.confidence),
      manual_override: true,
      manually_pinned: Boolean(form.manually_pinned),
    };
    if (itemType === "object") {
      payload.owner_character_name = form.owner_character_name;
    }
    if (itemType === "plot_thread") {
      payload.status = form.status;
    }
    if (itemType === "relationship") {
      payload.relationship_type = form.relationship_type;
      for (const [field] of relationshipWeightFields) {
        payload[field] = Number(form[field]) || 0;
      }
    }
    if (itemType === "emotional_memory") {
      payload.memory_text = form.value;
      payload.memory_type = form.memory_type;
      payload.emotional_weight = Number(form.emotional_weight) || 0;
      payload.themes = textToList(form.themes);
      payload.related_characters = textToList(form.related_characters);
      payload.trigger_conditions = form.trigger_conditions;
      payload.cooldown_scenes = Number(form.cooldown_scenes) || 0;
    }
    await onUpdate(itemType, item.id, payload);
    setIsEditing(false);
  };

  return (
    <div className={`rounded-lg border p-3 ${needsReview ? "border-amber-300/40 bg-amber-300/5" : "border-line bg-[#0d0e11]"}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-zinc-100">{stateTitle(item)}</span>
            {item.state_type ? <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-muted">{item.state_type}</span> : null}
            <span className={`rounded-full border px-2 py-0.5 text-[11px] ${
              currentStatus === "Active"
                ? "border-moss/30 bg-moss/10 text-moss"
                : currentStatus === "Disabled"
                  ? "border-zinc-500/30 bg-zinc-500/10 text-muted"
                  : "border-amber-300/25 bg-amber-300/10 text-amber-200"
            }`}>
              {currentStatus}
            </span>
            {item.manual_override ? <span className="rounded-full border border-tide/30 bg-tide/10 px-2 py-0.5 text-[11px] text-tide">manual</span> : null}
            {item.manually_pinned ? <span className="rounded-full border border-moss/30 bg-moss/10 px-2 py-0.5 text-[11px] text-moss">pinned</span> : null}
            {item.is_tentative ? <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-2 py-0.5 text-[11px] text-amber-200">tentative</span> : null}
            {needsReview ? <span className="rounded-full border border-ember/30 bg-ember/10 px-2 py-0.5 text-[11px] text-ember">needs review</span> : null}
          </div>
          {item.character_name || item.owner_character_name || item.character_a_name || item.character_b_name || item.related_characters?.length ? (
            <p className="mt-1 text-xs text-muted">
              {[item.character_name, item.owner_character_name, [item.character_a_name, item.character_b_name].filter(Boolean).join(" / "), listToText(item.related_characters)]
                .filter(Boolean)
                .join(" · ")}
            </p>
          ) : null}
        </div>
        {itemType ? (
          <div className="flex flex-wrap justify-end gap-2">
            <button className={textButton} disabled={disabled} onClick={() => setIsEditing((value) => !value)} type="button">
              <Pencil size={14} />
              Edit
            </button>
            {currentStatus === "Active" || currentStatus === "Resolved" ? (
              <>
                <button className={textButton} disabled={disabled} onClick={() => onDisable?.(itemType, item.id)} type="button">
                  Disable
                </button>
                <button className={textButton} disabled={disabled} onClick={() => onArchive?.(itemType, item.id)} type="button">
                  Archive
                </button>
              </>
            ) : (
              <button className={textButton} disabled={disabled} onClick={() => onRestore?.(itemType, item.id)} type="button">
                Restore
              </button>
            )}
          </div>
        ) : null}
      </div>

      <p className="mt-2 break-words text-sm leading-6 text-zinc-300">{stateDisplayValue(item) || "No detail recorded."}</p>
      {item.owner_character_name && itemType === "object" ? (
        <p className="mt-1 text-xs text-muted">Owner: {item.owner_character_name}</p>
      ) : null}
      {itemType === "relationship" && salienceParts.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {salienceParts.map(([label, value]) => (
            <span className="rounded-full border border-line bg-panelSoft px-2 py-0.5 text-[11px] text-muted" key={label}>
              {label} {formatWeight(value)}
            </span>
          ))}
        </div>
      ) : null}
      {itemType === "emotional_memory" ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <span className="rounded-full border border-line bg-panelSoft px-2 py-0.5 text-[11px] text-muted">
            Weight {formatWeight(item.emotional_weight)}
          </span>
          {item.use_count ? (
            <span className="rounded-full border border-line bg-panelSoft px-2 py-0.5 text-[11px] text-muted">
              Used {item.use_count}x
            </span>
          ) : null}
          {item.themes?.slice(0, 5).map((theme) => (
            <span className="rounded-full border border-line bg-panelSoft px-2 py-0.5 text-[11px] text-muted" key={theme}>
              {theme}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        <span>Confidence {Math.round((item.confidence || 0) * 100)}%</span>
        {item.review_status ? <span>Review {item.review_status}</span> : null}
        {item.updated_at ? <span>Updated {item.updated_at}</span> : null}
        {item.last_used_in_prompt ? <span>Last recalled {item.last_used_in_prompt}</span> : null}
      </div>
      {sourceScene ? (
        <p className="mt-1 break-all text-[11px] text-muted">
          Source scene {sourceScene}{sourceVersion ? ` / version ${sourceVersion}` : ""}
        </p>
      ) : null}

      {isEditing ? (
        <div className="mt-3 grid gap-3 rounded-lg border border-line bg-panelSoft p-3">
          <label className="min-w-0">
            <span className={labelClass}>Value</span>
            <textarea
              className={`${fieldClass} min-h-24 resize-y leading-6`}
              onChange={(event) => setForm((current) => ({ ...current, value: event.target.value }))}
              value={form.value}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="min-w-0">
              <span className={labelClass}>Confidence</span>
              <input
                className={fieldClass}
                max="1"
                min="0"
                onChange={(event) => setForm((current) => ({ ...current, confidence: event.target.value }))}
                step="0.05"
                type="number"
                value={form.confidence}
              />
            </label>
            {itemType === "object" ? (
              <label className="min-w-0">
                <span className={labelClass}>Owner</span>
                <input
                  className={fieldClass}
                  onChange={(event) => setForm((current) => ({ ...current, owner_character_name: event.target.value }))}
                  value={form.owner_character_name}
                />
              </label>
            ) : null}
            {itemType === "plot_thread" ? (
              <label className="min-w-0">
                <span className={labelClass}>Status</span>
                <select
                  className={fieldClass}
                  onChange={(event) => setForm((current) => ({ ...current, status: event.target.value }))}
                  value={form.status}
                >
                  <option value="active">Active</option>
                  <option value="resolved">Resolved</option>
                  <option value="archived">Archived</option>
                </select>
              </label>
            ) : null}
            <label className="flex min-h-10 items-center justify-between gap-3 rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-200">
              <span>Pin for recall</span>
              <input
                checked={Boolean(form.manually_pinned)}
                className="h-4 w-4 accent-moss"
                onChange={(event) => setForm((current) => ({ ...current, manually_pinned: event.target.checked }))}
                type="checkbox"
              />
            </label>
          </div>
          {itemType === "relationship" ? (
            <div className="grid gap-3">
              <label className="min-w-0">
                <span className={labelClass}>Relationship type</span>
                <select
                  className={fieldClass}
                  onChange={(event) => setForm((current) => ({ ...current, relationship_type: event.target.value }))}
                  value={form.relationship_type}
                >
                  {relationshipTypes.map((type) => (
                    <option key={type} value={type}>
                      {type.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                {relationshipWeightFields.map(([field, label]) => (
                  <label className="min-w-0" key={field}>
                    <span className={labelClass}>{label}</span>
                    <input
                      className={fieldClass}
                      max="1"
                      min="0"
                      onChange={(event) => setForm((current) => ({ ...current, [field]: event.target.value }))}
                      step="0.05"
                      type="number"
                      value={form[field]}
                    />
                  </label>
                ))}
              </div>
            </div>
          ) : null}
          {itemType === "emotional_memory" ? (
            <div className="grid gap-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="min-w-0">
                  <span className={labelClass}>Memory type</span>
                  <select
                    className={fieldClass}
                    onChange={(event) => setForm((current) => ({ ...current, memory_type: event.target.value }))}
                    value={form.memory_type}
                  >
                    {emotionalMemoryTypes.map((type) => (
                      <option key={type} value={type}>
                        {type.replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="min-w-0">
                  <span className={labelClass}>Emotional weight</span>
                  <input
                    className={fieldClass}
                    max="1"
                    min="0"
                    onChange={(event) => setForm((current) => ({ ...current, emotional_weight: event.target.value }))}
                    step="0.05"
                    type="number"
                    value={form.emotional_weight}
                  />
                </label>
              </div>
              <label className="min-w-0">
                <span className={labelClass}>Themes</span>
                <input
                  className={fieldClass}
                  onChange={(event) => setForm((current) => ({ ...current, themes: event.target.value }))}
                  placeholder="family, rescue, grief"
                  value={form.themes}
                />
              </label>
              <label className="min-w-0">
                <span className={labelClass}>Related characters</span>
                <input
                  className={fieldClass}
                  onChange={(event) => setForm((current) => ({ ...current, related_characters: event.target.value }))}
                  placeholder="Elara, Lyra"
                  value={form.related_characters}
                />
              </label>
              <label className="min-w-0">
                <span className={labelClass}>Trigger conditions</span>
                <textarea
                  className={`${fieldClass} min-h-20 resize-y leading-6`}
                  onChange={(event) => setForm((current) => ({ ...current, trigger_conditions: event.target.value }))}
                  value={form.trigger_conditions}
                />
              </label>
              <label className="min-w-0">
                <span className={labelClass}>Cooldown scenes</span>
                <input
                  className={fieldClass}
                  max="50"
                  min="0"
                  onChange={(event) => setForm((current) => ({ ...current, cooldown_scenes: event.target.value }))}
                  step="1"
                  type="number"
                  value={form.cooldown_scenes}
                />
              </label>
            </div>
          ) : null}
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button className={textButton} onClick={() => setIsEditing(false)} type="button">
              Cancel
            </button>
            <button className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 transition hover:bg-white disabled:opacity-50" disabled={disabled} onClick={save} type="button">
              <Save size={15} />
              Save edit
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function EmptyState({ children }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-[#0d0e11] p-4 text-sm leading-6 text-muted">
      {children}
    </div>
  );
}

function StoryPronunciationPanel({ activeSession }) {
  const ttsSettings = useAppStore((state) => state.ttsSettings);
  const saveTTSSettings = useAppStore((state) => state.saveTTSSettings);
  const sessionId = activeSession?.id || null;
  const storyText = useMemo(
    () => pronunciationEntriesToText(ttsSettings.pronunciation_entries, sessionId),
    [sessionId, ttsSettings.pronunciation_entries],
  );
  const globalCount = (ttsSettings.pronunciation_entries || []).filter(
    (entry) => entry?.scope !== "story" && entry?.enabled !== false,
  ).length;
  const storyCount = (ttsSettings.pronunciation_entries || []).filter(
    (entry) => entry?.scope === "story" && entry?.story_id === sessionId && entry?.enabled !== false,
  ).length;
  const [draft, setDraft] = useState(storyText);

  useEffect(() => {
    setDraft(storyText);
  }, [storyText]);

  if (!sessionId) return null;

  const saveStoryPronunciations = () =>
    saveTTSSettings({
      pronunciation_entries: mergeStoryPronunciationEntries(
        ttsSettings.pronunciation_entries,
        sessionId,
        draft,
      ),
    }).catch(() => {});

  return (
    <div className="mb-4 rounded-lg border border-line bg-panelSoft p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Volume2 size={16} className="text-tide" />
            Story Pronunciation
          </div>
          <p className="mt-1 text-sm leading-6 text-muted">
            Add local aliases for names or places Kokoro misreads. These affect narration only.
          </p>
        </div>
        <span className="sd-chip shrink-0 rounded-full border border-line bg-[#0d0e11] px-2 py-0.5 text-xs text-muted">
          {storyCount} story / {globalCount} global
        </span>
      </div>
      <textarea
        className={`${fieldClass} min-h-24 resize-y leading-5`}
        onBlur={saveStoryPronunciations}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={"Kaelen => Kay-len\nMiri => Meer-ee"}
        value={draft}
      />
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs leading-5 text-muted">
          Global aliases live in Settings. Story aliases are applied only to this story.
        </span>
        <button className={textButton} onClick={saveStoryPronunciations} type="button">
          Save pronunciation
        </button>
      </div>
    </div>
  );
}

function StoryStatePanel({ activeSession }) {
  const storyState = useAppStore((state) => state.storyStateBySession[activeSession?.id] || null);
  const storyStateSettings = useAppStore((state) => state.storyStateSettings);
  const isRefreshingStoryState = useAppStore((state) => state.isRefreshingStoryState);
  const isRefreshingSummary = useAppStore((state) => state.isRefreshingSummary);
  const fetchStoryState = useAppStore((state) => state.fetchStoryState);
  const refreshSummary = useAppStore((state) => state.refreshSummary);
  const extractStoryStateForSelectedScene = useAppStore((state) => state.extractStoryStateForSelectedScene);
  const saveStoryStateSettings = useAppStore((state) => state.saveStoryStateSettings);
  const archiveStoryStateItem = useAppStore((state) => state.archiveStoryStateItem);
  const disableStoryStateItem = useAppStore((state) => state.disableStoryStateItem);
  const restoreStoryStateItem = useAppStore((state) => state.restoreStoryStateItem);
  const updateStoryStateItem = useAppStore((state) => state.updateStoryStateItem);
  const undoStoryStateRun = useAppStore((state) => state.undoStoryStateRun);
  const [undoTarget, setUndoTarget] = useState(null);

  const conflictItemIds = useMemo(() => {
    const ids = new Set();
    for (const conflict of storyState?.conflicts || []) {
      for (const id of conflict.item_ids || []) ids.add(id);
    }
    return ids;
  }, [storyState?.conflicts]);

  const liveByCharacter = useMemo(() => {
    const groups = new Map();
    for (const item of storyState?.character_live_state || []) {
      const name = item.character_name || "Unassigned";
      groups.set(name, [...(groups.get(name) || []), item]);
    }
    return Array.from(groups.entries());
  }, [storyState?.character_live_state]);
  const continuitySnapshot = useMemo(() => {
    const activeLive = (storyState?.character_live_state || []).filter((item) => !item.archived && !item.disabled);
    const keyIn = (item, keys) => keys.includes(String(item.key || "").toLowerCase());
    const positions = activeLive
      .filter((item) =>
        keyIn(item, ["current_location", "location", "room_position", "position", "spatial_position", "current_posture", "current_action"]),
      )
      .slice(0, 8);
    const appearance = activeLive
      .filter((item) =>
        keyIn(item, ["current_outfit", "outfit", "clothing", "hair_style", "visible_injuries"]) ||
        String(item.key || "").toLowerCase().includes("injury") ||
        String(item.key || "").toLowerCase().includes("wound"),
      )
      .slice(0, 8);
    const knowledge = activeLive
      .filter((item) => keyIn(item, ["known_secrets", "knows", "knowledge", "short_term_goal", "long_term_goal", "emotional_state"]))
      .slice(0, 8);
    const objects = (storyState?.objects || [])
      .filter((item) => !item.archived && !item.disabled)
      .slice(0, 8);
    const relationships = (storyState?.relationships || [])
      .filter((item) => !item.archived && !item.disabled)
      .slice(0, 6);
    return { appearance, knowledge, objects, positions, relationships };
  }, [storyState?.character_live_state, storyState?.objects, storyState?.relationships]);
  const latestRun = storyState?.latest_run || null;
  const summaryStatus = storyState?.summary_status || null;
  const summaryLabel = summaryStatus?.needs_summary ? "stale" : summaryStatus?.status || "not run yet";
  const summaryMeta =
    summaryStatus?.scenes_since_summary && summaryStatus.scenes_since_summary > 0
      ? `${summaryStatus.scenes_since_summary} scene${summaryStatus.scenes_since_summary === 1 ? "" : "s"} behind`
      : summaryStatus?.scene_count
        ? `${summaryStatus.scene_count} scene${summaryStatus.scene_count === 1 ? "" : "s"}`
        : "";

  const renderRows = (items, itemType, emptyText) =>
    items?.length ? (
      <div className="grid gap-2">
        {items.map((item) => (
          <StateRow
            disabled={isRefreshingStoryState}
            item={item}
            itemType={itemType}
            key={item.id}
            needsReview={conflictItemIds.has(item.id)}
            onArchive={archiveStoryStateItem}
            onDisable={disableStoryStateItem}
            onRestore={restoreStoryStateItem}
            onUpdate={updateStoryStateItem}
          />
        ))}
      </div>
    ) : (
      <EmptyState>{emptyText}</EmptyState>
    );

  return (
    <div className="grid gap-4">
      <section className="rounded-lg border border-line bg-panelSoft p-4">
        <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
              <BrainCircuit size={16} className="text-tide" />
              Automatic Story State
            </div>
            <p className="mt-1 text-sm leading-6 text-muted">
              Current story only. Story State supplies factual continuity for the next prose prompt; it does not override your system prompt or director note.
            </p>
          </div>
          <label className="flex min-h-10 shrink-0 items-center justify-between gap-3 rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-200">
            <span>Automatic</span>
            <input
              checked={storyStateSettings.automatic_story_state !== false}
              className="h-4 w-4 accent-moss"
              onChange={(event) =>
                saveStoryStateSettings({ automatic_story_state: event.target.checked }).catch(() => {})
              }
              type="checkbox"
            />
          </label>
        </div>

        <div className="mb-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_170px]">
          <label className="flex min-h-10 items-center justify-between gap-3 rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-200">
            <span>Run extraction in background</span>
            <input
              checked={storyStateSettings.run_state_extraction_in_background !== false}
              className="h-4 w-4 accent-moss"
              onChange={(event) =>
                saveStoryStateSettings({ run_state_extraction_in_background: event.target.checked }).catch(() => {})
              }
              type="checkbox"
            />
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-muted">Timeout seconds</span>
            <input
              className={fieldClass}
              min="10"
              onChange={(event) =>
                saveStoryStateSettings({
                  state_extraction_timeout_seconds: Number(event.target.value) || 120,
                }).catch(() => {})
              }
              step="10"
              type="number"
              value={storyStateSettings.state_extraction_timeout_seconds || 120}
            />
          </label>
        </div>

        <div className="grid gap-2 text-sm">
          <div className="flex justify-between gap-3">
            <span className="text-muted">Last extraction</span>
            <span
              className={
                latestRun?.status === "failed"
                  ? "text-ember"
                  : latestRun?.status === "stale"
                    ? "text-amber-200"
                    : latestRun
                      ? "text-moss"
                      : "text-muted"
              }
            >
              {latestRun?.status || "none yet"}
            </span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted">Used in next prompt</span>
            <span className="text-zinc-200">{storyState?.prompt_item_count || 0}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted">V3 memory pack</span>
            <span className="text-zinc-200">{storyState?.memory_pack_item_count || 0}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted">Skipped archived/disabled</span>
            <span className="text-muted">{storyState?.archived_item_count || 0}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted">Manual overrides</span>
            <span className="text-tide">{storyState?.manual_override_count || 0}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted">Conflicts</span>
            <span className={storyState?.conflicts?.length ? "text-amber-200" : "text-muted"}>{storyState?.conflicts?.length || 0}</span>
          </div>
          <div className="flex justify-between gap-3">
            <span className="text-muted">Summary</span>
            <span
              className={
                summaryStatus?.status === "failed"
                  ? "text-ember"
                  : summaryStatus?.needs_summary
                    ? "text-amber-200"
                    : summaryStatus?.status === "completed" || summaryStatus?.status === "current"
                      ? "text-moss"
                      : "text-muted"
              }
            >
              {summaryLabel}
              {summaryMeta ? ` · ${summaryMeta}` : ""}
            </span>
          </div>
          {latestRun?.error ? <p className="rounded-lg border border-ember/30 bg-ember/10 p-3 text-sm text-ember">{latestRun.error}</p> : null}
          {storyState?.summary_status?.error ? (
            <p className="rounded-lg border border-ember/30 bg-ember/10 p-3 text-sm text-ember">{storyState.summary_status.error}</p>
          ) : null}
          {summaryStatus?.needs_summary && summaryStatus?.status !== "failed" ? (
            <p className="rounded-lg border border-amber-400/20 bg-amber-400/10 p-3 text-sm leading-6 text-amber-100">
              The rolling overview is behind the latest scene. Refresh it when you want older events, relationships, and objects tightened for future prompts.
            </p>
          ) : null}
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            className={textButton}
            disabled={!activeSession || isRefreshingStoryState}
            onClick={() => fetchStoryState(activeSession.id)}
            type="button"
          >
            <RefreshCw size={15} className={isRefreshingStoryState ? "animate-spin" : ""} />
            Refresh
          </button>
          <button
            className={textButton}
            disabled={!activeSession || isRefreshingStoryState}
            onClick={() => extractStoryStateForSelectedScene()}
            type="button"
          >
            <BrainCircuit size={15} />
            Re-extract selected scene
          </button>
          <button
            className={textButton}
            disabled={!activeSession || isRefreshingSummary}
            onClick={() => refreshSummary(activeSession.id)}
            type="button"
          >
            <RefreshCw size={15} className={isRefreshingSummary ? "animate-spin" : ""} />
            Refresh summary
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-line bg-panelSoft p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-zinc-100">Continuity Snapshot</h3>
          <span className="text-xs text-muted">{storyState?.memory_pack_item_count || storyState?.prompt_item_count || 0} prompt item(s)</span>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Where / Blocking</div>
            {continuitySnapshot.positions.length ? (
              <div className="grid gap-1.5 text-sm text-zinc-200">
                {continuitySnapshot.positions.map((item) => (
                  <div className="safe-wrap" key={item.id}>
                    <span className="text-muted">{item.character_name}: </span>
                    {String(item.key || "").replaceAll("_", " ")} - {item.value}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">No current character positions tracked yet.</p>
            )}
          </div>
          <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Objects / Ownership</div>
            {continuitySnapshot.objects.length ? (
              <div className="grid gap-1.5 text-sm text-zinc-200">
                {continuitySnapshot.objects.map((item) => (
                  <div className="safe-wrap" key={item.id}>
                    <span className="text-muted">{item.name || item.object_key}: </span>
                    {item.value}
                    {item.owner_character_name ? ` · owner ${item.owner_character_name}` : ""}
                    {item.holder_character_name ? ` · holder ${item.holder_character_name}` : ""}
                    {item.current_location ? ` · ${item.current_location}` : ""}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">No current object ownership tracked yet.</p>
            )}
          </div>
          <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Appearance / Injuries</div>
            {continuitySnapshot.appearance.length ? (
              <div className="grid gap-1.5 text-sm text-zinc-200">
                {continuitySnapshot.appearance.map((item) => (
                  <div className="safe-wrap" key={item.id}>
                    <span className="text-muted">{item.character_name}: </span>
                    {String(item.key || "").replaceAll("_", " ")} - {item.value}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">No live appearance changes tracked yet.</p>
            )}
          </div>
          <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Relationships / Knowledge</div>
            {continuitySnapshot.relationships.length || continuitySnapshot.knowledge.length ? (
              <div className="grid gap-1.5 text-sm text-zinc-200">
                {continuitySnapshot.relationships.map((item) => (
                  <div className="safe-wrap" key={item.id}>
                    <span className="text-muted">{item.character_a_name} / {item.character_b_name}: </span>
                    {item.content}
                  </div>
                ))}
                {continuitySnapshot.knowledge.map((item) => (
                  <div className="safe-wrap" key={item.id}>
                    <span className="text-muted">{item.character_name}: </span>
                    {String(item.key || "").replaceAll("_", " ")} - {item.value}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">No relationship or knowledge stakes tracked yet.</p>
            )}
          </div>
        </div>
      </section>

      {storyState?.conflicts?.length ? (
        <section className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-4">
          <h3 className="text-sm font-semibold text-amber-100">Needs Review</h3>
          <div className="mt-2 grid gap-2">
            {storyState.conflicts.map((conflict) => (
              <div className="text-sm leading-6 text-amber-100" key={conflict.id}>
                {conflict.message}
                <span className="block text-xs text-muted">Use Edit, Archive, Disable, or Restore on the highlighted rows.</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <Section title="Characters" defaultOpen>
        {liveByCharacter.length ? (
          <div className="grid gap-3">
            {liveByCharacter.map(([name, items]) => (
              <article className="rounded-lg border border-line bg-panelSoft p-4" key={name}>
                <h3 className="mb-3 text-sm font-semibold text-zinc-100">{name}</h3>
                {renderRows(items, "character", "No live state for this character yet.")}
              </article>
            ))}
          </div>
        ) : (
          <EmptyState>Live character state will appear after the next completed scene is analyzed.</EmptyState>
        )}
      </Section>

      <Section title="Relationships" defaultOpen={false}>
        {renderRows(storyState?.relationships || [], "relationship", "No relationship changes tracked yet.")}
      </Section>

      <Section title="Emotional Memory" defaultOpen={false}>
        {renderRows(
          storyState?.emotional_memories || [],
          "emotional_memory",
          "High-impact grief, promises, betrayals, and unresolved emotional memories will appear here when relevant.",
        )}
      </Section>

      <Section title="Scene / Location" defaultOpen={false}>
        {renderRows(storyState?.scene_state || [], "scene", "No live scene or location state tracked yet.")}
      </Section>

      <Section title="World" defaultOpen={false}>
        {renderRows(storyState?.world_state || [], "world", "No live world changes tracked yet.")}
      </Section>

      <Section title="Objects / Inventory" defaultOpen={false}>
        {renderRows(storyState?.objects || [], "object", "No important objects tracked yet.")}
      </Section>

      <Section title="Plot Threads" defaultOpen={false}>
        {renderRows(
          (storyState?.plot_threads || []).map((item) => ({
            ...item,
            key: item.title || item.thread_key,
            value: item.content || item.status,
          })),
          "plot_thread",
          "No active or archived plot threads tracked yet.",
        )}
      </Section>

      <Section title="Recent Updates" defaultOpen={false}>
        {latestRun ? (
          <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-zinc-100">Latest extraction: {latestRun.status}</p>
                <p className="mt-1 break-all text-xs text-muted">
                  Scene {latestRun.scene_id}{latestRun.version_id ? ` / version ${latestRun.version_id}` : ""}
                </p>
              </div>
              {latestRun.status !== "undone" ? (
                <button className={textButton} disabled={isRefreshingStoryState} onClick={() => setUndoTarget(latestRun)} type="button">
                  Undo this extraction
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
        {storyState?.recent_events?.length ? (
          <div className="grid gap-2">
            {storyState.recent_events.map((event) => (
              <StateRow
                item={{
                  ...event,
                  key: `${event.character_name || "State"}: ${event.key?.replaceAll("_", " ")}`,
                  value: event.value,
                }}
                key={event.id}
              />
            ))}
          </div>
        ) : (
          <EmptyState>No state updates recorded yet.</EmptyState>
        )}
      </Section>

      <Section title="Used In Next Prompt" defaultOpen={false}>
        <div className="grid gap-3 text-sm text-muted">
          <div className="rounded-lg border border-line bg-[#0d0e11] p-3">
            {storyState?.memory_pack_item_count || 0} selected v3 memory item(s) will be included when available. {storyState?.prompt_item_count || 0} legacy state item(s) remain available for fallback and comparison. {storyState?.archived_item_count || 0} archived or disabled item(s) will be skipped.
            {storyState?.manual_override_count ? ` ${storyState.manual_override_count} manual override(s) are protected.` : ""}
          </div>
          {storyState?.memory_pack_context ? (
            <details className="rounded-lg border border-line bg-[#0d0e11] p-3" open>
              <summary className="cursor-pointer text-sm font-semibold text-zinc-100">Show v3 memory pack</summary>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-5 text-zinc-300">
                {storyState.memory_pack_context}
              </pre>
            </details>
          ) : null}
          {storyState?.prompt_context ? (
            <details className="rounded-lg border border-line bg-[#0d0e11] p-3">
              <summary className="cursor-pointer text-sm font-semibold text-zinc-100">Show legacy prose prompt state</summary>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-5 text-zinc-300">
                {storyState.prompt_context}
              </pre>
            </details>
          ) : (
            <EmptyState>No automatic state will be added to the next prompt yet.</EmptyState>
          )}
        </div>
      </Section>

      <AnimatePresence>
        {undoTarget ? (
          <ConfirmDialog
            body="This archives non-manual state items created by the selected extraction run. Raw scene text and character cards stay untouched."
            confirmLabel="Undo Extraction"
            onCancel={() => setUndoTarget(null)}
            onConfirm={async () => {
              await undoStoryStateRun(undoTarget.id);
              setUndoTarget(null);
            }}
            title="Undo this Story State extraction?"
          />
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export default function StoryDetailsDrawer({ activeSession, onClose }) {
  const activeSessionId = useAppStore((state) => state.activeSessionId);
  const fetchStoryContext = useAppStore((state) => state.fetchStoryContext);
  const isLoadingStoryDetails = useAppStore((state) => state.isLoadingStoryDetails);
  const sessionCharacters = useAppStore(
    (state) => state.sessionCharactersBySession[activeSession?.id || activeSessionId] || EMPTY_ARRAY,
  );
  const worldNotes = useAppStore((state) => state.worldNotesBySession[activeSession?.id || activeSessionId]);
  const foundation = useAppStore((state) => state.foundationsBySession[activeSession?.id || activeSessionId] || null);
  const qualityNotes = useAppStore(
    (state) => state.qualityNotesBySession[activeSession?.id || activeSessionId] || EMPTY_ARRAY,
  );
  const qualityHints = useAppStore(
    (state) => state.qualityHintsBySession[activeSession?.id || activeSessionId] || null,
  );
  const [tab, setTab] = useState("foundation");
  const session = activeSession || null;
  const activeCount = sessionCharacters.filter((link) => link.is_active).length;
  const worldSet = hasWorldNotes(worldNotes);
  const foundationSet = hasStoryFoundation(foundation);
  const qaCount =
    qualityNotes.filter((note) => note.status === "open").length + (qualityHints?.hints?.length || 0);

  useEffect(() => {
    fetchStoryContext(session?.id || null);
  }, [fetchStoryContext, session?.id]);

  const tabs = [
    ["foundation", BookOpen, "Foundation", foundationSet ? "set" : "unset"],
    ["characters", UsersRound, "Characters", activeCount],
    ["world", Globe2, "World", worldSet ? "set" : "unset"],
    ["state", BrainCircuit, "State", "auto"],
    ["quality", Check, "QA", qaCount || "quiet"],
  ];

  return (
    <motion.div
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-50 bg-black/55 backdrop-blur-sm"
      exit={{ opacity: 0 }}
      initial={{ opacity: 0 }}
    >
      <button aria-label="Dismiss story details" className="absolute inset-0" onClick={onClose} type="button" />
      <motion.aside
        animate={{ x: 0 }}
        className="sd-drawer absolute right-0 top-0 flex h-full w-full max-w-3xl flex-col overflow-x-hidden border-l border-line bg-panel shadow-glow"
        exit={{ x: 760 }}
        initial={false}
        transition={{ type: "spring", stiffness: 340, damping: 32 }}
      >
        <div className="shrink-0 border-b border-line p-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <UserRound size={18} className="text-moss" />
                <h2 className="text-lg font-semibold text-zinc-100">Story Details</h2>
              </div>
              <p className="mt-1 truncate text-sm text-muted">
                {session?.title || "Global cards and future story context"}
              </p>
            </div>
            <button
              aria-label="Close story details"
              className={iconButton}
              onClick={onClose}
              type="button"
            >
              <X size={17} />
            </button>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
            {tabs.map(([id, Icon, label, badge]) => (
              <button
                className={`flex min-h-10 items-center justify-center gap-2 rounded-lg border px-2 text-sm font-medium transition ${
                  tab === id
                    ? "border-tide/45 bg-tide/10 text-zinc-100"
                    : "border-line bg-panelSoft text-muted hover:border-zinc-600 hover:text-zinc-200"
                }`}
                key={id}
                onClick={() => setTab(id)}
                type="button"
              >
                <Icon size={16} />
                <span className="truncate">{label}</span>
                <span className="hidden rounded-full border border-line px-1.5 py-0.5 text-[11px] text-muted sm:inline">
                  {badge}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="story-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
          {isLoadingStoryDetails ? (
            <div className="mb-4 rounded-lg border border-line bg-panelSoft p-3 text-sm text-muted">
              Loading story details...
            </div>
          ) : null}
          {tab === "characters" ? <CharactersPanel activeSession={session} /> : null}
          {tab === "foundation" ? <FoundationPanel activeSession={session} /> : null}
          {tab === "world" ? <WorldPanel activeSession={session} /> : null}
          {tab === "state" ? (
            <>
              <StoryPronunciationPanel activeSession={session} />
              <StoryStatePanel activeSession={session} />
            </>
          ) : null}
          {tab === "quality" ? <QualityPanel activeSession={session} /> : null}
        </div>
      </motion.aside>
    </motion.div>
  );
}
