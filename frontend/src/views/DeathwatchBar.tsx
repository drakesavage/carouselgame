import { useState } from "react";
import { Eye, Users } from "lucide-react";
import { api, Campaign, DeathwatchPace } from "../api";
import "../people.css";   // .seg
import "../storybar.css";

const PACES: { pace: DeathwatchPace; label: string }[] = [{ pace: "full", label: "Watch it all" }, { pace: "condensed", label: "Condensed" }];

/** Deathwatch while the player's character can't act (pick how much of the rest to watch), and the wiped screen once the
 * whole group is on the missing-poster board: start a new group in the same town, or restore the last camp save. */
export function DeathwatchBar({ campaign, onChanged, openSaves }: { campaign: Campaign; onChanged: () => void; openSaves?: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fate = campaign.fate;
  if (!fate || (!fate.wiped && !fate.deathwatch)) return null;
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  if (fate.wiped) {
    return (
      <section className="storybar deathwatch wiped" aria-label="Your group is missing">
        <div className="storybar-head"><span className="storybar-title"><Users size={13} /> Your group is on the missing-poster board.</span></div>
        <form className="row wrap" onSubmit={(e) => { e.preventDefault(); if (name.trim()) run(() => api.newGroup(campaign.id, name.trim())); }}>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your new character's name" aria-label="New character's name" maxLength={80} />
          <button className="btn primary" disabled={busy || !name.trim()}>Start a new group</button>
          {openSaves && <button type="button" className="btn ghost" onClick={openSaves}>Restore a camp save</button>}
        </form>
        {err && <div className="storybar-cast error">{err}</div>}
      </section>
    );
  }
  const pc = campaign.roster.find((p) => p.id === campaign.active_character_id) ?? campaign.party.find((p) => p.id === campaign.active_character_id);
  const pace = fate.deathwatch!.pace;
  return (
    <section className="storybar deathwatch" aria-label="Deathwatch">
      <div className="storybar-head">
        <span className="storybar-title"><Eye size={13} /> You're watching. {pc?.name ?? "Your character"} can't act.</span>
        <div className="seg" role="group" aria-label="How much to watch">
          {PACES.map((p) => (
            <button key={p.pace} className={pace === p.pace ? "active" : ""} aria-pressed={pace === p.pace} disabled={busy}
              onClick={() => pace !== p.pace && run(() => api.deathwatchPace(campaign.id, p.pace))}>{p.label}</button>
          ))}
        </div>
      </div>
      {err && <div className="storybar-cast error">{err}</div>}
    </section>
  );
}
