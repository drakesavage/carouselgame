import { useEffect, useMemo, useState } from "react";
import { Award, BookOpen, CalendarDays, ChevronRight, Scroll, Search } from "lucide-react";
import { ActiveStory, api, Campaign, Chronology, fmtClock, LoreFact, SavePoint, StoryHistory } from "../api";
import { ago, Chip, Empty, humanize } from "../ui";
import "../journal.css";

/** What kind of moment an event is, for the Chronicle's label and filters. Anything unlisted is a plain record. */
const KIND: Record<string, string> = {
  travel: "Travel", group_moved: "Travel", location_changed: "Travel", place_learned: "Travel", zone_entered: "Travel", character_moved: "Travel",
  scene_started: "Scene", scene_closed: "Scene",
  story_choice: "Story", story_phase: "Story", story_camera: "Story", story_cast: "Story", story_concluded: "Story", story_omen: "Omen",
  screenplay_revised: "Story", spoiler_confirmed: "Story", ticket_granted: "Story", ticket_revealed: "Story", trope_used: "Story", story_rescue: "Story",
  omen_sighted: "Omen", omen_examined: "Omen",
  npc_met: "Resident", npc_updated: "Resident", npc_attitude: "Resident", resident_recruited: "Resident", resident_dismissed: "Resident",
  character_died: "Fate", character_found: "Fate", fate_restored: "Fate", wallpaper_changed: "Fate", permanent_loss: "Fate", posthumous_window: "Fate",
  poster_created: "Poster", poster_taken: "Poster",
};
const FILTERS = [
  { id: "all", label: "All", kinds: null },
  { id: "travel", label: "Travel", kinds: ["Travel"] },
  { id: "stories", label: "Stories", kinds: ["Story", "Scene"] },
  { id: "people", label: "People", kinds: ["Resident", "Fate", "Poster"] },
  { id: "omens", label: "Omens", kinds: ["Omen"] },
  { id: "saves", label: "Saves", kinds: [] },
] as const;
type FilterId = (typeof FILTERS)[number]["id"];
const TABS = [{ id: "timeline", label: "Timeline", icon: CalendarDays }, { id: "stories", label: "Stories", icon: Award },
  { id: "lore", label: "Clues & facts", icon: BookOpen }, { id: "before", label: "Earlier records", icon: Scroll }] as const;

/** Day of the Carousel clock (minute 0 is 08:00 on day 1), so the Chronicle agrees with the status strip. */
const clockDay = (m: number) => Math.floor((480 + m) / 1440) + 1;
const clockTime = (m: number) => fmtClock(m).split(" · ")[1];

/** The Chronicle: what actually happened, by day of game time, searchable and filterable; the stories that ended; the clues
 * on record. Only player-visible records (journal.py); record-keeping hides behind a toggle. */
