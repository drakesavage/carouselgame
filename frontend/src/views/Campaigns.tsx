import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Archive, ArchiveRestore, FolderOpen, Loader2, Pencil, Plus, Swords, Trash2 } from "lucide-react";
import { api, ManagedCampaign } from "../api";
import { Chip, Empty, Panel } from "../ui";

const when = (s: string | null) => (s ? new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z").toLocaleDateString() : "never");
const SOURCE: Record<string, string> = { demo: "demo", ledger_import: "imported", manual: "new" };

/** Every campaign on this install: open, start a new one, rename, archive, delete (backed up first; game PC only). */
export function CampaignsPanel({ cid, onCampaign }: { cid: number; onCampaign: (id: number | null) => void }) {
  const [list, setList] = useState<ManagedCampaign[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [doomed, setDoomed] = useState<ManagedCampaign | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const load = () => api.manageCampaigns().then(setList).catch((e) => setMsg(String(e)));
  useEffect(() => { load(); }, [cid]);

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setMsg(null);
    try { await fn(); if (done) setMsg(done); await load(); onCampaign(cid || null); } catch (e) { setMsg(String(e instanceof Error ? e.message : e)); }
  };
  const shown = (list ?? []).filter((c) => showArchived || !c.archived || c.id === cid);
  const archived = (list ?? []).filter((c) => c.archived).length;

  return (
    <Panel title={<><Swords size={13} /> Campaigns</>} right={<button className="btn small" onClick={() => setCreating(true)}><Plus size={12} /> New campaign</button>}>
      {msg && <div className="sub" style={{ marginBottom: 8 }}>{msg}</div>}
      {!list ? <div className="muted">Loading…</div> : shown.length === 0 ? <Empty>No campaigns yet. Start one, import the ledger, or open the demo.</Empty> : (
        <div className="camp-list">
          {shown.map((c) => (
            <div key={c.id} className={`camp-row${c.id === cid ? " current" : ""}`}>
              <div className="grow" style={{ minWidth: 0 }}>
                {renaming === c.id ? (
                  <form className="row" onSubmit={(e) => { e.preventDefault(); act(() => api.editCampaign(c.id, { name }), "Renamed.").then(() => setRenaming(null)); }}>
                    <input className="input" autoFocus value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
                    <button className="btn small" disabled={!name.trim()}>Save</button>
                    <button type="button" className="btn small ghost" onClick={() => setRenaming(null)}>Cancel</button>
                  </form>
                ) : (
                  <div className="camp-name">{c.name}</div>
                )}
                <div className="sub">
                  {c.character ?? "no character"}{c.current_location ? ` · ${c.current_location}` : ""}
                </div>
                <div className="chips" style={{ marginTop: 4 }}>
                  {c.id === cid && <Chip tone="cyan">open now</Chip>}
                  <Chip tone={c.is_demo ? "amber" : ""}>{SOURCE[c.data_source] ?? c.data_source}</Chip>
                  {c.archived ? <Chip tone="violet">archived</Chip> : null}
                  <Chip>{c.turns} turn{c.turns === 1 ? "" : "s"}</Chip>
                  <Chip title="Last turn played">played {when(c.last_played)}</Chip>
                </div>
              </div>
              <div className="camp-actions">
                {c.id !== cid && <button className="btn small" onClick={() => onCampaign(c.id)}><FolderOpen size={12} /> Open</button>}
                <button className="btn small ghost" title="Rename" onClick={() => { setRenaming(c.id); setName(c.name); }}><Pencil size={12} /></button>
                <button className="btn small ghost" title={c.archived ? "Unarchive: show it in the campaign picker again" : "Archive: hide it from the campaign picker (nothing is removed)"}
                  onClick={() => act(() => api.editCampaign(c.id, { archived: !c.archived }), c.archived ? `“${c.name}” is back in the picker.` : `“${c.name}” archived.`)}>
                  {c.archived ? <ArchiveRestore size={12} /> : <Archive size={12} />}
                </button>
                <button className="btn small ghost danger-text" title="Delete" onClick={() => setDoomed(c)}><Trash2 size={12} /></button>
              </div>
            </div>
          ))}
        </div>
      )}
      {archived > 0 && (
        <button className="btn small ghost" style={{ marginTop: 8 }} onClick={() => setShowArchived(!showArchived)}>
          {showArchived ? "Hide archived" : `Show ${archived} archived`}
        </button>
      )}
      <NewCampaign open={creating} onClose={() => setCreating(false)} onMade={(id) => { setCreating(false); load(); onCampaign(id); }} />
      <DeleteCampaign target={doomed} onClose={() => setDoomed(null)} onDeleted={(out) => {
        const next = (list ?? []).find((c) => c.id !== doomed?.id && !c.archived) ?? (list ?? []).find((c) => c.id !== doomed?.id);
        setDoomed(null);
        // a toast: the settings view reloads when the open campaign changes, which would drop a message here
        window.dispatchEvent(new CustomEvent("crawler:announce", { detail: { text: `Deleted “${out.deleted}”. Backup ${out.backup} holds it and its pictures if you ever need it back.`, style: "notice" } }));
        load();
        onCampaign(doomed?.id === cid ? next?.id ?? null : cid);
      }} />
    </Panel>
  );
}

function NewCampaign({ open, onClose, onMade }: { open: boolean; onClose: () => void; onMade: (id: number) => void }) {
  const blank = { name: "", character: "", location: "", premise: "" };
  const [f, setF] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (open) { setF(blank); setErr(null); } }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api.createCampaign({
        name: f.name.trim(), character: f.character.trim(), location: f.location.trim() || null, premise: f.premise.trim() || null,
      });
      onMade(r.campaign_id);
    } catch (e) { setErr(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  };
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="modal" aria-describedby={undefined}>
          <Dialog.Title className="h1" style={{ fontSize: 17 }}>New campaign</Dialog.Title>
          <form className="camp-form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <label>Campaign name<input className="input" required maxLength={80} value={f.name} onChange={set("name")} placeholder="e.g. The Long Drive In" /></label>
            <label>Your character's name<input className="input" required maxLength={60} value={f.character} onChange={set("character")} /></label>
            <label>Starting location <span className="sub">(optional)</span><input className="input" maxLength={120} value={f.location} onChange={set("location")} /></label>
            <label>Premise <span className="sub">(optional: what the GM should know going in; the party knows it too)</span>
              <textarea className="input" rows={3} maxLength={2000} value={f.premise} onChange={set("premise")} /></label>
            <div className="sub">Stats, Archetype and tickets start empty: Carousel assigns them in play. Nothing is made up for you.</div>
            {err && <div className="sub" style={{ color: "var(--red)" }}>{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <Dialog.Close type="button" className="btn ghost">Cancel</Dialog.Close>
              <button className="btn" disabled={busy || !f.name.trim() || !f.character.trim()}>{busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Start campaign</button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function DeleteCampaign({ target, onClose, onDeleted }: { target: ManagedCampaign | null; onClose: () => void; onDeleted: (r: { deleted: string; backup: string }) => void }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setTyped(""); setErr(null); }, [target]);
  const go = async () => {
    if (!target) return;
    setBusy(true); setErr(null);
    try { onDeleted(await api.deleteCampaign(target.id, typed)); } catch (e) { setErr(String(e instanceof Error ? e.message : e)); } finally { setBusy(false); }
  };
  return (
    <Dialog.Root open={!!target} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="modal" aria-describedby={undefined}>
          <Dialog.Title className="h1" style={{ fontSize: 17 }}>Delete “{target?.name}”?</Dialog.Title>
          <p className="sub">
            This removes its story, characters, items, saves and pictures from the app ({target?.turns ?? 0} turn{target?.turns === 1 ? "" : "s"}). A backup is made first and its
            pictures are moved into it, so it can be brought back by restoring that backup (docs/OPERATIONS.md). The Git ledger is not touched.
            Only possible on the game PC. If you just want it out of the way, archive it instead.
          </p>
          <label>Type the campaign's name to confirm<input className="input" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={target?.name} /></label>
          {err && <div className="sub" style={{ color: "var(--red)", marginTop: 6 }}>{err}</div>}
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}>
            <Dialog.Close className="btn ghost">Cancel</Dialog.Close>
            <button className="btn danger" disabled={busy || typed.trim() !== target?.name} onClick={go}>{busy ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />} Delete campaign</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
