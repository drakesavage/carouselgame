import { useEffect, useState } from "react";
import { AlertTriangle, KeyRound, RefreshCw } from "lucide-react";
import { api, ApiError, DiagnosticsRow } from "../api";
import { Chip, Empty } from "../ui";

const TOKEN_KEY = "gm-diagnostics-token";

/** Development-only GM diagnostics. Needs the GM token (data/gm_token.txt); everything here may contain secrets. */
export function Diagnostics({ cid }: { cid: number }) {
  const [token, setToken] = useState(() => { try { return sessionStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; } });
  const [draft, setDraft] = useState("");
  const [list, setList] = useState<DiagnosticsRow[] | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  const [detail, setDetail] = useState<Record<string, any> | null>(null);
  const [tab, setTab] = useState<"integrity" | "context" | "steps" | "rolls" | "usage">("integrity");
  const [err, setErr] = useState<string | null>(null);

  const load = async (t = token) => {
    setErr(null);
    try { const l = await api.gmDiagnosticsList(cid, t); setList(l); if (l.length && sel == null) setSel(l[0].turn_id); }
    catch (e) { setList(null); setErr(e instanceof ApiError && e.status === 403 ? "That GM token was rejected." : String(e)); }
  };
  useEffect(() => { if (token) load(); }, [cid, token]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (sel != null && token) api.gmDiagnostics(sel, token).then(setDetail).catch((e) => setErr(String(e))); }, [sel, token]);

  if (!token) {
    return (
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}><KeyRound size={14} /> <b>GM token required</b></div>
        <div className="sub" style={{ marginBottom: 8 }}>Diagnostics show the GM's full context, including private companion sheets and secrets. Paste the token from <span className="mono">data\gm_token.txt</span> (kept only for this browser session).</div>
        <div className="row"><input className="input" type="password" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="GM token" />
          <button className="btn" onClick={() => { try { sessionStorage.setItem(TOKEN_KEY, draft); } catch { /* ignore */ } setToken(draft); }}>Unlock</button></div>
      </div>
    );
  }
  const usage = detail?.usage ?? {};
  const rounds: any[] = usage.rounds ?? [];
  const totals = rounds.reduce((a, r) => ({ input: a.input + (r.usage?.input_tokens ?? 0), cached: a.cached + (r.usage?.cached_input_tokens ?? 0), output: a.output + (r.usage?.output_tokens ?? 0), secs: a.secs + (r.duration_s ?? 0) }), { input: 0, cached: 0, output: 0, secs: 0 });
  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="sub grow">GM-only. Never share screenshots of this panel with players.</span>
        <button className="btn small" onClick={() => load()}><RefreshCw size={12} /> Refresh</button>
        <button className="btn small ghost" onClick={() => { try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ } setToken(""); setList(null); setDetail(null); }}>Lock</button>
      </div>
      {err && <div className="error-box" style={{ marginBottom: 10 }}>{err}</div>}
      {!list ? null : list.length === 0 ? <Empty>No diagnosed turns yet.</Empty> : (
        <div className="diag-grid">
          <div className="diag-list">
            {list.map((r) => (
              <button key={r.turn_id} className={sel === r.turn_id ? "active" : ""} onClick={() => setSel(r.turn_id)}>
                <span className="row"><b>Turn {r.turn_id}</b><Chip tone={r.status === "completed" ? "green" : "red"}>{r.status}</Chip>{r.flags > 0 && <Chip tone="amber">{r.flags} flag{r.flags > 1 ? "s" : ""}</Chip>}</span>
                <span className="sub">{r.input}</span>
                <span className="sub mono">{r.rounds} round(s) · {r.input_tokens.toLocaleString()} input tokens</span>
              </button>
            ))}
          </div>
          <div>
            {!detail ? <div className="muted">Loading…</div> : (
              <>
                <div className="chips" style={{ marginBottom: 8 }}>
                  <Chip>state t={usage.game_time_before} → {usage.game_time_after} ({(usage.game_time_after ?? 0) - (usage.game_time_before ?? 0)} min)</Chip>
                  <Chip>context {Math.round((detail.context_chars ?? 0) / 4).toLocaleString()} tokens</Chip>
                  <Chip>present: {(usage.present ?? []).join(", ") || "—"}</Chip>
                  {usage.rolled_back && <Chip tone="red">rolled back</Chip>}
                </div>
                <div className="tabs">
                  {(["integrity", "context", "steps", "rolls", "usage"] as const).map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}
                </div>
                {tab === "integrity" && (
                  <div style={{ display: "grid", gap: 6 }}>
                    {(detail.integrity ?? []).length === 0 ? <div className="flag info">No inconsistencies detected between the narration and the validated changes.</div>
                      : detail.integrity.map((f: any, i: number) => <div key={i} className={`flag ${f.level}`}><AlertTriangle size={12} /> {f.message}</div>)}
                    <div className="h2">Validated changes</div>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{(usage.changes ?? []).map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
                    {(usage.errors ?? []).length > 0 && <><div className="h2">Rejected requests</div><ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{usage.errors.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul></>}
                  </div>
                )}
                {tab === "context" && (
                  <>
                    <div className="chips" style={{ marginBottom: 6 }}>{Object.entries(usage.context_sizes ?? {}).map(([k, v]) => <Chip key={k}>{k} ~{Math.round(Number(v) / 4)}t</Chip>)}</div>
                    <div className="pre" style={{ maxHeight: 560 }}>{detail.context_text}</div>
                  </>
                )}
                {tab === "steps" && (
                  <div style={{ display: "grid", gap: 8 }}>
                    {(detail.steps ?? []).map((s: any, i: number) => (
                      <div key={i} className="card">
                        <div className="row"><Chip tone={s.kind === "gm_request" ? "cyan" : s.kind === "tool_results" ? "violet" : ""}>{s.kind}</Chip></div>
                        {s.kind === "player" && <div style={{ marginTop: 6 }}>{s.text}</div>}
                        {s.kind === "instruction" && <div className="sub" style={{ marginTop: 6 }}>{s.text}</div>}
                        {s.kind === "gm_request" && <div className="pre" style={{ marginTop: 6 }}>{s.tool_calls.map((c: any) => `${c.name}(${JSON.stringify(c.arguments, null, 1)})`).join("\n\n")}{s.draft ? `\n\n(draft) ${s.draft}` : ""}</div>}
                        {s.kind === "tool_results" && <div className="pre" style={{ marginTop: 6 }}>{s.results.map((r: any) => `${r.ok ? "✓" : "✗"} ${r.name}: ${typeof r.result === "string" ? r.result : JSON.stringify(r.result, null, 1)}`).join("\n\n")}</div>}
                      </div>
                    ))}
                  </div>
                )}
                {tab === "rolls" && (
                  <>
                    <table className="data"><thead><tr><th>#</th><th>Roll</th><th>Dice</th><th>Total</th><th>Result</th><th>RNG</th></tr></thead>
                      <tbody>{(detail.rolls ?? []).map((r: any) => (
                        <tr key={r.id}><td className="mono">{r.id}</td><td>{r.reason}{r.hidden ? <Chip tone="magenta">hidden</Chip> : null}</td>
                          <td className="mono">{(r.dice ?? []).map((d: any) => `${d.value}${d.kept ? "" : "×"}`).join(" ")}</td><td className="mono">{r.total}{r.target != null ? ` vs ${r.target}` : ""}</td>
                          <td>{r.degree ?? r.outcome ?? ""}</td><td className="sub">{r.rng}</td></tr>))}</tbody>
                    </table>
                    <div className="h2">Events</div>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{(detail.events ?? []).map((e: any) => <li key={e.id}>{e.summary} <span className="muted">({e.type}{e.visibility === "gm" ? ", GM-only" : ""}{e.rolled_back ? ", rolled back" : ""})</span></li>)}</ul>
                  </>
                )}
                {tab === "usage" && (
                  <>
                    <div className="chips" style={{ marginBottom: 8 }}>
                      <Chip tone="cyan">{totals.input.toLocaleString()} input tokens</Chip><Chip>{totals.cached.toLocaleString()} cached</Chip>
                      <Chip>{totals.output.toLocaleString()} output</Chip><Chip>{totals.secs.toFixed(1)} s in Codex</Chip>
                    </div>
                    <table className="data"><thead><tr><th>Round</th><th>Model</th><th>Effort</th><th>Prompt chars</th><th>Input</th><th>Output</th><th>Seconds</th></tr></thead>
                      <tbody>{rounds.map((r) => <tr key={r.round}><td>{r.round}</td><td>{r.model ?? "—"}</td><td>{r.effort ?? "—"}</td><td className="mono">{r.prompt_chars?.toLocaleString() ?? "—"}</td>
                        <td className="mono">{r.usage?.input_tokens?.toLocaleString() ?? "—"}</td><td className="mono">{r.usage?.output_tokens ?? "—"}</td><td className="mono">{r.duration_s ?? "—"}</td></tr>)}</tbody>
                    </table>
                    <div className="h2">Available to the GM</div>
                    <div className="sub">Tools: {(detail.available_tools ?? []).join(", ")}</div>
                    <div className="sub">State ops: {(detail.available_ops ?? []).join(", ")}</div>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
