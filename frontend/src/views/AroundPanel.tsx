import { useEffect, useState } from "react";
import { MapPin, MessageCircle, Users } from "lucide-react";
import { api, ApiError, AroundResident, LodgeResident, newKey, TurnStatus } from "../api";
import { Chip } from "../ui";
import "../storybar.css";

/** "Around you": the met residents where the group stands (resident_api.py). Talk walks over and starts the GM's turn;
 * residents the group hasn't met are never listed, the GM narrates them. */
export function AroundPanel({ cid, version, onTurn }: { cid: number; version: unknown; onTurn?: (t: TurnStatus) => void }) {
  const [people, setPeople] = useState<AroundResident[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api.around(cid).then(setPeople).catch(() => setPeople([]));
  useEffect(() => { load(); }, [cid, version]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!people.length) return null;

  const talk = async (p: AroundResident) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.talkResident(cid, p.id, newKey());
      onTurn?.(r.turn);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
      load();
    } finally { setBusy(false); }
  };

  return (
    <section className="venuepanel aroundpanel" aria-label="Around you">
      <div className="storybar-head"><span className="storybar-title"><Users size={13} aria-hidden /> Around you</span></div>
      <ul className="aroundpanel-list">
        {people.map((p) => (
          <li key={p.id}>
            <span className="aroundpanel-name">{p.name}</span>
            {p.role && <span className="aroundpanel-role">{p.role}</span>}
            <button className="btn small" disabled={busy} onClick={() => talk(p)} aria-label={`Talk to ${p.name}`}>
              <MessageCircle size={12} aria-hidden /> Talk
            </button>
          </li>
        ))}
      </ul>
      {msg && <div className="venuepanel-msg">{msg}</div>}
    </section>
  );
}

/** The Lodge roster: every resident the group has met, where they were last seen, whether they're with the group, and
 * what their introduction told the group. */
export function LodgeRoster({ cid, version }: { cid: number; version: unknown }) {
  const [list, setList] = useState<LodgeResident[] | null>(null);
  useEffect(() => { api.residents(cid).then(setList).catch(() => setList([])); }, [cid, version]);
  if (!list?.length) return null;
  return (
    <section className="lodge-roster" aria-label="Dyer's Lodge roster">
      <div className="h2">Dyer’s Lodge <span className="muted">{list.length} met</span></div>
      <div className="people-grid">
        {list.map((r) => (
          <div key={r.id} className="person lodge-resident">
            <div className="lodge-resident-head">
              <span className="person-name">{r.name} {r.in_party && <Chip tone="cyan">With you</Chip>}</span>
              {r.role && <span className="person-summary">{r.role}</span>}
              <span className="person-where"><MapPin size={12} aria-hidden /> {r.last_seen ? `Last seen: ${r.last_seen}` : "Not seen since you met"}</span>
            </div>
            {r.known.length > 0 && <ul className="lodge-resident-known">{r.known.map((k) => <li key={k}>{k}</li>)}</ul>}
          </div>
        ))}
      </div>
    </section>
  );
}
