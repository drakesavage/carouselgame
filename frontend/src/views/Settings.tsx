import { useEffect, useState } from "react";
import { DeviceSettings } from "./DeviceSettings";
import { BackupsPanel, RestorePreview } from "./Backups";
import { CampaignsPanel } from "./Campaigns";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, Bot, Copy, Database, Eye, EyeOff, History, KeyRound, Save, Smartphone } from "lucide-react";
import { api, Prefs, SavePoint } from "../api";
import { Chip, Empty, Panel } from "../ui";
import { Diagnostics } from "./Diagnostics";
import { RecordsView, RulesView } from "./Reference";

export function SettingsView({ cid, version, onChanged, onCampaign }: { cid: number; version: number; onChanged: () => void; onCampaign: (id: number | null) => void }) {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [saves, setSaves] = useState<SavePoint[] | null>(null);
  const [label, setLabel] = useState("");
  const [confirm, setConfirm] = useState<SavePoint | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const [access, setAccess] = useState<Awaited<ReturnType<typeof api.access>> | null>(null);
  useEffect(() => { api.settings().then(setPrefs); api.access().then(setAccess).catch(() => setAccess(null)); }, []);
  const [showCode, setShowCode] = useState(false);  // the device passcode stays masked until deliberately revealed
  useEffect(() => { if (cid) api.saves(cid).then(setSaves).catch(() => setSaves([])); }, [cid, version]);

  const update = async (p: Partial<Prefs>) => { setPrefs(await api.saveSettings(p)); setMsg("Settings saved."); };
  const save = async () => {
    try { await api.createSave(cid, label || `Manual save ${new Date().toLocaleString()}`); setLabel(""); setSaves(await api.saves(cid)); onChanged(); setMsg("Save point created."); }
    catch (e) { setMsg(String(e)); }
  };
  const restore = async (sp: SavePoint) => {
    try { const r = await api.restore(cid, sp.id); setMsg(`Restored “${sp.label}”. An automatic backup (#${r.backup_save_point_id}) lets you undo this.`); setSaves(await api.saves(cid)); onChanged(); }
    catch (e) { setMsg(String(e)); }
    setConfirm(null);
  };
  return (
    <div className="view-inner">
      <div className="kicker">Save &amp; recover</div>
      <h1 className="h1">Saves &amp; Settings</h1>
      {msg && <div className="card settings-msg">{msg}</div>}

      <section className="settings-section" aria-labelledby="set-saves">
        <h2 className="h2" id="set-saves">Saves</h2>
        <div className="settings-grid">
        <Panel title={<><Save size={13} /> Save points</>}>
          <div className="row"><input className="input" placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} /><button className="btn primary" onClick={save}>Save</button></div>
          <div className="sub" style={{ margin: "8px 0" }}>Saving happens only at camp (Dyer's Lodge, between stories): turns there autosave (last 25 kept). Restoring hides later transcript/events but keeps them in the audit log.</div>
          {!saves ? null : saves.length === 0 ? <Empty>No saves.</Empty> : (
            <table className="data"><tbody>{saves.map((s) => (
              <tr key={s.id}><td><div>{s.label}</div><div className="sub">{new Date(s.created_at).toLocaleString()}</div></td>
                <td><Chip tone={s.kind === "manual" ? "cyan" : s.kind === "pre_restore" ? "amber" : ""}>{s.kind.replace("_", " ")}</Chip></td>
                <td style={{ textAlign: "right" }}><button className="btn small" onClick={() => setConfirm(s)}><History size={12} /> Restore</button></td></tr>
            ))}</tbody></table>
          )}
        </Panel>

        <BackupsPanel />
        </div>
      </section>

      <section className="settings-section" id="campaigns" aria-labelledby="set-campaigns">
        <h2 className="h2" id="set-campaigns">Campaigns</h2>
        <CampaignsPanel cid={cid} onCampaign={onCampaign} />
      </section>

      <section className="settings-section" aria-labelledby="set-device">
        <h2 className="h2" id="set-device">Device</h2>
        <div className="settings-grid">
        <DeviceSettings />
        {access && (
          <Panel title={<><Smartphone size={13} /> Other devices</>}>
            <div className="sub" style={{ marginBottom: 8 }}>Through Barcode Battles on port {access.port}. Devices other than this PC need the passcode once.</div>
            {(access.lan_addresses.length ? access.lan_addresses : ["<this PC's address>"]).map((ip) => (
              <div key={ip} className="mono" style={{ fontSize: 14 }}>http://{ip}:{access.port}{access.path}</div>
            ))}
            <div className="row" style={{ marginTop: 10 }}>
              <span className="sub">Passcode</span>
              <span className="mono" style={{ fontSize: 22, letterSpacing: "0.12em", color: "var(--cyan)" }} aria-live="polite">
                {showCode ? access.passcode : "•".repeat(Math.max(6, access.passcode.length))}</span>
              <button className="btn small ghost" onClick={() => setShowCode((v) => !v)} aria-pressed={showCode}>{showCode ? <><EyeOff size={12} /> Hide</> : <><Eye size={12} /> Show</>}</button>
              <button className="btn small ghost" onClick={async () => { try { await navigator.clipboard.writeText(access.passcode); setMsg("Passcode copied."); } catch { setMsg("Copy failed; use Show instead."); } }}><Copy size={12} /> Copy</button>
              <span className="spacer" />
              {!access.fixed_by_env && (
                <button className="btn small" onClick={async () => {
                  if (!window.confirm("Make a new passcode? Every other device will need the new one to sign in again.")) return;
                  const r = await api.regeneratePasscode(); setAccess({ ...access, passcode: r.passcode }); setShowCode(false); setMsg("New passcode set; every other device must sign in again.");
                }}>
                  <KeyRound size={12} /> New passcode
                </button>
              )}
            </div>
            {access.fixed_by_env && <div className="sub">Set by CAROUSEL_PASSCODE in .env.</div>}
          </Panel>
        )}
        </div>
      </section>

      <section className="settings-section" aria-labelledby="set-gm">
        <h2 className="h2" id="set-gm">GM provider</h2>
        <div className="settings-grid">
        <Panel title={<><Bot size={13} /> The GM</>}>
          {prefs && (
            <div style={{ display: "grid", gap: 10 }}>
              <label>Provider
                <select className="select input" value={prefs.gm_provider} onChange={(e) => update({ gm_provider: e.target.value })}>
                  <option value="auto">Auto: Gemini for questions and conversation, Codex for mechanics</option>
                  <option value="codex">Codex CLI (your ChatGPT sign-in — no API billing)</option>
                  <option value="gemini">Gemini API (API key; free tier has rate limits)</option>
                  <option value="mock">Mock GM (offline testing, scripted)</option>
                </select>
              </label>
              {prefs.gm_provider === "auto" && <AutoInfo prefs={prefs} update={update} />}
              {prefs.gm_provider === "gemini" ? <>
              <div className="sub">Gemini key: {prefs.gemini_key_present ? "found" : <span style={{ color: "var(--red)" }}>not found — set GEMINI_API_KEY or put it in data\gemini_api_key.txt</span>}</div>
              <div className="flag warn">Free-tier Gemini inputs and outputs may be used by Google to improve its products. Every GM call sends the full briefing, including private companion sheets and GM secrets.</div>
              <label>Model
                <input className="input" defaultValue={prefs.gemini_model} placeholder="gemini-3.8-flash" onBlur={(e) => e.target.value !== prefs.gemini_model && update({ gemini_model: e.target.value })} />
              </label>
              </> : <>
              <div className="sub">Codex CLI: {prefs.codex_cli_path ? <span className="mono">{prefs.codex_cli_path}</span> : <span style={{ color: "var(--red)" }}>not found — install/sign in to Codex or set CODEX_BIN</span>}</div>
              <label>Model <span className="sub">(blank = your Codex default)</span>
                <input className="input" defaultValue={prefs.codex_model} placeholder="e.g. gpt-5.5" onBlur={(e) => e.target.value !== prefs.codex_model && update({ codex_model: e.target.value })} />
              </label>
              </>}
              <label>Briefing size
                <select className="select input" value={prefs.context_profile ?? "standard"} onChange={(e) => update({ context_profile: e.target.value })}>
                  <option value="standard">Standard (~20k tokens; the GM looks up the rest)</option>
                  <option value="large">Large (~80–100k tokens: whole party, every private sheet and house rule, ledger state files)</option>
                </select>
              </label>
              <label>Reasoning effort
                <select className="select input" value={prefs.codex_reasoning_effort} onChange={(e) => update({ codex_reasoning_effort: e.target.value })}>
                  {prefs.efforts.map((x) => <option key={x}>{x}</option>)}
                </select>
              </label>
              <div className="sub">{prefs.gm_provider === "auto" ? "Codex turns: sandboxed, read-only codex exec calls; Gemini turns: API calls that can only return JSON." : prefs.gm_provider === "gemini" ? "Each turn is one or more Gemini API calls that can only return JSON (no model-side tools)." : <>Each turn is one or more sandboxed, read-only <span className="mono">codex exec</span> calls.</>} Dice and state changes always run in this app, never in the model.</div>
            </div>
          )}
        </Panel>
        </div>
      </section>

      <section className="settings-section" id="behind-the-scenes" aria-labelledby="set-bts">
        <h2 className="h2" id="set-bts">Behind the Scenes</h2>
        <p className="sub">The rules, the records and the machinery. None of it is the story.</p>
        {cid ? <>
          <h3 className="settings-sub">Rules</h3>
          <RulesView cid={cid} />
          <h3 className="settings-sub">Records</h3>
          <RecordsView cid={cid} />
        </> : null}
        <h3 className="settings-sub">Consistency</h3>
        <div className="settings-grid">
        <Panel title={<><Database size={13} /> Record consistency</>}>
          <Consistency cid={cid} version={version} />
        </Panel>
        </div>
      <details className="card" style={{ marginTop: 14 }}>
        <summary style={{ cursor: "pointer" }}><b>Import visual reference</b> <span className="sub">(physical descriptions for sheets and portraits)</span></summary>
        <div style={{ marginTop: 12 }}><VisualReferenceImport cid={cid} /></div>
      </details>
      <details className="card" style={{ marginTop: 14 }}>
        <summary style={{ cursor: "pointer" }}><b>GM diagnostics</b> <span className="sub">(development: context packets, tool rounds, dice, integrity checks; GM token required)</span></summary>
        <div style={{ marginTop: 12 }}>{cid ? <Diagnostics cid={cid} /> : <div className="muted">Open a campaign first.</div>}</div>
      </details>
      </section>

      <Dialog.Root open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="modal" aria-describedby={undefined}>
            <Dialog.Title className="h1" style={{ fontSize: 17 }}>Restore “{confirm?.label}”?</Dialog.Title>
            <p className="sub">Current state is backed up automatically first, so this can be undone. Later turns are hidden from the transcript but remain in the audit log.</p>
            {confirm && <RestorePreview cid={cid} saveId={confirm.id} />}
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <Dialog.Close className="btn ghost">Cancel</Dialog.Close>
              <button className="btn danger" onClick={() => confirm && restore(confirm)}>Restore</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}


/** Auto GM: what goes where, and today's Gemini quota. */
/** Does each fact have one value across the records, the HUD and the GM briefing? Read-only (app/consistency.py). */
function Consistency({ cid, version }: { cid: number; version: number }) {
  const [r, setR] = useState<Awaited<ReturnType<typeof api.consistency>> | null>(null);
  useEffect(() => { api.consistency(cid).then(setR).catch(() => setR(null)); }, [cid, version]);
  if (!r) return null;
  const failed = r.checks.filter((c) => !c.ok);
  return (
    <details style={{ marginTop: 10 }} open={failed.length > 0}>
      <summary className="row" style={{ cursor: "pointer" }}>{failed.length ? <AlertTriangle size={13} color="var(--amber)" /> : null}
        Record consistency: {r.checks.length - failed.length}/{r.checks.length} agree</summary>
      <ul>{[...failed, ...r.checks.filter((c) => c.ok)].map((c, i) => (
        <li key={i} style={{ fontSize: 13 }}><Chip tone={c.ok ? "green" : "amber"}>{c.ok ? "agrees" : "differs"}</Chip> <b>{c.check}</b>{" "}
          <span className="sub">{Object.entries(c.values).map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</span></li>))}</ul>
    </details>
  );
}

function AutoInfo({ prefs, update }: { prefs: Prefs; update: (p: Partial<Prefs>) => Promise<void> }) {
  const [q, setQ] = useState<Record<string, any> | null>(null);
  useEffect(() => { api.geminiQuota().then(setQ).catch(() => {}); }, []);
  return (
    <div className="card" style={{ display: "grid", gap: 6 }}>
      <div className="sub">Recaps, status and rules questions → Gemini 3.5 Flash Lite. Conversation and roleplay → Gemini 3.8, then 3.6.
        Anything with rolls, combat, money, items, travel or rest, and every combat turn → Codex. A Gemini turn that turns out to need
        mechanics is handed to Codex before anything is applied. Summaries use Flash Lite.</div>
      {q && <div className="chips">{Object.entries(q.models as Record<string, any>).map(([m, v]) => (
        <span key={m} className={`chip ${v.spent || v.used >= v.limit ? "red" : v.limit - v.used < 5 ? "amber" : "green"}`}>{m.replace("gemini-", "")} {v.used}/{v.limit}</span>))}
        <span className="sub">today (resets midnight Pacific{q.key_present ? "" : "; no Gemini key found"})</span></div>}
      <div className="flag warn">Free-tier Gemini inputs and outputs may be used by Google to improve its products, and Gemini turns carry private sheets and GM secrets.</div>
      <div className="row">
        <label className="grow">Gemini thinking
          <select className="select input" value={prefs.gemini_effort ?? "medium"} onChange={(e) => update({ gemini_effort: e.target.value })}>
            {["low", "medium", "high"].map((x) => <option key={x}>{x}</option>)}
          </select>
        </label>
        <label className="grow">Gemini briefing
          <select className="select input" value={prefs.gemini_context_profile ?? "large"} onChange={(e) => update({ gemini_context_profile: e.target.value })}>
            <option value="large">Large (~60k tokens)</option><option value="standard">Standard (~20k)</option>
          </select>
        </label>
      </div>
    </div>
  );
}


/** Paste a visual reference (## Name, **demographic | age | class**, paragraphs, Signature, Expression). */
export function VisualReferenceImport({ cid }: { cid: number }) {
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const run = async () => {
    setMsg(null);
    try {
      const r = await api.importVisualReference(cid, text, "pasted visual reference");
      setMsg(`Updated ${r.updated.length}: ${r.updated.join(", ")}.${r.unmatched.length ? ` No match for: ${r.unmatched.join(", ")}.` : ""} Undo with save point #${r.save_point_id}.`);
      setText("");
    } catch (e) { setMsg(String(e)); }
  };
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div className="sub">Physical descriptions for character sheets, portraits and the GM. One <span className="mono">## Name</span> section per character,
        then <span className="mono">**demographic | age | class**</span>, a physique paragraph, an outfit paragraph, <span className="mono">**Signature:**</span> and <span className="mono">**Expression:**</span>.</div>
      <textarea className="input" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder="## Terri Campion&#10;**Black American woman | 37 | Bodycam Paladin**&#10;..." />
      <div className="row"><button className="btn" disabled={text.trim().length < 10} onClick={run}>Import</button>{msg && <span className="sub">{msg}</span>}</div>
    </div>
  );
}
