import { useEffect, useState } from "react";
import { RefreshCw, Save } from "lucide-react";
import { API_BASE_URL } from "../api.js";

const field = "sd-field w-full min-h-10 rounded-md border border-line bg-panelSoft px-3 text-sm";

export default function DesktopSettings() {
  const [info, setInfo] = useState(null);
  const [draft, setDraft] = useState(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    fetch(`${API_BASE_URL}/system/info`, { signal: abort.signal })
      .then(async (response) => { if (!response.ok) throw new Error("App settings are unavailable."); return response.json(); })
      .then((result) => { setInfo(result); setDraft(result.preferences); })
      .catch((error) => { if (error.name !== "AbortError") setMessage(error.message); });
    return () => abort.abort();
  }, []);
  const save = async () => {
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE_URL}/system/preferences`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(draft),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not save app settings.");
      setMessage("Saved. Quit and reopen StoryDriver to apply startup changes.");
    } catch (error) { setMessage(error.message); }
    finally { setBusy(false); }
  };
  return <section className="grid gap-4">
    <div className="flex items-center justify-between gap-3"><h3 className="font-semibold">StoryDriver</h3><span className="text-sm text-muted">{info?.version || "Loading..."}</span></div>
    {draft ? <>
      <label className="flex min-h-11 items-center gap-3"><input type="checkbox" checked={draft.lanEnabled} onChange={(e) => setDraft({ ...draft, lanEnabled: e.target.checked })} /><span>Allow private LAN access</span></label>
      <label className="flex min-h-11 items-center gap-3"><input type="checkbox" checked={draft.minimizeToTray} onChange={(e) => setDraft({ ...draft, minimizeToTray: e.target.checked })} /><span>Keep running in tray when closed</span></label>
      <div className="border-t border-line pt-4 text-xs text-muted">Data folder</div>
      <div className="safe-wrap select-text text-sm">{info.data_root}</div>
      <details className="border-y border-line py-4">
        <summary className="cursor-pointer text-sm font-medium">Local narration setup</summary>
        <div className="mt-4 grid gap-3">
          <label className="grid gap-1.5 text-xs text-muted">Kokoro-FastAPI folder<input className={field} value={draft.KOKORO_WORKING_DIR} placeholder="Path to Kokoro-FastAPI" onChange={(e) => setDraft({ ...draft, KOKORO_WORKING_DIR: e.target.value })} /></label>
          <label className="grid gap-1.5 text-xs text-muted">Kokoro Python executable<input className={field} value={draft.KOKORO_PYTHON_EXE} placeholder="Path to its isolated Python environment" onChange={(e) => setDraft({ ...draft, KOKORO_PYTHON_EXE: e.target.value })} /></label>
        </div>
      </details>
      <button type="button" className="sd-action-button inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-line px-3 text-sm disabled:opacity-50" disabled={busy} onClick={save}>{busy ? <RefreshCw size={16} className="animate-spin" /> : <Save size={16} />}Save app settings</button>
    </> : null}
    {message ? <p className="safe-wrap text-sm text-muted" role="status">{message}</p> : null}
  </section>;
}
