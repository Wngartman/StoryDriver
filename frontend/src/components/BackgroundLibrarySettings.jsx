import {
  Check,
  Copy,
  Image,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { useRef, useState } from "react";
import { API_BASE_URL } from "../api.js";
import { DEFAULT_UI_SETTINGS } from "../services/displaySettings.js";
import { useAppStore } from "../store/useAppStore.js";
import { SettingLabel } from "./SettingHelp.jsx";


const fieldClass = "sd-field min-h-10 w-full rounded-lg border border-line bg-[#0d0e11] px-3 text-sm text-zinc-100";
const labelClass = "mb-1.5 block text-xs font-medium uppercase tracking-wide text-muted";

export default function BackgroundLibrarySettings() {
  const inputRef = useRef(null);
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  const backgroundLibrary = useAppStore((state) => state.backgroundLibrary);
  const backgroundMessage = useAppStore((state) => state.backgroundMessage);
  const deleteBackground = useAppStore((state) => state.deleteBackground);
  const fetchBackgrounds = useAppStore((state) => state.fetchBackgrounds);
  const isLoading = useAppStore((state) => state.isLoadingBackgrounds);
  const renameBackground = useAppStore((state) => state.renameBackground);
  const saveUiSettings = useAppStore((state) => state.saveUiSettings);
  const settings = useAppStore((state) => state.uiSettings);
  const uploadBackgrounds = useAppStore((state) => state.uploadBackgrounds);
  const items = backgroundLibrary.items || [];
  const selectedIds = settings.background_selected_ids || [];
  const selected = new Set(selectedIds);

  const update = (patch) => saveUiSettings(patch).catch(() => {});
  const toggle = (itemId) => {
    const next = selected.has(itemId) ? selectedIds.filter((id) => id !== itemId) : [...selectedIds, itemId];
    update({ background_selected_ids: next });
  };
  const addFiles = (files) => uploadBackgrounds(files).catch(() => {});

  return (
    <div className="grid gap-4">
      <div
        className="grid min-h-24 place-items-center rounded-lg border border-dashed border-line bg-black/10 px-4 py-3 text-center"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          addFiles(event.dataTransfer.files);
        }}
      >
        <Image className="text-tide" size={20} />
        <p className="mt-1 text-sm text-zinc-200">Drop local PNG, JPG, or WebP files here</p>
        <p className="mt-1 break-all text-xs text-muted">{backgroundLibrary.folder || "D:\\StoryDriver\\backend\\data\\assets\\backgrounds"}</p>
        <div className="mt-3 flex flex-wrap justify-center gap-2">
          <button className="sd-action-button inline-flex min-h-10 items-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200" onClick={() => inputRef.current?.click()} type="button">
            <Upload size={15} /> Upload
          </button>
          <button className="sd-action-button inline-flex min-h-10 items-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200" disabled={isLoading} onClick={() => fetchBackgrounds(true)} type="button">
            <RefreshCw className={isLoading ? "animate-spin" : ""} size={15} /> Refresh Backgrounds
          </button>
          <button className="sd-action-button inline-flex min-h-10 items-center gap-2 rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200" onClick={() => navigator.clipboard?.writeText(backgroundLibrary.folder)} type="button">
            <Copy size={15} /> Copy folder
          </button>
        </div>
        <input accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" className="sr-only" multiple onChange={(event) => addFiles(event.target.files)} ref={inputRef} type="file" />
      </div>

      {backgroundMessage ? <p className="text-xs text-muted">{backgroundMessage}</p> : null}

      {items.length ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-muted">{selectedIds.length} of {items.length} selected</span>
            <div className="flex gap-2">
              <button className="min-h-9 rounded-lg border border-line px-3 text-xs text-zinc-200" onClick={() => update({ background_selected_ids: items.map((item) => item.id) })} type="button">Select all</button>
              <button className="min-h-9 rounded-lg border border-line px-3 text-xs text-zinc-200" onClick={() => update({ background_selected_ids: [] })} type="button">None</button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {items.map((item) => (
              <div className={`relative overflow-hidden rounded-lg border ${selected.has(item.id) ? "border-tide/60 bg-tide/10" : "border-line bg-panelSoft"}`} key={item.id}>
                <button aria-label={`${selected.has(item.id) ? "Deselect" : "Select"} background ${item.display_name}`} className="block w-full text-left" onClick={() => toggle(item.id)} type="button">
                  <img alt="" className="aspect-[4/3] w-full object-cover" loading="lazy" src={`${API_BASE_URL}${item.url}`} />
                  <span className="block truncate px-2 py-2 text-xs text-zinc-200">{item.display_name}</span>
                  {selected.has(item.id) ? <Check className="absolute right-2 top-2 rounded-full bg-black/70 p-1 text-tide" size={22} /> : null}
                </button>
                <div className="flex border-t border-line">
                  <button
                    aria-label={`Rename ${item.display_name}`}
                    className="grid min-h-10 flex-1 place-items-center text-muted hover:text-zinc-100"
                    onClick={() => {
                      const name = window.prompt("Background display name", item.display_name);
                      if (name?.trim()) renameBackground(item.id, name.trim()).catch(() => {});
                    }}
                    type="button"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    aria-label={`${pendingDeleteId === item.id ? "Confirm remove" : "Remove"} ${item.display_name}`}
                    className="grid min-h-10 flex-1 place-items-center border-l border-line text-muted hover:text-ember"
                    onClick={() => {
                      if (pendingDeleteId === item.id) {
                        deleteBackground(item.id).finally(() => setPendingDeleteId(null));
                      } else setPendingDeleteId(item.id);
                    }}
                    type="button"
                  >
                    {pendingDeleteId === item.id ? <span className="px-2 text-[11px] font-semibold text-ember">Confirm</span> : <Trash2 size={14} />}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="text-sm text-muted">No valid local backgrounds found.</p>
      )}

      <div className="grid gap-3 border-t border-line pt-4 min-[500px]:grid-cols-2">
        <label><SettingLabel className={labelClass} name="Opacity" /><input className={fieldClass} max="0.6" min="0" onChange={(event) => update({ background_opacity: Number(event.target.value) })} step="0.01" type="range" value={settings.background_opacity} /></label>
        <label><SettingLabel className={labelClass} name="Dim overlay" /><input className={fieldClass} max="0.9" min="0" onChange={(event) => update({ background_dim: Number(event.target.value) })} step="0.05" type="range" value={settings.background_dim} /></label>
        <label><SettingLabel className={labelClass} name="Blur" /><input className={fieldClass} max="24" min="0" onChange={(event) => update({ background_blur: Number(event.target.value) })} type="range" value={settings.background_blur} /></label>
        <label><SettingLabel className={labelClass} name="Saturation" /><input className={fieldClass} max="1.5" min="0" onChange={(event) => update({ background_saturation: Number(event.target.value) })} step="0.05" type="range" value={settings.background_saturation} /></label>
        <label><SettingLabel className={labelClass} name="Fit" /><select className={fieldClass} onChange={(event) => update({ background_fit: event.target.value })} value={settings.background_fit}><option value="cover">Cover</option><option value="contain">Contain</option><option value="fill">Fill</option></select></label>
        <label><SettingLabel className={labelClass} name="Position" /><select className={fieldClass} onChange={(event) => update({ background_position: event.target.value })} value={settings.background_position}><option value="center">Center</option><option value="top">Top</option><option value="bottom">Bottom</option><option value="left">Left</option><option value="right">Right</option></select></label>
        <label><SettingLabel className={labelClass} name="Attachment" /><select className={fieldClass} onChange={(event) => update({ background_attachment: event.target.value })} value={settings.background_attachment}><option value="fixed">Fixed</option><option value="scroll">Scroll</option></select></label>
        <label><SettingLabel className={labelClass} name="Rotation" /><select className={fieldClass} onChange={(event) => update({ background_rotation_seconds: Number(event.target.value) })} value={settings.background_rotation_seconds}><option value="0">Off</option><option value="60">1 minute</option><option value="300">5 minutes</option><option value="900">15 minutes</option><option value="1800">30 minutes</option><option value="3600">60 minutes</option>{![0,60,300,900,1800,3600].includes(settings.background_rotation_seconds) ? <option value={settings.background_rotation_seconds}>Custom</option> : null}</select></label>
        <label><SettingLabel className={labelClass} name="Custom rotation seconds" /><input className={fieldClass} max="86400" min="30" onChange={(event) => update({ background_rotation_seconds: Number(event.target.value) })} type="number" value={settings.background_rotation_seconds || 60} /></label>
        <label><SettingLabel className={labelClass} name="Rotation order" /><select className={fieldClass} onChange={(event) => update({ background_rotation_order: event.target.value })} value={settings.background_rotation_order}><option value="ordered">Ordered</option><option value="random">Random</option></select></label>
        <label><SettingLabel className={labelClass} name="Fade seconds" /><input className={fieldClass} max="10" min="0" onChange={(event) => update({ background_fade_seconds: Number(event.target.value) })} step="0.1" type="number" value={settings.background_fade_seconds} /></label>
        <label className="flex min-h-10 items-center gap-2 text-sm text-zinc-300"><input checked={settings.background_pause_while_writing} onChange={(event) => update({ background_pause_while_writing: event.target.checked })} type="checkbox" />Pause rotation while writing</label>
      </div>
      <button className="sd-action-button inline-flex min-h-10 items-center justify-center rounded-lg border border-line bg-panel px-3 text-sm text-zinc-200" onClick={() => update({
        background_selected_ids: [],
        background_opacity: DEFAULT_UI_SETTINGS.background_opacity,
        background_blur: DEFAULT_UI_SETTINGS.background_blur,
        background_dim: DEFAULT_UI_SETTINGS.background_dim,
        background_saturation: DEFAULT_UI_SETTINGS.background_saturation,
        background_position: DEFAULT_UI_SETTINGS.background_position,
        background_fit: DEFAULT_UI_SETTINGS.background_fit,
        background_attachment: DEFAULT_UI_SETTINGS.background_attachment,
        background_rotation_seconds: 0,
        background_rotation_order: DEFAULT_UI_SETTINGS.background_rotation_order,
        background_fade_seconds: DEFAULT_UI_SETTINGS.background_fade_seconds,
        background_pause_while_writing: DEFAULT_UI_SETTINGS.background_pause_while_writing,
      })} type="button">Restore background defaults</button>
    </div>
  );
}
