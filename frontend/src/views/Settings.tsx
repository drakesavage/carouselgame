import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, Bot, Database, Download, History, KeyRound, Save, Smartphone } from "lucide-react";
import { api, Prefs, SavePoint } from "../api";
import { Chip, Empty, Panel } from "../ui";
import { Diagnostics } from "./Diagnostics";

export function SettingsView({ cid, version, onChanged, onCampaign }: { cid: number; version: number; onChanged: () => void; onCampaign: (id: number) => void }) {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [saves, setSaves] = useState<SavePoint[] | null>(null);
  const [label, setLabel] = useState("");
  const [confirm, setConfirm] = useState<SavePoint | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [reports, setReports] = useState<Awaited<ReturnType<typeof api.importReports>> | null>(null);
  const [importing, setImporting] = useState(false);

  const [access, setAccess] = useState<Awaited<ReturnType<typeof api.access>> | null>(null);
  useEffect(() => { api.settings().then(setPrefs); api.importReports().then(setReports); api.access().then(setAccess).catch(() => setAccess(null)); }, []);
  useEffect(() => { api.saves(cid).then(setSaves); }, [cid, version]);

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
  const runImport = async () => {
    setImporting(true);
    try { const r = await api.importLedger(prefs?.ledger_dir); setReports(await api.importReports()); setMsg(r.campaign_id ? `Imported as a new campaign (#${r.campaign_id}).` : "Nothing imported — see the report."); if (r.campaign_id) onCampaign(r.campaign_id); }
    catch (e) { setMsg(String(e)); } finally { setImporting(false); }
  };
  const report = reports?.[0]?.report;

  return (
    <div className="view-inner">
      <div className="h1">Saves &amp; settings</div>
      {msg && <div className="card" style={{ margin: "10px 0", borderColor: "var(--cyan-dim)" }}>{msg}</div>}
      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", marginTop: 12 }}>
        <Panel title={<><Save size={13} /> Save points</>}>
          <div className="row"><input className="input" placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} /><button className="btn primary" onClick={save}>Save</button></div>
          <div className="sub" style={{ margin: "8px 0" }}>Every completed turn autosaves (last 25 kept). Restoring hides later transcript/events but keeps them in the audit log.</div>
          {!saves ? null : saves.length === 0 ? <Empty>No saves.</Empty> : (
            <table className="data"><tbody>{saves.map((s) => (
              <tr key={s.id}><td><div>{s.label}</div><div className="sub">{new Date(s.created_at).toLocaleString()}</div></td>
                <td><Chip tone={s.kind === "manual" ? "cyan" : s.kind === "import" ? "violet" : s.kind === "pre_restore" ? "amber" : ""}>{s.kind.replace("_", " ")}</Chip></td>
                <td style={{ textAlign: "right" }}><button className="btn small" onClick={() => setConfirm(s)}><History size={12} /> Restore</button></td></tr>
            ))}</tbody></table>
          )}
        </Panel>

        <Panel title={<><Bot size={13} /> AI Game Master</>}>
          {prefs && (
            <div style={{ display: "grid", gap: 10 }}>
              <label>Provider
                <select className="select input" value={prefs.gm_provider} onChange={(e) => update({ gm_provider: e.target.value })}>
                  <option value="codex">Codex CLI (your ChatGPT sign-in — no API billing)</option>
                  <option value="gemini">Gemini API (API key; free tier has rate limits)</option>
                  <option value="mock">Mock GM (offline testing, scripted)</option>
                </select>
              </label>
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
              <div className="sub">{prefs.gm_provider === "gemini" ? "Each turn is one or more Gemini API calls that can only return JSON (no model-side tools)." : <>Each turn is one or more sandboxed, read-only <span className="mono">codex exec</span> calls.</>} Dice and state changes always run in this app, never in the model.</div>
            </div>
          )}
        </Panel>

        {access && (
          <Panel title={<><Smartphone size={13} /> Other devices</>}>
            <div className="sub" style={{ marginBottom: 8 }}>Through Barcode Battles on port {access.port}. Devices other than this PC need the passcode once.</div>
            {(access.lan_addresses.length ? access.lan_addresses : ["<this PC's address>"]).map((ip) => (
              <div key={ip} className="mono" style={{ fontSize: 14 }}>http://{ip}:{access.port}{access.path}</div>
            ))}
            <div className="row" style={{ marginTop: 10 }}>
              <span className="sub">Passcode</span>
              <span className="mono" style={{ fontSize: 22, letterSpacing: "0.12em", color: "var(--cyan)" }}>{access.passcode}</span>
              <span className="spacer" />
              {!access.fixed_by_env && (
                <button className="btn small" onClick={async () => { const r = await api.regeneratePasscode(); setAccess({ ...access, passcode: r.passcode }); setMsg("New passcode set; every other device must sign in again."); }}>
                  <KeyRound size={12} /> New passcode
                </button>
              )}
            </div>
            {access.fixed_by_env && <div className="sub">Set by CRAWLER_PASSCODE in .env.</div>}
          </Panel>
        )}

        <Panel title={<><Database size={13} /> Campaign import</>}>
          {prefs && (
            <>
              <label>Ledger folder
                <input className="input" defaultValue={prefs.ledger_dir} onBlur={(e) => e.target.value !== prefs.ledger_dir && update({ ledger_dir: e.target.value })} />
              </label>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn" disabled={importing} onClick={runImport}><Download size={14} /> {importing ? "Importing…" : "Import as new campaign"}</button>
                <button className="btn ghost" onClick={async () => { const r = await api.makeDemo(); onCampaign(r.campaign_id); }}>Open demo campaign</button>
              </div>
              <div className="sub" style={{ marginTop: 6 }}>Read-only: the ledger is never modified. Each import creates a new campaign.</div>
            </>
          )}
          {report && (
            <div style={{ marginTop: 12 }}>
              <div className="h2" style={{ marginTop: 0 }}>Latest migration report</div>
              <div className="chips" style={{ marginBottom: 8 }}>
                {Object.entries(report.imported ?? {}).map(([k, v]) => <Chip key={k} tone="green">{k.replace(/_/g, " ")}: {String(v)}</Chip>)}
              </div>
              <details><summary className="row" style={{ cursor: "pointer" }}><AlertTriangle size={13} color="var(--amber)" /> {report.conflicts?.length ?? 0} conflicts</summary>
                <ul>{report.conflicts?.map((c: any, i: number) => <li key={i} style={{ fontSize: 13 }}><b>{c.topic}</b>: {JSON.stringify(c.values)} — <span className="sub">{c.resolution}</span></li>)}</ul></details>
              <details><summary style={{ cursor: "pointer" }}>{report.manual_review?.length ?? 0} items need manual review</summary>
                <ul>{report.manual_review?.map((m: any, i: number) => <li key={i} style={{ fontSize: 13 }}><span className="mono">{m.record}</span> {m.issue}</li>)}</ul></details>
              <details><summary style={{ cursor: "pointer" }}>{report.rules_pending_implementation?.length ?? 0} house rules awaiting mechanical implementation</summary>
                <ul>{report.rules_pending_implementation?.map((r: any, i: number) => <li key={i} style={{ fontSize: 13 }}>{r.rule} <Chip>{r.status ?? "pending"}</Chip></li>)}</ul></details>
              <div className="sub">Full report: reports/migration_report.md</div>
            </div>
          )}
        </Panel>
      </div>

      <details className="card" style={{ marginTop: 14 }}>
        <summary style={{ cursor: "pointer" }}><b>GM diagnostics</b> <span className="sub">(development: context packets, tool rounds, dice, integrity checks; GM token required)</span></summary>
        <div style={{ marginTop: 12 }}>{cid ? <Diagnostics cid={cid} /> : <div className="muted">Open a campaign first.</div>}</div>
      </details>

      <Dialog.Root open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="modal" aria-describedby={undefined}>
            <Dialog.Title className="h1" style={{ fontSize: 17 }}>Restore “{confirm?.label}”?</Dialog.Title>
            <p className="sub">Current state is backed up automatically first, so this can be undone. Later turns are hidden from the transcript but remain in the audit log.</p>
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
