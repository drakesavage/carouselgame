import { useEffect, useState } from "react";
import { Check, HardDrive, Loader2, ShieldAlert } from "lucide-react";
import { api } from "../api";
import { Chip, Empty, Panel } from "../ui";

const age = (s: number | null) => (s == null ? "never" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`);

/** Backups of this install (game PC only): when the last one was made, make one now, verify any. */
export function BackupsPanel() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.backups>> | null>(null);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [checked, setChecked] = useState<Record<string, string>>({});
  const load = () => api.backups().then(setData).catch(() => setDenied(true));
  useEffect(() => { load(); }, []);
  const make = async (media: boolean) => {
    setBusy(media ? "full" : "db");
    try { await api.backupCreate(media ? "manual full" : "manual", media); await load(); } finally { setBusy(null); }
  };
  const verify = async (name: string) => {
    setBusy(name);
    try { const v = await api.backupVerify(name); setChecked((c) => ({ ...c, [name]: v.ok ? "ok" : v.problems.slice(0, 2).join("; ") })); }
    finally { setBusy(null); }
  };
  const tone = data?.newest_age_s == null ? "red" : data.newest_age_s < 86400 ? "green" : data.newest_age_s < 3 * 86400 ? "amber" : "red";
  return (
    <Panel title={<><HardDrive size={13} /> Backups</>} accent={data ? <Chip tone={tone}>last: {age(data.newest_age_s)}</Chip> : undefined}>
      {denied ? <div className="sub"><ShieldAlert size={12} /> Backups are managed on the game PC only.</div> : !data ? <div className="muted">Loading…</div> : (
        <>
          <div className="row wrap" style={{ gap: 6 }}>
            <button className="btn small" disabled={!!busy} onClick={() => make(false)}>{busy === "db" ? <Loader2 size={12} className="spin" /> : null} Back up the game now</button>
            <button className="btn small ghost" disabled={!!busy} onClick={() => make(true)} title="Includes art and portraits (about 200 MB)">{busy === "full" ? <Loader2 size={12} className="spin" /> : null} Full backup with art</button>
          </div>
          <div className="sub" style={{ margin: "6px 0" }}>The server also makes a game-only backup each day it starts (the last 7 are kept). Restoring a backup is done from the command line (docs/OPERATIONS.md) so live data can't be overwritten by accident.</div>
          {data.backups.length === 0 ? <Empty>No backups yet.</Empty> : (
            <table className="data"><tbody>{data.backups.slice(0, 12).map((b) => (
              <tr key={b.name}>
                <td><div>{b.label}</div><div className="sub">{b.created ? new Date(b.created).toLocaleString() : b.name}</div></td>
                <td className="mono sub">{b.size_mb} MB{b.media ? " · art" : ""}</td>
                <td style={{ textAlign: "right" }}>
                  {checked[b.name] ? (checked[b.name] === "ok" ? <Chip tone="green"><Check size={11} /> verified</Chip> : <Chip tone="red" title={checked[b.name]}>problem</Chip>)
                    : b.has_manifest ? <button className="btn small ghost" disabled={!!busy} onClick={() => verify(b.name)}>{busy === b.name ? <Loader2 size={12} className="spin" /> : null} Verify</button>
                    : <span className="sub" title="Made by hand without a manifest">database copy</span>}
                </td>
              </tr>))}
            </tbody></table>
          )}
        </>
      )}
    </Panel>
  );
}

/** What restoring a save point would change, in player terms. */
export function RestorePreview({ cid, saveId }: { cid: number; saveId: number }) {
  const [p, setP] = useState<Awaited<ReturnType<typeof api.savePreview>> | null>(null);
  useEffect(() => { setP(null); api.savePreview(cid, saveId).then(setP).catch(() => {}); }, [cid, saveId]);
  if (!p) return <div className="muted">Working out what would change…</div>;
  const nothing = !p.changes.length && !p.items.length && !p.quests.length && !p.characters.length;
  return (
    <div className="restore-preview">
      {nothing ? <div className="sub">The game state matches this save point; restoring would change nothing but hide {p.messages_hidden} later message(s).</div> : (
        <>
          {p.changes.length > 0 && <table className="data"><thead><tr><th></th><th>Now</th><th>After restoring</th></tr></thead>
            <tbody>{p.changes.map((c) => <tr key={c.what}><td>{c.what}</td><td>{c.now}</td><td><b>{c.after_restore}</b></td></tr>)}</tbody></table>}
          {p.items.length > 0 && <details><summary>Items ({p.items_total})</summary><ul>{p.items.map((i) => <li key={i}>{i}</li>)}</ul></details>}
          {p.quests.length > 0 && <details><summary>Journal ({p.quests.length})</summary><ul>{p.quests.map((q) => <li key={q}>{q}</li>)}</ul></details>}
          {p.characters.length > 0 && <details><summary>Characters ({p.characters.length})</summary><ul>{p.characters.map((c) => <li key={c}>{c}</li>)}</ul></details>}
          <div className="sub">{p.messages_hidden} later message(s) would be hidden from the story.</div>
        </>
      )}
    </div>
  );
}
