import { useEffect, useState } from "react";
import { Award, Gift, PackageOpen } from "lucide-react";
import { api, Achievements, fmtMinutes } from "../api";
import { Chip, Empty, Portrait, sized } from "../ui";
import "../trophies.css";

const TIER_TONE: Record<string, string> = { Bronze: "amber", Silver: "", Gold: "amber", Platinum: "cyan", Legendary: "violet", Celestial: "magenta" };

/** Every crawler's achievements, newest first, with what each rewarded, and the boxes still waiting for a saferoom. */
export function AchievementsView({ cid, version, openCharacter }: { cid: number; version: number; openCharacter: (id: number) => void }) {
  const [data, setData] = useState<Achievements | null>(null);
  const [who, setWho] = useState<number | null>(null);
  useEffect(() => { setData(null); api.achievements(cid).then(setData).catch(() => setData({ characters: [], total: 0, unopened_boxes: 0 })); }, [cid, version]);
  if (!data) return <div className="muted">Loading…</div>;
  const shown = who == null ? data.characters : data.characters.filter((c) => c.id === who);
  return (
    <div className="view-inner trophies">
      <div className="h1">Achievements</div>
      <div className="sub">{data.total} achievement{data.total === 1 ? "" : "s"} · {data.unopened_boxes} unopened box{data.unopened_boxes === 1 ? "" : "es"} (boxes open in a saferoom)</div>
      <div className="chips trophy-filter">
        <button className={`chip-btn ${who == null ? "on" : ""}`} onClick={() => setWho(null)}>Everyone</button>
        {data.characters.map((c) => (
          <button key={c.id} className={`chip-btn ${who === c.id ? "on" : ""}`} onClick={() => setWho(c.id)}>
            {c.name.split(" ")[0]} <span className="count">{c.achievements.length}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 && <Empty>No achievements yet. The System is watching.</Empty>}
      {shown.map((c) => (
        <section key={c.id} className="trophy-crawler">
          <button className="trophy-head" onClick={() => openCharacter(c.id)} title="Open their character page">
            <Portrait name={c.name} src={c.portrait_path ? sized(`media/portraits/${encodeURIComponent(c.portrait_path)}`, 64) : null} crop={c.portrait_crop} size={40} />
            <div>
              <div className="trophy-name">{c.name}{c.is_player && <Chip tone="cyan">you</Chip>}</div>
              <div className="sub">{c.achievements.length} achievement{c.achievements.length === 1 ? "" : "s"}{c.boxes.length ? ` · ${c.boxes.length} box${c.boxes.length === 1 ? "" : "es"} to open` : ""}</div>
            </div>
          </button>
          {c.boxes.length > 0 && (
            <div className="box-shelf">
              {c.boxes.map((b) => (
                <div key={b.id} className={`loot-box tier-${(b.tier ?? "").toLowerCase()}`} title={b.from ? `From: ${b.from}` : undefined}>
                  <Gift size={16} /> <span>{b.name}</span>
                </div>
              ))}
            </div>
          )}
          {c.achievements.length === 0 ? <div className="sub" style={{ margin: "6px 0 0 52px" }}>None yet.</div> : (
            <div className="trophy-list">
              {c.achievements.map((a) => (
                <article key={a.id} className="trophy">
                  <div className="trophy-title"><Award size={15} /> New Achievement! {a.title}</div>
                  {a.description && <p>{a.description}</p>}
                  {a.reward && <p className="trophy-reward"><b>Reward:</b> {a.reward}</p>}
                  <div className="chips">
                    {a.box && <Chip tone={TIER_TONE[a.box.split(" ")[0]] ?? ""}>{a.box_opened ? <><PackageOpen size={11} /> opened</> : <><Gift size={11} /> {a.box}</>}</Chip>}
                    {a.game_time_minutes != null && <span className="sub mono">t+{fmtMinutes(a.game_time_minutes)}</span>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
