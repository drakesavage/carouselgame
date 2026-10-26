import { useEffect, useMemo, useState } from "react";
import { Award, Backpack, Clock3, Coins, Flag, Footprints, Heart, Loader2, Scroll, Search, Sparkles, Swords, Zap } from "lucide-react";
import { ArtButton } from "./Art";
import { api, CharacterCard, EventRow, fmtMinutes, Item, Quest } from "../api";
import { Bar, Chip, Empty, humanize, Portrait } from "../ui";

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setData(null); fn().then(setData).catch((e) => setErr(String(e))); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, err };
}

export function CharactersView({ cid, version, openCharacter }: { cid: number; version: number; openCharacter: (id: number) => void }) {
  const { data } = useLoad(() => api.characters(cid), [cid, version]);
  const { data: camp } = useLoad(() => api.campaign(cid), [cid, version]);
  if (!data) return <div className="muted">Loading…</div>;
  // where people are comes from the party groups; party membership is a separate chip
  const pc = data.find((c) => c.id === camp?.active_character_id);
  const groupOf = (key?: string | null) => camp?.groups.find((g) => g.key === key);
  const heading = (c: CharacterCard) => {
    const g = groupOf(c.assignment);
    if (!g) return c.in_party ? "Party (no group)" : humanize(c.roster_category ?? c.kind);
    if (pc && g.key === pc.assignment) return `With ${pc.name.split(" ")[0]}`;
    return `Elsewhere: ${g.label} @ ${g.location ?? "position not recorded"}`;
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
              <div key={c.id} className="card clickable" onClick={() => openCharacter(c.id)}>
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <Portrait name={c.name} path={c.portrait_path} crop={c.portrait_crop} size={64} />
                  <div className="grow">
                    <div className="pname">{c.name}</div>
                    <div className="pclass">{[c.level != null ? `L${c.level}` : null, c.race ?? c.species, c.class_name].filter(Boolean).join(" · ")}</div>
                    <Bar kind="hp" cur={c.hp_current} max={c.hp_max} />
                    <Bar kind="mana" cur={c.mana_current} max={c.mana_max} />
                  </div>
                </div>
                <div className="chips" style={{ marginTop: 8 }}>
                  <Chip tone={c.status === "active" ? "green" : "amber"}>{c.status}</Chip>
                  {c.in_party ? <Chip tone="cyan" title="Member of the System party">party member</Chip> : null}
                  {c.statuses.map((s) => <Chip key={s.id}>{s.name}</Chip>)}
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function InventoryView({ cid, version }: { cid: number; version: number }) {
  const { data } = useLoad(() => api.inventory(cid), [cid, version]);
  const [artTick, setArtTick] = useState(0);
  const { data: itemArt } = useLoad(() => api.artList(cid, { kind: "item" }), [cid, version, artTick]);
  const artFor = (id: number) => itemArt?.find((j) => j.subject_id === id && j.is_current && j.url)?.url ?? undefined;
  const { data: looks } = useLoad(() => api.artAppearances(cid, "item"), [cid, version, artTick]);
  const [drafting, setDrafting] = useState(false);
  const [draftMsg, setDraftMsg] = useState<string | null>(null);
  const draftLooks = async () => {
    setDrafting(true); setDraftMsg(null);
    try {
      const r = await api.artDraftItemLooks(cid);
      setDraftMsg(r.drafted.length ? `Drafted ${r.drafted.length} looks${r.remaining ? `; ${r.remaining} left (run again)` : ""}. Review them on the cards; edit any in its Paint dialog.`
        : "Every item already has a look.");
      setArtTick((t) => t + 1);
    } catch (e) { setDraftMsg(String(e)); } finally { setDrafting(false); }
  };
  const [q, setQ] = useState("");
  const match = (i: Item) => !q || `${i.name} ${i.owner_name ?? ""} ${i.description ?? ""}`.toLowerCase().includes(q.toLowerCase());
  const byOwner = useMemo(() => {
    const m: Record<string, Item[]> = {};
    data?.by_character.filter(match).forEach((i) => (m[i.owner_name ?? "?"] ??= []).push(i));
    return m;
  }, [data, q]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <div className="muted">Loading…</div>;
  const gold = data.shared.find((i) => i.name.toLowerCase() === "gold");
  const shared = data.shared.filter((i) => !i.deployed && i !== gold).filter(match);
  const deployed = data.shared.filter((i) => i.deployed).filter(match);
  const table = (items: Item[]) => (
    <div className="icards">{items.map((i) => {
      const effects = Array.isArray(i.properties?.effects) ? (i.properties.effects as string[]) : [];
      const note = i.description ?? (i.properties?.note as string) ?? (i.properties?.status as string) ?? "";
      const prov = i.properties?.provenance as string | undefined;
      return (
        <div key={i.id} className={`icard ${i.equipped ? "equipped" : ""} ${i.quantity == null ? "unknown" : ""}`}>
          <div className="icard-top">
            {artFor(i.id) && <img className="icard-art" src={artFor(i.id)} alt="" />}
            <span className={`icard-name ${i.rarity ? "rarity-" + i.rarity.toLowerCase() : ""}`}>{i.name}</span>
            <ArtButton cid={cid} kind="item" subjectId={i.id} subjectName={i.name} label="" onChanged={() => setArtTick((t) => t + 1)} />
            {i.quantity == null ? <span className="chip amber" title="Count not established in the ledger">qty ?</span> : <span className="icard-qty">×{i.quantity}</span>}
          </div>
          <div className="chips">
            {i.equipped ? <Chip tone="cyan">{i.slot ?? "equipped"}</Chip> : i.slot ? <Chip>{i.slot}</Chip> : null}
            {i.deployed ? <Chip tone="amber">deployed</Chip> : null}
            {i.rarity && <Chip tone="violet">{i.rarity}</Chip>}
            {i.owner_name && <Chip>{i.owner_name}</Chip>}
          </div>
          {effects.length > 0 && <div>{effects.join("; ")}</div>}
          {note && <div className="sub">{note}</div>}
          {looks?.[String(i.id)] && <div className="sub" title="Appearance notes for pictures (art only, not canon)"><Sparkles size={10} /> <i>{looks[String(i.id)]}</i></div>}
          {prov && <div className="sub">From: {prov}</div>}
          <div className="sub mono" style={{ fontSize: 10.5 }}>#{i.id}</div>
        </div>
      );
    })}</div>
  );
  return (
    <div className="view-inner">
      <div className="row"><div className="h1 grow">Inventory</div>
        <button className="btn small ghost" disabled={drafting} onClick={draftLooks} title="Gemini Flash Lite writes a physical description for every item without one, for item pictures (art only)">
          {drafting ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />} Draft item looks</button>
        {gold && <Chip tone="amber"><Coins size={13} /> {gold.quantity ?? "?"} Gold</Chip>}</div>
      {draftMsg && <div className="sub" style={{ marginTop: 6 }}>{draftMsg}</div>}
      <div className="row" style={{ margin: "12px 0" }}><Search size={16} color="var(--muted)" /><input className="input" placeholder="Filter items or owners…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="h2"><Backpack size={14} /> Shared stash</div>
      {shared.length ? table(shared) : <Empty>Nothing in the shared stash.</Empty>}
      {deployed.length > 0 && (<><div className="h2">Deployed assets</div>{table(deployed)}</>)}
      {Object.entries(byOwner).map(([owner, items]) => (<section key={owner}><div className="h2">{owner}</div>{table(items)}</section>))}
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