export function ChronicleView({ campaign, story, version, openCharacter, openSaves }: {
  campaign: Campaign; story: ActiveStory | null; version: number; openCharacter: (id: number) => void; openSaves: () => void;
}) {
  const cid = campaign.id;
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("timeline");
  const [chron, setChron] = useState<Chronology | null>(null);
  const [lore, setLore] = useState<LoreFact[] | null>(null);
  const [stories, setStories] = useState<StoryHistory[] | null>(null);
  const [saves, setSaves] = useState<SavePoint[] | null>(null);
  const [bookkeeping, setBookkeeping] = useState(false);
  const [filter, setFilter] = useState<FilterId>("all");
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string | null>(null);
  useEffect(() => {
    api.chronology(cid).then(setChron).catch(() => {}); api.loreNotebook(cid).then(setLore).catch(() => setLore([]));
    api.stories(cid).then(setStories).catch(() => setStories([])); api.saves(cid).then(setSaves).catch(() => setSaves([]));
  }, [cid, version]);
  const cats = useMemo(() => Array.from(new Set((lore ?? []).map((f) => f.category))).sort(), [lore]);
  const days = useMemo(() => {
    const kinds = FILTERS.find((f) => f.id === filter)?.kinds;
    const needle = q.trim().toLowerCase();
    const out = new Map<number | null, { id: number; kind: string; summary: string; minutes: number | null; bookkeeping: boolean }[]>();
    for (const d of chron?.days ?? []) for (const e of d.events) {
      const kind = KIND[e.type] ?? "Record";
      if (!bookkeeping && e.bookkeeping) continue;
      if (kinds && !(kinds as readonly string[]).includes(kind)) continue;
      if (needle && !e.summary.toLowerCase().includes(needle)) continue;
      const day = e.game_time_minutes == null ? null : clockDay(e.game_time_minutes);
      if (!out.has(day)) out.set(day, []);
      out.get(day)!.push({ id: e.id, kind, summary: e.summary, minutes: e.game_time_minutes, bookkeeping: e.bookkeeping });
    }
    return [...out.entries()];
  }, [chron, filter, q, bookkeeping]);
  return (
    <div className="view-inner chronicle">
      <div className="chron-layout">
        <div className="chron-main">
          <div className="kicker">The footage that remains</div>
          <h1 className="h1">Every story leaves evidence.</h1>
          <nav className="tabs" role="tablist" aria-label="Chronicle sections">
            {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}><t.icon size={13} aria-hidden /> {t.label}</button>)}
          </nav>
          {tab === "timeline" && (!chron ? <div className="muted">Loading…</div> : (
            <>
              <div className="chron-tools">
                <label className="search-bar chron-search"><Search size={14} aria-hidden /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search what happened" aria-label="Search the Chronicle" /></label>
                <div className="seg" role="group" aria-label="Show">
                  {FILTERS.map((f) => <button key={f.id} className={filter === f.id ? "active" : ""} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>{f.label}</button>)}
                </div>
                <label className="sub"><input type="checkbox" checked={bookkeeping} onChange={(e) => setBookkeeping(e.target.checked)} /> Show record-keeping</label>
              </div>
              {filter === "saves" ? (
                !saves?.length ? <Empty>No save points yet. The game saves at camp.</Empty> : (
                  <ol className="chron-day">{saves.map((s) => (
                    <li key={s.id} className="chron-entry"><span className="chron-time">{ago(s.created_at)}</span><span className="chron-kind">Save</span>
                      <span className="chron-text">{s.label}</span></li>))}
                  </ol>
                )
              ) : days.length === 0 ? <Empty>{q || filter !== "all" ? "Nothing matches." : "Nothing has happened yet."}</Empty> : days.map(([day, evs]) => (
                <section key={String(day)} className="chron-section">
                  <h2 className="h2">{day != null ? `Day ${day}` : "Records with no game time"} <span className="muted">{evs.length}</span></h2>
                  <ol className="chron-day">{evs.map((e) => (
                    <li key={e.id} className={`chron-entry ${e.bookkeeping ? "bk" : ""} k-${e.kind.toLowerCase()}`}>
                      <span className="chron-time">{e.minutes != null ? clockTime(e.minutes) : "—"}</span>
                      <span className="chron-kind">{e.kind}</span>
                      <span className="chron-text">{e.summary}</span>
                    </li>))}
                  </ol>
                </section>
              ))}
            </>
          ))}
          {tab === "stories" && (!stories ? <div className="muted">Loading…</div> : !stories.length ? <Empty>No story has ended yet.</Empty> : (
            <div className="story-cards">{stories.map((s) => (
              <article key={s.id} className="story-card">
                <div className="kicker">{s.started_minutes != null ? fmtClock(s.started_minutes) : "—"} → {s.ended_minutes != null ? fmtClock(s.ended_minutes) : "—"}</div>
                <h3 className="story-card-title">{s.title}</h3>
                <Chip tone={s.ending === "full_success" ? "green" : s.ending === "defeat" ? "red" : "amber"}>{humanize(s.ending)}</Chip>
                {s.cast.length > 0 && <p className="sub">{s.cast.join(", ")}</p>}
              </article>))}
            </div>
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
                    <Chip>{f.category}</Chip>
                    {f.topic && (f.character_id ? <button className="link-btn" onClick={() => openCharacter(f.character_id!)}>{f.topic}</button> : <b className="lore-topic">{f.topic}</b>)}
                    {f.certainty !== "canonical" && <Chip tone="amber">{f.certainty}</Chip>}
                  </div>
                  <p>{f.fact}</p>
                </article>))}
              </div>
            </>
          ))}
          {tab === "before" && (!chron ? <div className="muted">Loading…</div> : (
            <>
              <div className="sub" style={{ margin: "10px 0" }}>Events the campaign records as done before play began. They carry no dates, so they're listed in the order recorded.</div>
              {chron.imported_history.length === 0 ? <Empty>No earlier records.</Empty> : (
                <ul className="before">{chron.imported_history.map((h) => <li key={h.id}>{h.fact}</li>)}</ul>
              )}
            </>
          ))}
        </div>
        <aside className="chron-rail" aria-label="Campaign continuity">
          <section className="rail-section">
            <h3 className="rail-head">Current chapter</h3>
            <p className="chron-chapter">{story ? story.title : campaign.current_location ?? "Somewhere in Carousel"}</p>
            {story && <p className="sub">{story.phase}</p>}
          </section>
          <section className="rail-section">
            <h3 className="rail-head">Save &amp; recover</h3>
            <p className="sub">{campaign.last_save ? `Last saved at camp, ${ago(campaign.last_save.created_at)}` : "Not saved since leaving camp"}</p>
            <button className="link-btn rail-link" onClick={openSaves}>Saves <ChevronRight size={12} aria-hidden /></button>
          </section>
        </aside>
      </div>
    </div>
  );
}
