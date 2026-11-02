import { useEffect, useState } from "react";
import { Footprints, Loader2, Tent, X } from "lucide-react";
import { api, fmtClock, PlaceDossier as Dossier } from "../api";
import { humanize } from "../ui";

const HOW: Record<string, string> = { start: "Known from the start", visited: "Visited", told: "Heard of" };

/** The selected place on the town map: what the group knows about it, and Travel here (the real travel op; a refusal
 * shows the server's reason and never moves the marker). Everything comes from GET /places/{id}, known places only. */
export function PlaceDossier({ cid, placeId, version, here, busy, message, onTravel, onClose, unplaced = false }: {
  cid: number; placeId: string; version: unknown; here: boolean; busy: boolean; message: string | null;
  onTravel: () => void; onClose: () => void; unplaced?: boolean;
}) {
  const [d, setD] = useState<Dossier | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [broken, setBroken] = useState<string | null>(null);   // art that failed to load falls back to the projection card
  useEffect(() => {
    let live = true;
    setErr(null);
    api.place(cid, placeId).then((x) => { if (live) setD(x); }).catch((e) => { if (live) { setD(null); setErr((e as Error).message); } });
    return () => { live = false; };
  }, [cid, placeId, version]);
  if (err) return <aside className="dossier-panel" aria-label="Place"><p className="muted">{err}</p></aside>;
  if (!d || d.id !== placeId) return <aside className="dossier-panel" aria-label="Place" aria-busy><p className="muted">Opening the file…</p></aside>;
  const visits = d.visits.count === 1 ? "visited once" : d.visits.count ? `visited ${d.visits.count} times`
    : here ? "currently here" : d.known.how === "visited" ? "visited before" : "not visited";
  return (
    <aside className="dossier-panel" aria-label={`Place: ${d.name}`}>
      <button className="icon-btn dossier-close" onClick={onClose} aria-label="Close the place file"><X size={14} /></button>
      {d.image && broken !== d.image ? <img className="dossier-art" src={d.image} alt="" loading="lazy" onError={() => setBroken(d.image)} />
        : <div className="dossier-art projection" aria-hidden><span>{d.name}</span></div>}
      <div className="kicker">{[d.category, d.region].filter(Boolean).join(" · ")}</div>
      <h2 className="dossier-name">{d.name}</h2>
      {d.safe && <span className="safe-badge"><Tent size={11} aria-hidden /> Safe place</span>}
      <p className="dossier-known">{HOW[d.known.how ?? ""] ?? "Known"} since {fmtClock(d.known.minutes)} ({visits})</p>
      {d.description && <p className="dossier-desc">{d.description}</p>}
      {d.omens.length > 0 && (
        <section className="dossier-sec omen-sec">
          <h3 className="rail-head">Omens seen here</h3>
          <ul>{d.omens.map((o) => <li key={o.id}><span>{o.description}</span> <i>{humanize(o.stage)}</i></li>)}</ul>
        </section>
      )}
      {d.people.length > 0 && (
        <section className="dossier-sec">
          <h3 className="rail-head">People last seen here</h3>
          <ul>{d.people.map((p) => <li key={p}>{p}</li>)}</ul>
        </section>
      )}
      {d.stories.length > 0 && (
        <section className="dossier-sec">
          <h3 className="rail-head">Stories here</h3>
          <ul>{d.stories.map((s, i) => <li key={i}><span>{s.title}</span> — <i>{humanize(s.ending)}</i></li>)}</ul>
        </section>
      )}
      {unplaced ? <p className="muted small">You have heard of this place, but its exact location is not yet on your map.</p> : here ? <p className="muted small">You are here.</p> : (
        <button className="btn primary travel-btn" disabled={busy} onClick={onTravel}>
          {busy ? <Loader2 size={13} className="spin" aria-hidden /> : <Footprints size={13} aria-hidden />} Travel here
        </button>
      )}
      {message && <p className="card-msg" role="alert">{message}</p>}
    </aside>
  );
}
