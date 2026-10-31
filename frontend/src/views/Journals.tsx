import { useEffect, useState } from "react";
import { api, CharacterCard } from "../api";
import { Chip, humanize, Portrait } from "../ui";
import { paLabel, portraitSrc, role, roleLabel, useManifest } from "./Hud";
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
                    <div className="pclass">{roleLabel(c) ?? "no Archetype yet"}</div>
                    {c.life?.state === "deceased" ? <div className="sub">{c.life.note ?? "Deceased."}</div> : <div className="sub">{paLabel(c) ?? "Stats not assigned yet."}</div>}
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
