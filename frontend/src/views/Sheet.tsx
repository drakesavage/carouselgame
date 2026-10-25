import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Upload, X } from "lucide-react";
import { api, CharacterSheet } from "../api";
import { Bar, Chip, humanize, Portrait, STAT_ABBR, STAT_KEYS, Tree } from "../ui";
import { ArtButton, CropButton } from "./Art";

const DERIVED_ORDER = ["evade", "damage_resistance", "move", "step", "unallocated_stat_points", "size", "health_bar", "level_progress"];
const HIDDEN_SHEET_KEYS = new Set(["attacks", "narrative", "visual", "hotlist"]);

export function CharacterSheetDialog({ cid, charId, onClose, onChanged }: { cid: number; charId: number | null; onClose: () => void; onChanged: () => void }) {
  const [c, setC] = useState<CharacterSheet | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { setC(null); if (charId != null) api.character(cid, charId).then(setC).catch((e) => setErr(String(e))); }, [cid, charId]);

  const upload = async (f: File) => {
    try { await api.uploadPortrait(cid, charId!, f); setC(await api.character(cid, charId!)); onChanged(); }
    catch (e) { setErr(String(e)); }
  };
  const mods = c?.stats?.modifiers ?? {};
  const stats = c?.stats?.stats ?? {};
  const attacks = c?.sheet?.attacks as Record<string, any> | any[] | undefined;
  const attackList = attacks ? (Array.isArray(attacks) ? attacks : Object.values(attacks)) : [];
  const narrative = c?.sheet?.narrative as Record<string, unknown> | undefined;
  const ref = ((c?.sheet?.visual as Record<string, any> | undefined)?.reference ?? null) as Record<string, string> | null;
  const skills = c?.skills.filter((s) => s.kind !== "spell") ?? [];
  const spells = c?.skills.filter((s) => s.kind === "spell") ?? [];

  return (
    <Dialog.Root open={charId != null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="sheet" aria-describedby={undefined}>
          {!c ? <div className="panel-body">{err ?? "Loading sheet…"}</div> : (
            <>
              <div className="sheet-head">
                <div>
                  <Portrait name={c.name} path={c.portrait_path} crop={c.portrait_crop} size={132} />
                  <input ref={file} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
                  <div className="row" style={{ marginTop: 6, gap: 4 }}>
                    <button className="btn small ghost grow" onClick={() => file.current?.click()}><Upload size={12} /> Upload</button>
                    <ArtButton cid={cid} kind="character" subjectId={c.id} subjectName={c.name} onChanged={async () => { setC(await api.character(cid, charId!)); onChanged(); }} />
                  </div>
                  {c.portrait_path && <CropButton cid={cid} c={c} onSaved={async () => { setC(await api.character(cid, charId!)); onChanged(); }} />}
                </div>
                <div style={{ minWidth: 0 }}>
                  <Dialog.Title className="h1">{c.name}</Dialog.Title>
                  <div className="sub" style={{ marginBottom: 10 }}>
                    {[c.level != null ? `Level ${c.level}` : null, c.race ?? c.species, c.class_name].filter(Boolean).join(" · ")}
                    {c.crawler_number ? ` · Crawler #${c.crawler_number}` : ""}
                  </div>
                  <div className="chips" style={{ marginBottom: 10 }}>
                    <Chip tone="cyan">{humanize(c.roster_category ?? c.kind)}</Chip>
                    <Chip tone={c.status === "active" ? "green" : "amber"}>{c.status}</Chip>
                    {c.ai_favor != null && <Chip tone="magenta">AI Favor {c.ai_favor}</Chip>}
                    {c.in_party ? <Chip>in party</Chip> : null}
                  </div>
                  <div style={{ maxWidth: 360 }}>
                    <Bar kind="hp" cur={c.hp_current} max={c.hp_max} label="Health" />
                    <Bar kind="mana" cur={c.mana_current} max={c.mana_max} label="Mana" />
                  </div>
                  {c.bio && <div style={{ marginTop: 10, fontSize: 13.5 }}>{c.bio}</div>}
                </div>
                <Dialog.Close className="btn ghost small" aria-label="Close"><X size={16} /></Dialog.Close>
              </div>
              <div className="sheet-body">
                {Object.keys(stats).length > 0 && (
                  <>
                    <div className="h2">Stats</div>
                    <div className="statgrid">
                      {STAT_KEYS.filter((k) => k in stats || k in mods).map((k) => (
                        <div className="stat" key={k}><div className="abbr">{STAT_ABBR[k]}</div><div className="val">{stats[k] ?? "?"}</div><div className="mod">{mods[k] != null ? (mods[k] >= 0 ? "+" : "") + mods[k] : "?"}</div></div>
                      ))}
                    </div>
                  </>
                )}
                {Object.keys(c.derived ?? {}).length > 0 && (
                  <div className="kv" style={{ marginTop: 10 }}>
                    {DERIVED_ORDER.filter((k) => k in c.derived).map((k) => {
                      const v = c.derived[k] as any;
                      const shown = v && typeof v === "object" ? (v.value ?? v.category ?? (v.current != null ? `${v.current}/${v.target ?? "?"}` : v.slots != null ? `${v.slots} × ${v.health_per_slot}` : JSON.stringify(v))) : v;
                      return <div key={k}><div className="k">{humanize(k)}</div><div className="v">{String(shown)}</div></div>;
                    })}
                  </div>
                )}
                {c.statuses.length > 0 && (<><div className="h2">Conditions</div><div className="chips">{c.statuses.map((s) => <Chip key={s.id} tone={s.polarity === "buff" ? "green" : s.polarity === "debuff" ? "red" : ""} title={s.description ?? ""}>{s.name}</Chip>)}</div></>)}

                {skills.length > 0 && (
                  <>
                    <div className="h2">Skills</div>
                    <table className="data"><thead><tr><th>Skill</th><th>Rank</th><th>Notes</th></tr></thead>
                      <tbody>{skills.map((s) => (
                        <tr key={s.id}>
                          <td>{s.name} {s.marked ? <span className="dot" title="Marked for advancement" /> : null} {s.kind === "equipment_skill" && <Chip>gear</Chip>}</td>
                          <td className="mono">{s.rank ?? "?"}</td>
                          <td className="sub">{["specialty", "rank_5_upgrade", "perk", "provenance", "source"].map((k) => s.properties?.[k] ? <div key={k}>{humanize(k)}: {String(s.properties[k])}</div> : null)}</td>
                        </tr>))}
                      </tbody>
                    </table>
                  </>
                )}
                {spells.length > 0 && (
                  <>
                    <div className="h2">Spells</div>
                    <table className="data"><thead><tr><th>Spell</th><th>Rank</th><th>Mana</th><th>Effect</th></tr></thead>
                      <tbody>{spells.map((s) => <tr key={s.id}><td>{s.name}</td><td className="mono">{s.rank ?? "?"}</td><td className="mono">{s.mana_cost ?? "?"}</td><td className="sub">{String(s.properties?.effect ?? "")}</td></tr>)}</tbody>
                    </table>
                  </>
                )}
                {attackList.length > 0 && (
                  <>
                    <div className="h2">Attacks</div>
                    <table className="data"><thead><tr><th>Attack</th><th>Bonus</th><th>Damage</th><th>Range / notes</th></tr></thead>
                      <tbody>{attackList.map((a: any, i) => (
                        <tr key={i}><td>{a.name}</td><td className="mono">{a.attack_bonus ?? a.attack_bonus_before_disadvantage ?? "?"}</td><td className="mono">{a.damage ?? "—"}</td>
                          <td className="sub">{[a.range, a.reach, a.attack_roll_condition, a.usage_note].filter(Boolean).join(" · ")}</td></tr>))}
                      </tbody>
                    </table>
                  </>
                )}
                {(c.limited_uses?.length ?? 0) > 0 && (
                  <>
                    <div className="h2">Limited uses</div>
                    <div className="chips">{c.limited_uses.map((u) => (
                      <Chip key={u.ability} tone={u.uses >= u.uses_allowed ? "red" : "green"}>{u.ability} · {u.uses_allowed - u.uses}/{u.uses_allowed} left per {u.per.replace("_", " ")}</Chip>
                    ))}</div>
                  </>
                )}
                {Array.isArray(c.sheet?.hotlist) && (c.sheet.hotlist as unknown[]).length > 0 && (
                  <><div className="h2">Hotlist</div><div className="chips">{(c.sheet.hotlist as unknown[]).map((h, i) => <Chip key={i} tone="cyan">{humanize(String(h))}</Chip>)}</div></>
                )}
                <div className="h2">Equipment &amp; inventory</div>
                {c.items.length === 0 ? <div className="muted">Nothing recorded.</div> : (
                  <table className="data"><thead><tr><th>Item</th><th>Qty</th><th>Details</th></tr></thead>
                    <tbody>{c.items.map((it) => (
                      <tr key={it.id}>
                        <td>{it.name} {it.equipped ? <Chip tone="cyan">{it.slot ?? "equipped"}</Chip> : it.slot ? <Chip>{it.slot}</Chip> : null}</td>
                        <td className="mono">{it.quantity ?? <span title="Count not established in the ledger">?</span>}</td>
                        <td className="sub">{Array.isArray(it.properties?.effects) ? (it.properties.effects as string[]).join("; ") : ""}{it.description ? ` ${it.description}` : ""}</td>
                      </tr>))}
                    </tbody>
                  </table>
                )}
                {c.achievements.length > 0 && (<><div className="h2">Achievements</div>{c.achievements.map((a, i) => <div key={i} className="card" style={{ marginBottom: 6 }}><b>{a.title}</b><div className="sub">{a.description}</div></div>)}</>)}
                {c.npc_profile && (c.npc_profile.personality || c.npc_profile.known_motivation) && (
                  <><div className="h2">What you know</div>
                    <div className="card">{c.npc_profile.personality && <div>{c.npc_profile.personality}</div>}{c.npc_profile.known_motivation && <div className="sub">Role: {c.npc_profile.known_motivation}</div>}
                      <div className="sub">Attitude toward the party: {c.npc_profile.attitude == null ? "not recorded" : c.npc_profile.attitude}</div></div></>
                )}
                {ref && (
                  <>
                    <div className="h2">Appearance</div>
                    <div className="card appearance">
                      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                        {[ref.demographic, ref.age ?? ref.size, ref.class_title].filter(Boolean).map((x) => <Chip key={String(x)}>{String(x)}</Chip>)}
                      </div>
                      {ref.physique && <p>{String(ref.physique)}</p>}
                      {ref.outfit && <p>{String(ref.outfit)}</p>}
                      {ref.signature && <p><b>Signature:</b> {String(ref.signature)}</p>}
                      {ref.expression && <p><b>Expression:</b> {String(ref.expression)}</p>}
                    </div>
                  </>
                )}
                {narrative && <><div className="h2">Narrative</div><Tree value={narrative} /></>}
                {c.sheet?.visual ? <><div className="h2">Appearance</div><Tree value={c.sheet.visual} /></> : null}
                {Object.entries(c.sheet ?? {}).filter(([k]) => !HIDDEN_SHEET_KEYS.has(k)).length > 0 && (
                  <>
                    <div className="h2">Full record</div>
                    <Tree value={Object.fromEntries(Object.entries(c.sheet).filter(([k]) => !HIDDEN_SHEET_KEYS.has(k)))} />
                  </>
                )}
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
