import { useState } from "react";
import { BookOpen, Eye, Scissors, ListCollapse } from "lucide-react";
import { ActiveStory, api, Campaign, DeathwatchPace } from "../api";
import { StoryBar } from "./StoryBar";
import { PosterBoard } from "./PosterBoard";

const PACES: { pace: DeathwatchPace; label: string }[] = [{ pace: "full", label: "Watch it all" }, { pace: "condensed", label: "Condensed" }];

/** The spectator's quick actions: they ask for a cut, a summary or more of the same, never for the dead character to act. */
export const SPECTATOR_QUICK = [
  { icon: Scissors, label: "Cut to the others", text: "Cut to the others — what are they doing now?" },
  { icon: ListCollapse, label: "Summarise", text: "Summarise what has happened since my character left the scene." },
  { icon: Eye, label: "Keep watching", text: "Keep watching." },
];

/** Deathwatch: the player's character can't act, the camera keeps rolling. Shows only what the fate API sends (the pace);
 * never a cause, never whether a rescue is possible, never a promise that the others will win. */
export function SpectatorFrame({ campaign, story, onChanged, openChronicle }: {
  campaign: Campaign; story: ActiveStory | null; onChanged: () => void; openChronicle?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pace = campaign.fate?.deathwatch?.pace;
  if (!pace) return null;
  const choose = async (p: DeathwatchPace) => {
    if (p === pace) return;
    setBusy(true); setErr(null);
    try { await api.deathwatchPace(campaign.id, p); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <section className="spectator" aria-label="Spectator view">
      <div className="spectator-corners" aria-hidden><i /><i /><i /><i /></div>
      <div className="spectator-kicker">Your character has left the scene</div>
      <h2 className="spectator-title">The movie isn't over.</h2>
      <div className="spectator-controls">
        <div className="seg" role="group" aria-label="How much to watch">
          {PACES.map((p) => (
            <button key={p.pace} className={pace === p.pace ? "active" : ""} aria-pressed={pace === p.pace} disabled={busy} onClick={() => choose(p.pace)}>{p.label}</button>
          ))}
        </div>
        {openChronicle && <button className="btn small ghost" onClick={openChronicle}><BookOpen size={13} aria-hidden /> Open the Chronicle</button>}
      </div>
      {err && <div className="spectator-error">{err}</div>}
      <StoryBar story={story} />
    </section>
  );
}

/** The whole group is on the missing-poster board: the end card, the posters, and a new group in the same town
 * (Plan 6's new-group flow), or a camp save from Saves & Settings. */
export function WipedScreen({ campaign, onChanged, openSaves }: { campaign: Campaign; onChanged: () => void; openSaves?: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const start = async () => {
    setBusy(true); setErr(null);
    try { await api.newGroup(campaign.id, name.trim()); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="wiped" aria-label="The End">
      <div className="end-card">
        <div className="end-title">THE END</div>
        <p className="end-line">Your group is on the missing-poster board.</p>
      </div>
      <PosterBoard cid={campaign.id} version={campaign} />
      <form className="end-form" onSubmit={(e) => { e.preventDefault(); if (name.trim()) start(); }}>
        <label className="h2" htmlFor="new-group-name">A new group in the same town</label>
        <div className="row wrap">
          <input id="new-group-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your new character's name" maxLength={80} />
          <button className="btn primary" disabled={busy || !name.trim()}>Start a new group</button>
          {openSaves && <button type="button" className="btn ghost" onClick={openSaves}>Restore a camp save</button>}
        </div>
        {err && <div className="spectator-error">{err}</div>}
      </form>
    </div>
  );
}
