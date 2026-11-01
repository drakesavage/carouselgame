import { useEffect, useState } from "react";
import { DoorOpen, Lock } from "lucide-react";
import { api, ApiError, newKey, PlayerVenue, TurnStatus } from "../api";
import "../storybar.css";

/** Inside a venue: the room the group is in, the ways on (blocked ones disabled with the reason) and the rooms seen.
 * Only what venue_api.py hands the player. A way click moves the group and starts the GM's turn, like a map click;
 * leaving the venue happens from the map. */
export function VenuePanel({ cid, version, onTurn }: { cid: number; version: unknown; onTurn?: (t: TurnStatus) => void }) {
  const [venue, setVenue] = useState<PlayerVenue | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api.venue(cid).then(setVenue).catch(() => setVenue(null));
  useEffect(() => { load(); }, [cid, version]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!venue) return null;

  const go = async (to: string) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.venueMove(cid, to, newKey());
      await load();
      onTurn?.(r.turn);
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 409 ? "The GM is still resolving a turn." : e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <section className="venuepanel" aria-label={`Venue: ${venue.title}`}>
      <div className="storybar-head">
        <span className="storybar-title">{venue.title}</span>
        <span className="venuepanel-here">You are at {venue.zone.label}</span>
      </div>
      <div className="venuepanel-ways" role="group" aria-label="Ways on">
        {venue.ways.map((w) => (
          <button key={w.id} className="btn small" disabled={busy || !!w.blocked} title={w.blocked ?? undefined} onClick={() => go(w.id)}>
            {w.blocked ? <Lock size={12} /> : <DoorOpen size={12} />} {w.label}{w.blocked ? ` (blocked: ${w.blocked})` : ""}
          </button>
        ))}
      </div>
      {msg && <div className="venuepanel-msg">{msg}</div>}
      <div className="venuepanel-seen">Seen: {venue.seen.map((z) => z.label).join(" · ")}</div>
      <div className="venuepanel-leave">Leave by {venue.entry === venue.exit ? venue.entry : `${venue.entry} / ${venue.exit}`} (from the map)</div>
    </section>
  );
}
