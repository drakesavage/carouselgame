import { useEffect, useState } from "react";
import { Ticket as TicketIcon } from "lucide-react";
import { ActiveStory, api, Campaign, CharacterSheet, StatName } from "../api";
import { Portrait } from "../ui";
import { fateLine, portraitSrc, roleLabel, useManifest } from "./Hud";
import { StoryBar } from "./StoryBar";
import "../wallpaper.css";

const STATS: StatName[] = ["Mettle", "Moxie", "Hustle", "Savvy", "Grit"];
const label = (id: string | null | undefined) => (id ? id.replace(/^(base|aspect|advanced)\./, "").split(/[.-]/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") : null);

/** The Red Wallpaper: the player character's own screen. Their poster, Plot Armor, stats and fate, and what the running
 * story has revealed to them, from GET /characters/{id} and /story only. What isn't revealed is absent: no count of
 * hidden things, no "obscured" rows. */
export function Wallpaper({ campaign, story, version, onChanged, openTickets }: {
  campaign: Campaign; story: ActiveStory | null; version: number; onChanged: () => void; openTickets: () => void;
}) {
  const m = useManifest();
  const [c, setC] = useState<CharacterSheet | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const pcId = campaign.active_character_id;
  useEffect(() => {
    if (pcId == null) return;
    api.character(campaign.id, pcId).then(setC).catch((e) => setErr((e as Error).message));
  }, [campaign.id, pcId, version]);
  if (pcId == null) return <div className="wallpaper"><div className="wp-frame"><p className="wp-empty">No character yet.</p></div></div>;
  if (!c) return <div className="wallpaper"><div className="wp-frame"><p className="wp-empty">{err ?? "The wallpaper peels back…"}</p></div></div>;
  const pa = c.stats.plot_armor;
  const archetype = roleLabel(c);
  const tagline = [label(c.advanced_role_id), label(c.aspect_id)].filter(Boolean).join(" · ");
  const fate = fateLine(c);
  const equipped = (c.tickets ?? []).filter((t) => t.equipped);
  const paMods = c.stats.modifiers.filter((x) => x.target === "plot_armor");
  const paDiffers = pa.base !== pa.current || pa.current !== pa.effective;
  return (
    <div className="wallpaper">
      <div className="wp-frame">
        <header className="wp-head">
          <div className="wp-kicker">The place behind your eyes</div>
          <h1 className="wp-title">The Red Wallpaper</h1>
          <p className="wp-sub">The camera sees a scene. You see the machinery of a story.</p>
        </header>
        <div className="wp-cols">
          <nav className="wp-card wp-nav" aria-label="Your window into the game">
            <h2 className="wp-label">Your window into the game</h2>
            <a href="#wp-poster">Your poster</a>
            <a href="#wp-armor">Plot Armor</a>
            {story && <a href="#wp-cycle">The Plot Cycle</a>}
            <a href="#wp-tickets">Tickets</a>
            <a href="#wp-revealed">Revealed</a>
            <p className="wp-note">The Wallpaper only shows what you can perceive.</p>
          </nav>
          <section className="wp-poster" id="wp-poster" aria-label="Your poster">
            <div className="wp-poster-kicker">A Carousel original</div>
            <div className="wp-portrait">
              {portraitSrc(c, m) ? <Portrait name={c.name} src={portraitSrc(c, m)} crop={c.portrait_crop} size={180} />
                : <svg viewBox="0 0 100 120" className="wp-silhouette" role="img" aria-label={`${c.name} (no portrait)`}><circle cx="50" cy="38" r="20" /><path d="M14 120c0-26 16-46 36-46s36 20 36 46z" /></svg>}
            </div>
            <div className="wp-poster-title">{archetype ?? "Uncast"}</div>
            {tagline && <div className="wp-tagline">{tagline}</div>}
            <div className="wp-billing">Starring {c.name}</div>
          </section>
          <section className="wp-card wp-part" aria-label="Your part in the story">
            <h2 className="wp-label">Your part in the story</h2>
            <div className="wp-armor" id="wp-armor">
              <div><div className="wp-label small">Your Plot Armor</div><p className="wp-small">Not a health bar. A measure of what the story lets you survive.</p></div>
              <div className="wp-armor-num" aria-label={`Plot Armor ${pa.effective ?? "unknown"}`}>{pa.effective ?? "—"}</div>
            </div>
            {paDiffers && pa.effective != null && (
              <p className="wp-small">Base {pa.base} → current {pa.current} → effective {pa.effective}
                {paMods.length > 0 && <> ({paMods.map((x) => `${x.source} ${x.op === "add" ? (x.value >= 0 ? "+" : "") + x.value : "×" + x.value}`).join(", ")})</>}</p>
            )}
            <dl className="wp-stats">
              {STATS.map((s) => {
                const base = c.stats.stats[s], cur = c.stats.current[s];
                return <div key={s}><dt>{s}</dt><dd>{base ?? "—"}{cur != null && base != null && cur !== base && <span className="wp-cur" title="With current effects"> → {cur}</span>}</dd></div>;
              })}
            </dl>
            {fate && <p className="wp-fate">{fate}</p>}
            <StatPoint cid={campaign.id} c={c} onChanged={onChanged} />
            <div className="wp-tickets" id="wp-tickets">
              <div className="wp-label small">Equipped tickets</div>
              {equipped.length ? (
                <ul>{equipped.map((t) => <li key={t.ref}><button className="wp-ticket" onClick={openTickets}><TicketIcon size={12} aria-hidden /> {t.name}</button></li>)}</ul>
              ) : <p className="wp-small">None equipped.</p>}
            </div>
          </section>
          <section className="wp-card wp-revealed" id="wp-revealed" aria-label="What the Wallpaper reveals">
            <h2 className="wp-label">What the Wallpaper reveals</h2>
            {story?.premise || story?.revisions.length ? (
              <>
                {story.premise && <p className="wp-premise">{story.premise}</p>}
                <ul>{[...story.revisions].reverse().map((r, i) => <li key={i}>{r}</li>)}</ul>
              </>
            ) : <p className="wp-small">Nothing has been revealed to you yet.</p>}
          </section>
        </div>
        {story && <div id="wp-cycle"><StoryBar story={story} /></div>}
      </div>
    </div>
  );
}

/** Spending a pending stat point (Plan 4's stat-point endpoint). */
function StatPoint({ cid, c, onChanged }: { cid: number; c: CharacterSheet; onChanged: () => void }) {
  const [stat, setStat] = useState("mettle");
  const [err, setErr] = useState<string | null>(null);
  const n = c.pending_stat_points ?? 0;
  if (n <= 0) return null;
  const spend = async () => { setErr(null); try { await api.spendStatPoint(cid, c.id, stat); onChanged(); } catch (e) { setErr((e as Error).message); } };
  return (
    <div className="wp-point">
      <span>{n} stat point{n > 1 ? "s" : ""} to spend</span>
      <select className="select" value={stat} onChange={(e) => setStat(e.target.value)} aria-label="Stat to raise">
        {STATS.map((k) => <option key={k} value={k.toLowerCase()}>{k}</option>)}
      </select>
      <button className="btn small" onClick={spend}>Spend</button>
      {err && <span className="wp-err">{err}</span>}
    </div>
  );
}
