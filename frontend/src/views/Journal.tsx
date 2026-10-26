import { useEffect, useMemo, useState } from "react";
import {
  Award, Backpack, BookOpen, Bookmark, CalendarDays, ChevronDown, ChevronRight, Clock3, Flag, Footprints, Hammer, Handshake, Heart, MapPin,
  Scroll, Sparkles, Swords, Users, Wrench, Zap,
} from "lucide-react";
import { api, Chronology, fmtMinutes, JournalQuest, LoreFact } from "../api";
import { Chip, Empty, humanize } from "../ui";
import "../journal.css";

const KIND_ICON: Record<string, typeof Flag> = { quest: Flag, commitment: Handshake, reservation: Bookmark, project: Hammer, crafting: Wrench };

/**
 * The quest journal: one canonical card per quest, promise, reservation or project, with who holds it, who it involves,
 * where, when it's due and what it waits on. Active first; finished ones fold away.
 */
export function QuestsView({ cid, version, focus, openCharacter }: { cid: number; version: number; focus?: number | null; openCharacter: (id: number) => void }) {
  const [data, setData] = useState<{ kinds: { id: string; label: string }[]; quests: JournalQuest[] } | null>(null);
  const [showDone, setShowDone] = useState(false);
  useEffect(() => { setData(null); api.journal(cid).then(setData).catch(() => setData({ kinds: [], quests: [] })); }, [cid, version]);
  useEffect(() => {
    if (data && focus != null) document.getElementById(`quest-${focus}`)?.scrollIntoView({ block: "center" });
  }, [data, focus]);
  if (!data) return <div className="muted">Loading…</div>;
  const open = data.quests.filter((q) => q.status === "active" || q.status === "paused");
  const done = data.quests.filter((q) => q.status === "completed" || q.status === "failed");
  return (
    <div className="view-inner journal">
      <div className="h1">Quest journal</div>
      <div className="sub">Quests, promises, reserved items and projects as the records hold them. The GM sees every open one each turn.</div>
      {data.quests.length === 0 && <Empty>No quests recorded.</Empty>}
      {data.kinds.map((k) => {
        const list = open.filter((q) => q.kind === k.id);
        if (!list.length) return null;
        const Icon = KIND_ICON[k.id] ?? Flag;
        return (
          <section key={k.id}>
            <div className="h2"><Icon size={14} /> {k.label} <span className="muted">{list.length}</span></div>
            <div className="jq-grid">{list.map((q) => <QuestCard key={q.id} q={q} focused={q.id === focus} openCharacter={openCharacter} />)}</div>
          </section>
        );
      })}
      {done.length > 0 && (
        <section>
          <button className="h2 as-button" aria-expanded={showDone} onClick={() => setShowDone((s) => !s)}>
            {showDone ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Completed &amp; failed <span className="muted">{done.length}</span>
          </button>
          {showDone && <div className="jq-grid">{done.map((q) => <QuestCard key={q.id} q={q} focused={q.id === focus} openCharacter={openCharacter} />)}</div>}
        </section>
      )}
    </div>
  );
}

function QuestCard({ q, focused, openCharacter }: { q: JournalQuest; focused: boolean; openCharacter: (id: number) => void }) {
  const [open, setOpen] = useState(focused);
  const Icon = KIND_ICON[q.kind] ?? Flag;
  const details = Object.entries(q.details).filter(([k, v]) => v != null && v !== "" && !["source", "depends_on", "requires"].includes(k));
  return (
    <article id={`quest-${q.id}`} className={`jq ${q.kind} ${q.status} ${focused ? "focused" : ""}`}>
      <button className="jq-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon size={15} className="jq-icon" />
        <span className="jq-title">{q.title}</span>
        <span className="jq-chips">
          {q.immediate && <Chip tone="cyan" title="Part of the immediate objective">now</Chip>}
          {q.status !== "active" && <Chip tone={q.status === "completed" ? "green" : q.status === "failed" ? "red" : "amber"}>{q.status}</Chip>}
          {q.due_in_minutes != null && <Chip tone={q.due_in_minutes < 0 ? "red" : q.due_in_minutes < 180 ? "amber" : ""}>{q.due_in_minutes < 0 ? "overdue" : `due in ${fmtMinutes(q.due_in_minutes)}`}</Chip>}
        </span>
      </button>
      <div className="jq-meta">
        {q.holder && <span><Users size={11} /> held by {q.holder}</span>}
        {q.giver && <span>from {q.giver}</span>}
        {q.places.map((p) => <span key={p}><MapPin size={11} /> {p}</span>)}
      </div>
      {q.people.length > 0 && (
        <div className="jq-people">{q.people.map((p) => <button key={p.id} className="chip" onClick={() => openCharacter(p.id)} title={`Open ${p.name}'s dossier`}>{p.name}</button>)}</div>
      )}
      {q.progress && <div className="jq-progress">{q.progress}</div>}
      {open && (
        <div className="jq-body">
          {q.description && q.description !== q.title.replace(/^\w+:\s*/, "") && <p>{q.description}</p>}
          {q.depends_on != null && <p><b>Waits on:</b> {String(q.depends_on)}</p>}
          {details.length > 0 && <dl>{details.map(([k, v]) => (
            <div key={k}><dt>{humanize(k)}</dt><dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>))}</dl>}
          {q.source && <p className="sub">Record: {q.source}</p>}
        </div>
      )}
    </article>
  );
}

// ------------------------------------------------------------------ chronicle: timeline, lore notebook, imported history
const EVENT_ICON: Record<string, typeof Clock3> = {
  time_advanced: Clock3, hp_changed: Heart, mana_changed: Zap, item_added: Backpack, item_removed: Backpack, item_transferred: Backpack,
  quest_added: Flag, quest_updated: Flag, quest_completed: Flag, achievement: Award, location_changed: Footprints, encounter_start: Swords,
  encounter_end: Swords, advancement_checkpoint: Sparkles, fact_recorded: Scroll, scene_started: MapPin, group_moved: Footprints,
};
const TABS = [{ id: "timeline", label: "Timeline", icon: CalendarDays }, { id: "lore", label: "Lore notebook", icon: BookOpen },
  { id: "before", label: "Before the console", icon: Scroll }] as const;

export function ChronicleView({ cid, version, openCharacter }: { cid: number; version: number; openCharacter: (id: number) => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("timeline");
  const [chron, setChron] = useState<Chronology | null>(null);
  const [lore, setLore] = useState<LoreFact[] | null>(null);
  const [bookkeeping, setBookkeeping] = useState(false);
  const [cat, setCat] = useState<string | null>(null);
  useEffect(() => { api.chronology(cid).then(setChron).catch(() => {}); api.loreNotebook(cid).then(setLore).catch(() => setLore([])); }, [cid, version]);
  const cats = useMemo(() => Array.from(new Set((lore ?? []).map((f) => f.category))).sort(), [lore]);
  return (
    <div className="view-inner chronicle">
      <div className="h1">Chronicle</div>
      <nav className="tabs" role="tablist" aria-label="Chronicle sections">
        {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}><t.icon size={13} /> {t.label}</button>)}
      </nav>
      {tab === "timeline" && (!chron ? <div className="muted">Loading…</div> : (
        <>
          <div className="row" style={{ margin: "10px 0" }}>
            <div className="sub grow">What happened in this console, by Dungeon Day (game time; newest first). Earlier history is under "Before the console".</div>
            <label className="sub"><input type="checkbox" checked={bookkeeping} onChange={(e) => setBookkeeping(e.target.checked)} /> show record-keeping</label>
          </div>
          {chron.days.length === 0 && <Empty>No events yet.</Empty>}
          {chron.days.map((d) => {
            const evs = d.events.filter((e) => bookkeeping || !e.bookkeeping);
            if (!evs.length) return null;
            return (
              <section key={String(d.day)} className="tl-day">
                <div className="h2">{d.day != null ? `Dungeon Day ${d.day}` : "Corrections and records (no game time)"} <span className="muted">{evs.length}</span></div>
                <ol className="tl">{evs.map((e) => { const Icon = EVENT_ICON[e.type] ?? Scroll; return (
                  <li key={e.id} className={e.bookkeeping ? "bk" : ""}>
                    <span className="mono muted tl-time">{e.game_time_minutes != null ? `T+${fmtMinutes(e.game_time_minutes)}` : "—"}</span>
                    <Icon size={13} className="tl-icon" /><span>{e.summary}</span>
                  </li>); })}</ol>
              </section>
            );
          })}
        </>
      ))}
      {tab === "lore" && (!lore ? <div className="muted">Loading…</div> : (
        <>
          <div className="chips" style={{ margin: "10px 0" }} role="group" aria-label="Filter by category">
            <button className={`chip ${cat == null ? "cyan" : ""}`} onClick={() => setCat(null)}>All <span className="mono">{lore.length}</span></button>
            {cats.map((c) => <button key={c} className={`chip ${cat === c ? "cyan" : ""}`} onClick={() => setCat(cat === c ? null : c)}>{c} <span className="mono">{lore.filter((f) => f.category === c).length}</span></button>)}
          </div>
          {lore.length === 0 && <Empty>No established facts recorded.</Empty>}
          <div className="lore-grid">{lore.filter((f) => !cat || f.category === cat).map((f) => (
            <article key={f.id} className="lore-card">
              <div className="lore-head">
                <Chip tone={f.category === "People" ? "cyan" : f.category === "Threats" ? "red" : f.category === "Places" || f.category === "Intelligence" ? "violet" : ""}>{f.category}</Chip>
                {f.topic && (f.character_id ? <button className="link-btn" onClick={() => openCharacter(f.character_id!)}>{f.topic}</button> : <b className="lore-topic">{f.topic}</b>)}
                {f.certainty !== "canonical" && <Chip tone="amber">{f.certainty}</Chip>}
              </div>
              <p>{f.fact}</p>
              {f.source && <div className="lore-src" title={f.source}>{f.source}</div>}
            </article>))}
          </div>
        </>
      ))}
      {tab === "before" && (!chron ? <div className="muted">Loading…</div> : (
        <>
          <div className="sub" style={{ margin: "10px 0" }}>Events the campaign ledger records as completed before this console. The records give no dates, so they're listed in the order recorded, not as a dated timeline. The full originals are in Records.</div>
          {chron.imported_history.length === 0 ? <Empty>No imported history.</Empty> : (
            <ul className="before">{chron.imported_history.map((h) => <li key={h.id}>{h.fact}</li>)}</ul>
          )}
        </>
      ))}
    </div>
  );
}
