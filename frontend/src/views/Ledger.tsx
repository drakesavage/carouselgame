import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, ArrowDownToLine, ArrowUpFromLine, CheckCircle2, GitCommitHorizontal, RefreshCw } from "lucide-react";
import { api, ApiError, LedgerPreview } from "../api";
import { Chip, Empty, Panel } from "../ui";

function DiffView({ diff }: { diff: string }) {
  return (
    <div className="diff">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : ""}>{l || " "}</div>
      ))}
    </div>
  );
}

const short = (sha?: string | null) => (sha ? sha.slice(0, 10) : "—");
const show = (v: unknown) => (v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

export function LedgerView({ cid, version, onChanged }: { cid: number; version: number; onChanged: () => void }) {
  const [pv, setPv] = useState<LedgerPreview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const [author, setAuthor] = useState(() => { try { return JSON.parse(localStorage.getItem("ledgerAuthor") ?? "null"); } catch { return null; } });
  const [checkpoint, setCheckpoint] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [confirmPush, setConfirmPush] = useState(false);
  const [history, setHistory] = useState<Awaited<ReturnType<typeof api.ledgerHistory>>>([]);

  const load = async (fetch = true) => {
    setLoading(true); setErr(null);
    try {
      const p = await api.ledgerPreview(cid, fetch);
      setPv(p);
      setSelected(new Set(p.changes.filter((c) => c.status === "clean").map((c) => c.id)));
      setMessage(p.suggested_message);
      setAuthor((a: any) => a ?? p.status.author_default);
      setHistory(await api.ledgerHistory(cid));
    } catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); setPv(null); }
    finally { setLoading(false); }
  };
  useEffect(() => { setDone(null); }, [cid]);
  useEffect(() => { if (!busy) load(true); }, [cid, version]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const g: Record<string, LedgerPreview["changes"]> = {};
    pv?.changes.forEach((c) => (g[c.group] ??= []).push(c));
    return g;
  }, [pv]);

  const write = async () => {
    if (!pv || !author) return;
    setBusy("write"); setErr(null);
    try {
      try { localStorage.setItem("ledgerAuthor", JSON.stringify(author)); } catch { /* ignore */ }
      const r = await api.ledgerApply(cid, { fingerprint: pv.fingerprint, selected: [...selected], message, author_name: author.name, author_email: author.email, regenerate_checkpoint: checkpoint && !pv.checkpoint_curated });
      setDone(`Committed ${short(r.commit)} — ${r.summary.changes} changes in ${r.summary.files.length} files. Validator passed.${r.summary.skipped ? ` ${r.summary.skipped} change(s) left for later.` : ""}`);
      await load(false);
      onChanged();
    } catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); }
    finally { setBusy(null); }
  };
  const gitAction = async (kind: "pull" | "push") => {
    setBusy(kind); setErr(null); setConfirmPush(false);
    try { await (kind === "pull" ? api.ledgerPull(cid) : api.ledgerPush(cid)); setDone(kind === "pull" ? "Pulled the latest ledger commits (fast-forward)." : "Pushed to the remote."); await load(false); }
    catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); }
    finally { setBusy(null); }
  };

  if (!pv && err) return (
    <div className="view-inner">
      <div className="h1">Ledger write-back</div>
      <div className="error-box" style={{ marginTop: 12 }}>{err}</div>
    </div>
  );
  if (!pv) return <div className="muted">{loading ? "Comparing the campaign with the ledger…" : ""}</div>;
  const st = pv.status;
  const clean = pv.changes.filter((c) => c.status === "clean");
  const conflicts = pv.changes.filter((c) => c.status === "conflict");
  const canWrite = !pv.blockers.length && selected.size > 0 && author?.name && author?.email && message.trim().length >= 3;

  return (
    <div className="view-inner">
      <div className="row">
        <div className="grow">
          <div className="h1">Ledger write-back</div>
          <div className="sub">Review what this console changed, then write it to the Git campaign ledger as one validated commit. Nothing is pushed unless you press Push.</div>
        </div>
        <button className="btn" onClick={() => load(true)} disabled={loading}><RefreshCw size={14} /> {loading ? "Checking…" : "Refresh"}</button>
      </div>

      <div className="chips" style={{ margin: "12px 0" }}>
        <Chip tone="cyan">{st.branch} @ {short(st.head)}</Chip>
        <Chip title="Ledger commit the console's changes are compared against">baseline {short(pv.base_commit)}</Chip>
        {st.upstream && <Chip tone={st.behind ? "red" : "green"}>{st.behind ? `${st.behind} behind` : "up to date"} with {st.upstream}</Chip>}
        {!!st.ahead && <Chip tone="amber">{st.ahead} unpushed</Chip>}
        {st.dirty_files.length > 0 && <Chip tone="red">{st.dirty_files.length} uncommitted file(s)</Chip>}
        <Chip>{pv.ledger_path}</Chip>
      </div>
      <div className="row wrap" style={{ marginBottom: 12 }}>
        {!!st.behind && <button className="btn" disabled={!!busy} onClick={() => gitAction("pull")}><ArrowDownToLine size={14} /> Pull (fast-forward)</button>}
        {!!st.ahead && <button className="btn" disabled={!!busy || !!st.behind} onClick={() => setConfirmPush(true)}><ArrowUpFromLine size={14} /> Push {st.ahead} commit{st.ahead > 1 ? "s" : ""}</button>}
        {st.fetch_error && <span className="sub">Couldn't reach the remote: {st.fetch_error}</span>}
      </div>

      {done && <div className="card" style={{ borderColor: "var(--cyan-dim)", marginBottom: 12 }}><CheckCircle2 size={14} color="var(--hp)" /> {done}</div>}
      {err && <div className="error-box" style={{ marginBottom: 12, whiteSpace: "pre-wrap" }}>{err}</div>}
      {pv.blockers.map((b, i) => <div key={i} className="error-box" style={{ marginBottom: 8 }}><AlertTriangle size={14} color="var(--red)" /> {b}</div>)}

      {pv.changes.length === 0 ? (
        <Empty>The ledger already matches this campaign. Play some turns, then come back here to record them.</Empty>
      ) : (
        <div style={{ display: "grid", gap: 14, gridTemplateColumns: "minmax(0, 1.2fr) minmax(300px, 0.8fr)" }}>
          <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
            <Panel title="Changes" accent={`${selected.size}/${clean.length} selected`} right={conflicts.length ? <Chip tone="amber">{conflicts.length} conflict{conflicts.length > 1 ? "s" : ""}</Chip> : null}>
              {Object.entries(groups).map(([g, list]) => (
                <div key={g} style={{ marginBottom: 10 }}>
                  <div className="h2" style={{ margin: "4px 0" }}>{g}</div>
                  {list.map((c) => (
                    <label key={c.id} className={`change-row ${c.status}`}>
                      <input type="checkbox" disabled={c.status !== "clean"} checked={selected.has(c.id)}
                        onChange={(e) => setSelected((s) => { const n = new Set(s); e.target.checked ? n.add(c.id) : n.delete(c.id); return n; })} />
                      <div>
                        <div>{c.summary}</div>
                        {c.status === "conflict" && (
                          <div className="sub" style={{ color: "var(--amber)" }}>
                            {c.detail.conflict}{c.detail.ledger_now !== undefined ? ` — ledger now has ${show(c.detail.ledger_now)}, console wants ${show(c.detail.to)}` : ""}. Not written; reconcile by hand.
                          </div>
                        )}
                      </div>
                      <Chip tone={c.visibility === "gm" ? "magenta" : ""}>{c.file.replace("state/characters/", "").replace("state/", "")}</Chip>
                    </label>
                  ))}
                </div>
              ))}
            </Panel>
            {pv.warnings.length > 0 && (
              <Panel title="Not written automatically">
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{pv.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </Panel>
            )}
            <Panel title="File diffs" accent={pv.files.length}>
              <div className="sub" style={{ marginBottom: 8 }}>Shows every clean change; unchecked changes are left out when writing.</div>
              {pv.files.map((f) => (
                <details key={f.path} style={{ marginBottom: 8 }} open={pv.files.length <= 3}>
                  <summary style={{ cursor: "pointer" }} className="row"><b className="mono" style={{ fontSize: 13 }}>{f.path}</b>{f.new_file && <Chip tone="green">new file</Chip>}{f.visibility === "gm" && <Chip tone="magenta">GM-only</Chip>}</summary>
                  <div style={{ marginTop: 6 }}><DiffView diff={f.diff} /></div>
                </details>
              ))}
            </Panel>
          </div>
          <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
            <Panel title={<><GitCommitHorizontal size={13} /> Commit</>}>
              <label>Message<textarea className="input" rows={6} value={message} onChange={(e) => setMessage(e.target.value)} style={{ resize: "vertical" }} /></label>
              <div className="row" style={{ marginTop: 8 }}>
                <label className="grow">Author<input className="input" value={author?.name ?? ""} onChange={(e) => setAuthor({ ...author, name: e.target.value })} /></label>
                <label className="grow">Email<input className="input" value={author?.email ?? ""} onChange={(e) => setAuthor({ ...author, email: e.target.value })} /></label>
              </div>
              <label className="row" style={{ marginTop: 10, fontSize: 13 }}>
                <input type="checkbox" checked={checkpoint && !pv.checkpoint_curated} disabled={!!pv.checkpoint_curated} onChange={(e) => setCheckpoint(e.target.checked)} /> Regenerate checkpoints/latest.md (save-point protocol)
              </label>
              {pv.checkpoint_curated && (
                <div className="sub" style={{ color: "var(--amber)", marginTop: 4 }}>
                  <AlertTriangle size={12} /> The current checkpoint contains hand-written sections that tools/build_checkpoint.py does not produce. Regeneration is blocked so they can't be replaced; update the checkpoint by hand.
                </div>
              )}
              <div className="sub" style={{ margin: "8px 0" }}>Runs the ledger's own tools/validate_state.py first. If validation fails, every file is restored and nothing is committed.</div>
              <button className="btn primary" style={{ width: "100%", justifyContent: "center" }} disabled={!canWrite || !!busy} onClick={write}>
                {busy === "write" ? "Writing…" : `Write ${selected.size} change${selected.size === 1 ? "" : "s"} & commit`}
              </button>
            </Panel>
            <Panel title="Sync history">
              {history.length === 0 ? <div className="muted">No write-backs yet.</div> : history.map((h) => (
                <div key={h.id} className="stat-line"><span className="mono">{short(h.new_commit)}</span><span className="k">{h.summary.changes} changes · {new Date(h.created_at).toLocaleString()}</span></div>
              ))}
            </Panel>
          </div>
        </div>
      )}

      <Dialog.Root open={confirmPush} onOpenChange={setConfirmPush}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="modal" aria-describedby={undefined}>
            <Dialog.Title className="h1" style={{ fontSize: 17 }}>Push to {st.upstream}?</Dialog.Title>
            <p className="sub">This publishes {st.ahead} local commit(s) to the remote ledger, where your other sessions (e.g. ChatGPT) will read them.</p>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <Dialog.Close className="btn ghost">Cancel</Dialog.Close>
              <button className="btn primary" onClick={() => gitAction("push")}>Push</button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
