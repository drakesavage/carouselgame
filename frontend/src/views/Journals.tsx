import { useEffect, useState } from "react";
import { Award, Backpack, Clock3, Flag, Footprints, Heart, Scroll, Sparkles, Swords, Zap } from "lucide-react";
import { api, CharacterCard, EventRow, fmtMinutes, Quest } from "../api";
import { Bar, Chip, Empty, humanize, Portrait } from "../ui";
import { portraitSrc, role, useManifest } from "./Hud";
import { LifeChip } from "./People";

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setData(null); fn().then(setData).catch((e) => setErr(String(e))); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, err };
}

export function CharactersView({ cid, version, openCharacter }: { cid: number; version: number; openCharacter: (id: number) => void }) {
  const { data } = useLoad(() => api.characters(cid), [cid, version]);
  const { data: camp } = useLoad(() => api.campaign(cid), [cid, version]);
  const m = useManifest();
  if (!data) return <div className="muted">Loading…</div>;
  // where people are comes from the party groups; party membership is a separate chip
  const pc = data.find((c) => c.id === camp?.active_character_id);
  const groupOf = (key?: string | null) => camp?.groups.find((g) => g.key === key);
  const heading = (c: CharacterCard) => {
    if (c.life && c.life.state !== "active" && c.life.state !== "contact") return "Fallen, missing & retired";
    const g = groupOf(c.assignment);
    if (!g) return c.in_party ? "Party (no group)" : humanize(c.roster_category ?? c.kind);
    if (pc && g.key === pc.assignment) return `With ${pc.name.split(" ")[0]}`;
    return `Offscreen: ${g.label} · ${g.location ?? "position not recorded"}`;
  };
  const groups = data.reduce<Record<string, CharacterCard[]>>((acc, c) => { (acc[heading(c)] ??= []).push(c); return acc; }, {});
  return (
    <div className="view-inner">
      <div className="h1">Characters</div>
      {Object.entries(groups).map(([g, list]) => (
        <section key={g}>
          <div className="h2">{g} <span className="muted">{list.length}</span></div>
          <div className="grid">
            {list.map((c) => (
              <button key={c.id} className={`card clickable char-card ${c.life?.state === "deceased" ? "gone" : ""}`} onClick={() => openCharacter(c.id)}
                aria-label={`${c.name}: open dossier`}>
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <Portrait name={c.name} src={portraitSrc(c, m)} crop={c.portrait_crop} size={64} />
                  <div className="grow">
                    <div className="pname">{c.name} <span className={`role ${role(c)[1]}`}>{role(c)[0]}</span></div>
                    <div className="pclass">{[c.level != null ? `L${c.level}` : null, c.race ?? c.species, c.class_name].filter(Boolean).join(" · ") || "class not recorded"}</div>
                    {c.life?.state === "deceased" ? <div className="sub">{c.life.note ?? "Deceased."}</div> : c.hp_max != null || c.mana_max != null ? (
                      <><Bar kind="hp" cur={c.hp_current} max={c.hp_max} /><Bar kind="mana" cur={c.mana_current} max={c.mana_max} /></>
                    ) : <div className="sub">No combat sheet recorded.</div>}
                  </div>
                </div>
                <div className="chips" style={{ marginTop: 8 }}>
                  <LifeChip life={c.life} />
                  {c.in_party ? <Chip tone="cyan" title="Member of the System party">party member</Chip> : null}
                  {c.statuses.map((s) => <Chip key={s.id} tone={s.polarity === "buff" ? "green" : s.polarity === "debuff" ? "red" : ""}>{s.name}</Chip>)}
                </div>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function QuestsView({ cid, version, focus }: { cid: number; version: number; focus?: number | null }) {
  const { data } = useLoad(() => api.quests(cid), [cid, version]);
  useEffect(() => { // arriving from the story's objective or an alert: bring that quest into view
    if (data && focus != null) document.getElementById(`quest-${focus}`)?.scrollIntoView({ block: "center" });
  }, [data, focus]);
  if (!data) return <div className="muted">Loading…</div>;
  const section = (title: string, list: Quest[]) => list.length > 0 && (
    <section><div className="h2">{title} <span className="muted">{list.length}</span></div>
      <div className="grid">{list.map((q) => (
        <div key={q.id} id={`quest-${q.id}`} className={`card ${q.id === focus ? "focused" : ""}`}>
          <div className="row"><Flag size={14} color={q.priority === 1 ? "var(--magenta)" : "var(--muted)"} /><b className="grow">{q.title}</b>
            {q.immediate && <Chip tone="cyan" title="Part of the immediate objective">now</Chip>}<Chip>{q.kind}</Chip></div>
          {q.description && <div className="sub" style={{ marginTop: 6 }}>{q.description}</div>}
          {q.progress && <div style={{ marginTop: 6, fontSize: 13 }}>{q.progress}</div>}
          {q.giver && <div className="sub">From: {q.giver}</div>}
        </div>))}
      </div>
    </section>
  );
  return (
    <div className="view-inner">
      <div className="h1">Quest journal</div>
      {data.length === 0 && <Empty>No quests recorded.</Empty>}
      {section("Active quests", data.filter((q) => q.status === "active" && q.kind === "quest"))}
      {section("Projects & crafting", data.filter((q) => q.status === "active" && q.kind !== "quest"))}
      {section("Paused", data.filter((q) => q.status === "paused"))}
      {section("Completed", data.filter((q) => q.status === "completed"))}
      {section("Failed", data.filter((q) => q.status === "failed"))}
    </div>
  );
}

export function NpcView({ cid, version, openCharacter }: { cid: number; version: number; openCharacter: (id: number) => void }) {
  const { data } = useLoad(() => api.npcs(cid), [cid, version]);
  if (!data) return <div className="muted">Loading…</div>;
  return (
    <div className="view-inner">
      <div className="h1">People</div>
      <div className="sub">Allies, companions and contacts — what you have observed of them.</div>
      <div className="grid" style={{ marginTop: 14 }}>
        {data.map((n) => (
          <div key={n.id} className="card clickable" onClick={() => openCharacter(n.id)}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <Portrait name={n.name} path={n.portrait_path} crop={n.portrait_crop} size={56} />
              <div className="grow">
                <div className="pname">{n.name}</div>
                <div className="pclass">{humanize(n.roster_category ?? n.kind)} · {n.status}</div>
                {n.attitude != null && (
                  <div className="row" style={{ marginTop: 4, gap: 3 }} title={`Attitude ${n.attitude}`}>
                    {[-3, -2, -1, 0, 1, 2, 3].map((v) => <span key={v} style={{ width: 10, height: 6, borderRadius: 2, background: v === n.attitude ? (v < 0 ? "var(--red)" : v > 0 ? "var(--hp)" : "var(--text-2)") : "var(--line-2)" }} />)}
                  </div>
                )}
              </div>
            </div>
            {n.personality && <div style={{ fontSize: 13, marginTop: 8 }}>{n.personality}</div>}
            {n.known_motivation && <div className="sub" style={{ marginTop: 4 }}>{n.known_motivation}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

const EVENT_ICON: Record<string, typeof Clock3> = {
  time_advanced: Clock3, hp_changed: Heart, mana_changed: Zap, item_added: Backpack, item_removed: Backpack, item_transferred: Backpack,
  quest_added: Flag, quest_updated: Flag, quest_completed: Flag, achievement: Award, location_changed: Footprints, encounter_start: Swords,
  encounter_end: Swords, advancement_checkpoint: Sparkles, advancement_checkpoint_pending: Sparkles, fact_recorded: Scroll,
};

export function TimelineView({ cid, version }: { cid: number; version: number }) {
  const { data } = useLoad(() => api.timeline(cid), [cid, version]);
  const lore = useLoad(() => api.lore(cid), [cid, version]).data;
  if (!data) return <div className="muted">Loading…</div>;
  return (
    <div className="view-inner">
      <div className="h1">Campaign timeline</div>
      <div className="sub">Validated mechanical events recorded by this console (newest first). Earlier history lives in Records.</div>
      {data.length === 0 ? <Empty>No events yet — they appear as play changes the game state.</Empty> : (
        <table className="data" style={{ marginTop: 12 }}>
          <thead><tr><th style={{ width: 110 }}>Game time</th><th>Event</th></tr></thead>
          <tbody>{data.map((e: EventRow) => { const Icon = EVENT_ICON[e.type] ?? Scroll; return (
            <tr key={e.id}><td className="mono muted">T+{fmtMinutes(e.game_time_minutes)}</td><td><span className="row"><Icon size={14} color="var(--cyan)" />{e.summary}</span></td></tr>
          ); })}</tbody>
        </table>
      )}
      {lore && lore.length > 0 && (
        <>
          <div className="h2">Established world facts</div>
          <ul>{lore.map((f) => <li key={f.id} style={{ marginBottom: 6 }}>{f.fact} {f.certainty !== "canonical" && <Chip tone="amber">{f.certainty}</Chip>}</li>)}</ul>
        </>
      )}
    </div>
  );
}
