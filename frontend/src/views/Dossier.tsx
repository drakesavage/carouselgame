import { KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Activity, Award, BookOpen, Brain, ChevronDown, ChevronRight, Crosshair, Dumbbell, Footprints, Gauge, Gem, HardHat, Hand, Heart,
  HeartPulse, History, LayoutGrid, MapPin, MessageCircle, Package, Search, Shield, ShieldCheck, Sparkles, Sword, Swords, Upload, Users,
  Wind, Wrench, X, Zap,
} from "lucide-react";
import { api, Life } from "../api";
import { LifeChip } from "./People";
import { Chip, cropStyle, humanize, Portrait, PortraitCrop, Tree } from "../ui";
import { ArtButton, CropButton } from "./Art";
import "../dossier.css";

// ------------------------------------------------------------------ data (GET /api/campaigns/{cid}/characters/{id}/dossier)
type Attr = { key: string; abbr: string; name: string; value: number | null; modifier: number | null; unenhanced: number | null; standout: boolean };
type Skill = { id: number; name: string; rank: number | null; kind: string; category: string; stat: string | null; learned: boolean; gear_granted: boolean;
  passive: boolean; marked: boolean; mana_cost: number | null; description: string | null; specialty: string | null; perk: string | null; upgrades: string[];
  source: string | null; condition: string | null; provenance: string | null; details: Record<string, unknown>; attack: Record<string, any> | null; signature: boolean };
type Item = { id: number; name: string; quantity: number | null; equipped: boolean; deployed: boolean; rarity: string | null; description: string | null;
  slot: string; effects: string[]; notes: string[]; status: string | null; provenance: string | null; special: boolean; properties: Record<string, unknown>; shared?: boolean };
type Effect = { type: string; name: string; detail: string | null; polarity?: string };
type HistoryEntry = { kind: string; text: string; when: number | null; source: string; at?: string };
type Dossier = {
  id: number; slug: string; name: string; kind: string; is_player: boolean; crawler_number: string | null; level: number | null; race: string | null;
  class_name: string | null; status: string; life: Life; in_party: boolean; roster_category: string | null; portrait_path: string | null; portrait_crop: PortraitCrop | null;
  role: string | null; group: { key: string; label: string; location: string | null; activity: string | null } | null; location: string | null;
  attributes: Attr[];
  combat: { health: { current: number | null; max: number | null; slots: number | null; per_slot: number | null }; mana: { current: number | null; max: number | null };
    evade: unknown; damage_resistance: unknown; move: unknown; step: unknown; size: { category?: string; value?: unknown } | null; resistances: unknown[];
    statuses: { id: number; name: string; polarity: string | null; description: string | null }[]; ai_favor: number | null; attacks: Record<string, any>[];
    level_progress: { current?: number; target?: number; toward_level?: number; note?: string } | null; unallocated_stat_points: unknown };
  build: { class_name: string | null; class_types: string[]; class_benefits: string[]; class_detriments: string[]; race: string | null; race_benefits: string[];
    role: string | null; tactics: string[]; highlights: { title: string; detail: string | null; source: string }[] };
  skills: Skill[]; signature_skill_ids: number[]; category_labels: Record<string, string>; effects: Effect[];
  equipment: { equipped: Item[]; carried: Item[]; linked_shared: Item[]; commitments: Record<string, any>[]; slot_labels: Record<string, string> };
  story: Record<string, any>; history: HistoryEntry[]; hotlist: unknown[];
};

const TABS = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "skills", label: "Skills", icon: Zap },
  { id: "equipment", label: "Equipment", icon: Package },
  { id: "story", label: "Story", icon: BookOpen },
  { id: "history", label: "History", icon: History },
] as const;
type TabId = (typeof TABS)[number]["id"];

const STAT_ICON: Record<string, typeof Brain> = { strength: Dumbbell, intelligence: Brain, constitution: HeartPulse, dexterity: Wind, charisma: MessageCircle };
const SLOT_ICON: Record<string, typeof Sword> = { weapon: Crosshair, armor: Shield, head: HardHat, hands: Hand, feet: Footprints, accessory: Gem, other: Package };
const EFFECT_META: Record<string, { label: string; tone: string }> = {
  class: { label: "Class feature", tone: "gold" }, race: { label: "Race", tone: "violet" }, specialty: { label: "Specialty", tone: "cyan" },
  learned: { label: "Learned", tone: "green" }, equipment: { label: "Equipment", tone: "steel" }, status: { label: "Status", tone: "magenta" },
  limited: { label: "Limited use", tone: "amber" },
};
const signed = (n: number | null | undefined) => (n == null ? "?" : `${n >= 0 ? "+" : ""}${n}`);

// ------------------------------------------------------------------ dialog
export function CharacterDossierDialog({ cid, charId, onClose, onChanged }: { cid: number; charId: number | null; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<Dossier | null>(null);
  const [raw, setRaw] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const [focusSkill, setFocusSkill] = useState<number | null>(null);
  const load = async () => {
    if (charId == null) return;
    try { setD(await api.dossier(cid, charId) as Dossier); } catch (e) { setErr(String(e)); }
  };
  useEffect(() => { setD(null); setRaw(null); setErr(null); load(); }, [cid, charId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (tab === "history" && charId != null && !raw) api.character(cid, charId).then((c) => setRaw(c.sheet as Record<string, unknown>)).catch(() => {}); }, [tab, cid, charId, raw]);
  const changed = async () => { await load(); onChanged(); };
  const openSkill = (id: number) => { setFocusSkill(id); setTab("skills"); };
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { if (body.current) body.current.scrollTop = 0; }, [tab]); // each tab opens at its top (a focused skill scrolls itself)

  return (
    <Dialog.Root open={charId != null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dossier" aria-describedby={undefined}>
          {!d ? <div className="dossier-loading">{err ?? "Opening dossier…"}<Dialog.Title className="sr-only">Character dossier</Dialog.Title></div> : (
            <>
              <Hero cid={cid} d={d} onChanged={changed} />
              <nav className="dossier-tabs" role="tablist" aria-label="Dossier sections" onKeyDown={(e: KeyboardEvent) => {
                const i = TABS.findIndex((t) => t.id === tab);
                if (e.key === "ArrowRight") setTab(TABS[(i + 1) % TABS.length].id);
                if (e.key === "ArrowLeft") setTab(TABS[(i + TABS.length - 1) % TABS.length].id);
              }}>
                {TABS.map((t) => (
                  <button key={t.id} role="tab" aria-selected={tab === t.id} tabIndex={tab === t.id ? 0 : -1} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
                    <t.icon size={15} /> {t.label}
                  </button>
                ))}
              </nav>
              <div className="dossier-body" role="tabpanel" ref={body}>
                {tab === "overview" && <Overview d={d} openSkill={openSkill} openTab={setTab} />}
                {tab === "skills" && <SkillsTab d={d} focus={focusSkill} />}
                {tab === "equipment" && <EquipmentTab d={d} />}
                {tab === "story" && <StoryTab d={d} />}
                {tab === "history" && <HistoryTab d={d} raw={raw} />}
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ------------------------------------------------------------------ hero header
function Hero({ cid, d, onChanged }: { cid: number; d: Dossier; onChanged: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const url = d.portrait_path ? `media/portraits/${encodeURIComponent(d.portrait_path)}` : null;
  const upload = async (f: File) => { try { await api.uploadPortrait(cid, d.id, f); onChanged(); } catch (e) { setErr(String(e)); } };
  const lp = d.combat.level_progress;
  const h = d.combat.health;
  return (
    <header className="dossier-hero">
      <div className="hero-portrait">
        {url ? <div className="hero-img" role="img" aria-label={d.name}
          style={d.portrait_crop ? cropStyle(url, { ...d.portrait_crop, k: Math.min(1, d.portrait_crop.k * 1.25) }) : { backgroundImage: `url("${url}")` }} />
          : <Portrait name={d.name} size={220} />}
        <div className="hero-portrait-actions">
          <input ref={file} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <button className="btn small ghost" onClick={() => file.current?.click()} title="Upload a portrait"><Upload size={12} /> Upload</button>
          <ArtButton cid={cid} kind="character" subjectId={d.id} subjectName={d.name} onChanged={onChanged} />
          {d.portrait_path && <CropButton cid={cid} c={d} onSaved={onChanged} compact />}
        </div>
        {err && <div className="error-box">{err}</div>}
      </div>
      <div className="hero-identity">
        <div className="hero-title-row">
          <Dialog.Title className="hero-name">{d.name}</Dialog.Title>
          {d.crawler_number && <span className="hero-number">#{d.crawler_number}</span>}
        </div>
        <div className="hero-line">{[d.level != null ? `Level ${d.level}` : null, d.race, d.class_name].filter(Boolean).join(" · ")}</div>
        <div className="chips hero-chips">
          {d.is_player ? <Chip tone="cyan">Player character</Chip> : <Chip tone="cyan">{humanize(d.roster_category ?? d.kind)}</Chip>}
          {d.life.state === "active" && /^active\b/i.test(d.life.label) ? <Chip tone="green">{d.life.label}</Chip> : <LifeChip life={d.life} />}
          {d.group && <Chip title={d.group.activity ?? ""}><Users size={11} /> {d.group.label}</Chip>}
          {d.combat.statuses.map((s) => <StatusChip key={s.id} s={s} />)}
        </div>
        {d.role && <p className="hero-role">{d.role}</p>}
        {d.location && <div className="hero-location"><MapPin size={12} /> {d.location}</div>}
      </div>
      <div className={`hero-vitals ${d.life.state === "deceased" ? "gone" : ""}`}>
        {d.life.note && <div className="vitals-note">{d.life.note}</div>}
        <Vital icon={<Heart size={16} />} label="Health" cur={h.current} max={h.max} tone="hp"
          hint={h.slots ? `Health Bar: ${h.slots} slots × ${h.per_slot ?? "?"} HP. Damage and healing move whole slots.` : undefined} slots={h.slots} />
        <Vital icon={<Zap size={16} />} label="Mana" cur={d.combat.mana.current} max={d.combat.mana.max} tone="mana" />
        {lp && lp.target != null && <Vital icon={<Gauge size={16} />} label="Advancement" cur={lp.current ?? 0} max={lp.target} tone="adv"
          hint={lp.note ?? (lp.toward_level ? `Toward Level ${lp.toward_level}` : undefined)} />}
        {d.combat.ai_favor != null && (
          <div className="vital favor" title="AI Favor: the System's goodwill, spent on rerolls and other favors.">
            <div className="vital-head"><Sparkles size={16} /> AI Favor</div><div className="vital-num big">{d.combat.ai_favor}</div>
          </div>
        )}
      </div>
      <Dialog.Close className="btn ghost small dossier-close" aria-label="Close dossier"><X size={16} /></Dialog.Close>
    </header>
  );
}

function Vital({ icon, label, cur, max, tone, hint, slots }: { icon: ReactNode; label: string; cur: number | null; max: number | null; tone: string; hint?: string; slots?: number | null }) {
  const pct = cur != null && max ? Math.max(0, Math.min(100, (cur / max) * 100)) : 0;
  return (
    <div className={`vital ${tone}`} title={hint}>
      <div className="vital-head">{icon} {label}</div>
      <div className="vital-num">{cur ?? "?"} <span>/ {max ?? "?"}</span></div>
      <div className="vital-bar" aria-hidden>
        <i style={{ width: `${pct}%` }} />
        {slots && slots > 1 && slots <= 30 ? Array.from({ length: slots - 1 }, (_, i) => <b key={i} style={{ left: `${((i + 1) / slots) * 100}%` }} />) : null}
      </div>
    </div>
  );
}

function StatusChip({ s }: { s: { name: string; polarity: string | null; description: string | null } }) {
  return <Chip tone={s.polarity === "buff" ? "green" : s.polarity === "debuff" ? "red" : "amber"} title={s.description ?? humanize(s.polarity ?? "condition")}>{s.name}</Chip>;
}

// ------------------------------------------------------------------ overview
function Overview({ d, openSkill, openTab }: { d: Dossier; openSkill: (id: number) => void; openTab: (t: TabId) => void }) {
  const sig = d.signature_skill_ids.map((id) => d.skills.find((s) => s.id === id)!).filter(Boolean);
  const b = d.build;
  return (
    <div className="ov-grid">
      <div className="ov-col">
      <section className="dpanel ov-attrs">
        <h3>Core attributes</h3>
        {d.attributes.length === 0 ? <Empty>Stats not established in the record.</Empty> : (
          <div className="attr-row">{d.attributes.map((a) => <AttributeCard key={a.key} a={a} />)}</div>
        )}
      </section>
      <section className="dpanel ov-skills">
        <h3>Signature skills <button className="link-btn" onClick={() => openTab("skills")}>All {d.skills.length} skills <ChevronRight size={12} /></button></h3>
        {sig.length === 0 ? <Empty>No ranked skills recorded.</Empty> : (
          <div className="sig-skills">{sig.map((s) => <SkillMini key={s.id} s={s} labels={d.category_labels} onOpen={() => openSkill(s.id)} />)}</div>
        )}
      </section>
      <section className="dpanel ov-effects">
        <h3>Active abilities &amp; effects</h3>
        <EffectsList effects={d.effects} />
      </section>
      </div>
      <div className="ov-col">
      <section className="dpanel ov-combat"><h3>Combat readiness</h3><CombatReadiness d={d} /></section>
      <section className="dpanel ov-build">
        <h3>Signature build</h3>
        {(b.class_name || b.race) && (
          <div className="build-head">
            <div className="build-class">{b.class_name ?? "Class not recorded"}</div>
            <div className="chips">{b.class_types.map((t) => <Chip key={t} tone="gold">{t}</Chip>)}{b.race && <Chip tone="violet">{b.race}</Chip>}</div>
          </div>
        )}
        {b.highlights.length === 0 ? <Empty>No class features or specialties recorded.</Empty> : (
          <div className="highlights">{b.highlights.map((h, i) => (
            <div key={i} className={`highlight src-${h.source.replace(/\s/g, "-")}`}>
              <Sparkles size={14} />
              <div><div className="hl-title">{h.title}</div>{h.detail && <div className="hl-detail">{h.detail}</div>}<div className="hl-src">{h.source}</div></div>
            </div>))}
          </div>
        )}
        {b.tactics.length > 0 && <div className="build-tactics"><Swords size={13} /> {b.tactics.join(" ")}</div>}
      </section>
      <section className="dpanel ov-gear">
        <h3>Current equipment <button className="link-btn" onClick={() => openTab("equipment")}>Full gear <ChevronRight size={12} /></button></h3>
        {d.equipment.equipped.length === 0 ? <Empty>Nothing equipped is recorded.</Empty> : (
          <div className="gear-strip">{d.equipment.equipped.map((i) => <GearTile key={i.id} i={i} labels={d.equipment.slot_labels} />)}</div>
        )}
        {d.combat.attacks.length > 0 && (
          <div className="attack-lines">{d.combat.attacks.map((a, i) => (
            <div key={i} className="attack-line"><Crosshair size={12} /> <b>{a.name}</b>
              <span className="mono">{a.attack_bonus != null ? signed(a.attack_bonus) : a.attack_bonus_before_disadvantage != null ? signed(a.attack_bonus_before_disadvantage) : ""}</span>
              <span className="mono">{a.damage ?? ""}</span><span className="sub">{[a.range, a.reach, a.usage_note].filter(Boolean).join(" · ")}</span></div>))}
          </div>
        )}
      </section>
      </div>
    </div>
  );
}

function AttributeCard({ a }: { a: Attr }) {
  const Icon = STAT_ICON[a.key] ?? Activity;
  return (
    <div className={`attr ${a.standout ? "standout" : ""}`} title={`${a.name}${a.unenhanced != null ? ` (unenhanced ${a.unenhanced})` : ""}`}>
      <Icon size={15} className="attr-icon" aria-hidden />
      <div className="attr-abbr">{a.abbr}</div>
      <div className="attr-val">{a.value ?? "?"}</div>
      <div className="attr-mod">{signed(a.modifier)}</div>
      <div className="attr-name">{a.name}</div>
      {a.unenhanced != null && <div className="attr-base">base {a.unenhanced}</div>}
    </div>
  );
}

function CombatReadiness({ d }: { d: Dossier }) {
  const c = d.combat;
  const size = c.size ? [c.size.category, c.size.value].filter((x) => x != null && x !== "").join(" ") : null;
  const cells: [string, unknown, typeof Shield, string][] = [
    ["Evade", c.evade, Wind, "Target number Mobs must beat to hit with most Attacks."],
    ["Damage resistance", c.damage_resistance, ShieldCheck, "Subtracted from incoming damage before resistances and slot loss."],
    ["Move", c.move, Footprints, "Movement per Action."],
    ["Step", c.step, Footprints, "Free repositioning distance."],
    ["Size", size, Users, "Size category and value."],
  ];
  return (
    <div className="combat-grid">
      {cells.filter(([, v]) => v != null && v !== "").map(([label, v, Icon, hint]) => (
        <div key={label} className="cstat" title={hint}><Icon size={14} /><div><div className="cstat-k">{label}</div><div className="cstat-v">{String(v)}</div></div></div>
      ))}
      {c.health.slots != null && (
        <div className="cstat" title="Health is tracked in whole slots: damage smaller than one slot is lost, healing restores slots.">
          <Heart size={14} /><div><div className="cstat-k">Health bar</div><div className="cstat-v">{c.health.slots} × {c.health.per_slot ?? "?"}</div></div>
        </div>
      )}
      <div className="combat-wide">
        <div className="cstat-k">Resistances</div>
        <div className="chips">{c.resistances.length ? c.resistances.map((r, i) => <Chip key={i}>{String(typeof r === "object" ? JSON.stringify(r) : r)}</Chip>) : <span className="sub">none recorded</span>}</div>
      </div>
      <div className="combat-wide">
        <div className="cstat-k">Conditions</div>
        <div className="chips">{c.statuses.length ? c.statuses.map((s) => <StatusChip key={s.id} s={s} />) : <span className="sub">no buffs, debuffs or injuries</span>}</div>
      </div>
    </div>
  );
}

function SkillMini({ s, labels, onOpen }: { s: Skill; labels: Record<string, string>; onOpen: () => void }) {
  return (
    <button className={`skill-mini cat-${s.category}`} onClick={onOpen} title="Open in Skills">
      <div className="sm-rank">{s.rank ?? "?"}</div>
      <div className="sm-body">
        <div className="sm-name">{s.name}{s.marked && <span className="marked-dot" title="Marked for advancement" />}</div>
        <div className="sm-cat">{labels[s.category] ?? s.category}{s.stat ? ` · ${s.stat}` : ""}{s.gear_granted ? " · gear" : ""}</div>
        {(s.specialty || s.perk || s.upgrades[0]) && <div className="sm-note">{[s.specialty && `Specialty: ${s.specialty}`, s.perk && `Perk: ${s.perk}`, !s.specialty && !s.perk && s.upgrades[0]].filter(Boolean).join(" · ")}</div>}
      </div>
    </button>
  );
}

function EffectsList({ effects }: { effects: Effect[] }) {
  const [filter, setFilter] = useState<string | null>(null);
  const types = Array.from(new Set(effects.map((e) => e.type)));
  const shown = filter ? effects.filter((e) => e.type === filter) : effects;
  if (!effects.length) return <Empty>No active features, effects or conditions recorded.</Empty>;
  return (
    <>
      <div className="chips effect-filters">
        <button className={`chip ${filter == null ? "cyan" : ""}`} onClick={() => setFilter(null)}>All {effects.length}</button>
        {types.map((t) => <button key={t} className={`chip ${filter === t ? "cyan" : ""}`} onClick={() => setFilter(filter === t ? null : t)}>{EFFECT_META[t]?.label ?? humanize(t)}</button>)}
      </div>
      <ul className="effects">{shown.map((e, i) => (
        <li key={i} className={`effect tone-${EFFECT_META[e.type]?.tone ?? "steel"}`}>
          <span className="effect-type">{EFFECT_META[e.type]?.label ?? humanize(e.type)}</span>
          <span className="effect-name">{e.name}</span>
          {e.detail && <span className="effect-detail">{e.detail}</span>}
        </li>))}
      </ul>
    </>
  );
}

function GearTile({ i, labels }: { i: Item; labels: Record<string, string> }) {
  const Icon = SLOT_ICON[i.slot] ?? Package;
  return (
    <div className={`gear-tile ${i.rarity ? `rarity-${i.rarity.toLowerCase()}` : ""}`} title={[i.description, ...i.effects].filter(Boolean).join("\n")}>
      <Icon size={22} aria-hidden />
      <div className="gt-name">{i.name}</div>
      <div className="gt-slot">{labels[i.slot] ?? i.slot}{i.effects[0] ? ` · ${i.effects[0]}` : ""}</div>
    </div>
  );
}

// ------------------------------------------------------------------ skills tab
function SkillsTab({ d, focus }: { d: Dossier; focus: number | null }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string>("all");
  const [sort, setSort] = useState<"rank" | "name">("rank");
  const [open, setOpen] = useState<Set<number>>(() => new Set(focus != null ? [focus] : []));
  const focusRef = useRef<HTMLDivElement>(null);
  useEffect(() => { focusRef.current?.scrollIntoView({ block: "center" }); }, []);
  const order = ["signature", "combat", "engineering", "social", "exploration", "magic", "gear", "other"];
  const counts = useMemo(() => {
    const m: Record<string, number> = { all: d.skills.length, signature: d.skills.filter((s) => s.signature).length };
    d.skills.forEach((s) => { m[s.category] = (m[s.category] ?? 0) + 1; });
    return m;
  }, [d.skills]);
  const match = (s: Skill) => !q || `${s.name} ${s.specialty ?? ""} ${s.perk ?? ""} ${s.upgrades.join(" ")}`.toLowerCase().includes(q.toLowerCase());
  const sorted = (list: Skill[]) => [...list].sort((a, b) => sort === "name" ? a.name.localeCompare(b.name) : (b.rank ?? 0) - (a.rank ?? 0) || a.name.localeCompare(b.name));
  // one canonical card per skill: grouped by its category; "Signature" is a filter view, not a second copy
  const groups = cat === "all" ? order.filter((c) => c !== "signature").map((c) => [c, sorted(d.skills.filter((s) => s.category === c && match(s)))] as const)
    : [[cat, sorted(d.skills.filter((s) => (cat === "signature" ? s.signature : s.category === cat) && match(s)))] as const];
  const toggle = (id: number) => setOpen((o) => { const n = new Set(o); n.has(id) ? n.delete(id) : n.add(id); return n; });
  return (
    <div>
      <div className="toolbar">
        <div className="search"><Search size={14} /><input className="input" placeholder="Search skills, specialties, upgrades…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search skills" /></div>
        <select className="select input" value={sort} onChange={(e) => setSort(e.target.value as "rank" | "name")} aria-label="Sort skills"><option value="rank">Sort by rank</option><option value="name">Sort by name</option></select>
      </div>
      <div className="chips cat-filters">
        {["all", ...order].filter((c) => c === "all" || counts[c]).map((c) => (
          <button key={c} className={`chip ${cat === c ? "cyan" : ""}`} onClick={() => setCat(c)}>{c === "all" ? "All" : d.category_labels[c]} <span className="mono">{counts[c]}</span></button>
        ))}
      </div>
      {groups.filter(([, list]) => list.length).length === 0 && <Empty>No skills match.</Empty>}
      {groups.filter(([, list]) => list.length).map(([c, list]) => (
        <section key={c} className="skill-group">
          {cat === "all" && <h4>{d.category_labels[c]}</h4>}
          <div className="skill-cards">{list.map((s) => (
            <div key={s.id} ref={s.id === focus ? focusRef : undefined}>
              <SkillCard s={s} open={open.has(s.id)} onToggle={() => toggle(s.id)} labels={d.category_labels} />
            </div>))}
          </div>
        </section>
      ))}
    </div>
  );
}

function SkillCard({ s, open, onToggle, labels }: { s: Skill; open: boolean; onToggle: () => void; labels: Record<string, string> }) {
  const extra = Object.entries(s.details).filter(([k]) => !["provenance", "source", "condition"].includes(k));
  return (
    <div className={`skill-card cat-${s.category} ${open ? "open" : ""}`}>
      <button className="sc-head" onClick={onToggle} aria-expanded={open}>
        <div className="sc-rank" aria-label={`Rank ${s.rank ?? "unknown"}`}>{s.rank ?? "?"}</div>
        <div className="sc-main">
          <div className="sc-name">{s.name}</div>
          <div className="sc-tags">
            <span>{labels[s.category] ?? s.category}</span>
            {s.stat && <span>{s.stat}</span>}
            {s.gear_granted ? <span className="tag gear">gear-granted</span> : s.kind === "spell" ? <span className="tag spell">spell{s.mana_cost != null ? ` · ${s.mana_cost} mana` : ""}</span> : <span className="tag learned">learned</span>}
            {s.passive && <span className="tag">passive</span>}
            {s.marked && <span className="tag marked" title="Marked for an advancement check">marked</span>}
            {s.upgrades.length > 0 && <span className="tag upgrade">upgrade</span>}
          </div>
          {(s.specialty || s.perk || s.upgrades[0] || s.description) && (
            <div className="sc-summary">{s.specialty && <>Specialty: <b>{s.specialty}</b>. </>}{s.perk && <>Perk: <b>{s.perk}</b>. </>}{!s.specialty && !s.perk && (s.upgrades[0] ?? s.description)}</div>
          )}
        </div>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </button>
      {open && (
        <div className="sc-details">
          {s.description && <p>{s.description}</p>}
          {s.specialty && <p><b>Specialty:</b> {s.specialty}</p>}
          {s.perk && <p><b>Perk:</b> {s.perk}</p>}
          {s.upgrades.map((u, i) => <p key={i}><b>Rank upgrade:</b> {u}</p>)}
          {s.source && <p><b>Granted by:</b> {s.source}</p>}
          {s.condition && <p><b>Condition:</b> {s.condition}</p>}
          {s.attack && <p><b>Attack:</b> {s.attack.name} {s.attack.attack_bonus != null ? signed(s.attack.attack_bonus) : ""} · {s.attack.damage ?? ""}{s.attack.range ? ` · ${s.attack.range}` : ""}{s.attack.reach ? ` · reach ${s.attack.reach}` : ""}</p>}
          {extra.length > 0 && <Tree value={Object.fromEntries(extra)} />}
          {s.provenance && <p className="sub"><b>Provenance:</b> {s.provenance}</p>}
          {!s.description && !s.specialty && !s.perk && !s.upgrades.length && !s.source && !s.attack && !extra.length && !s.provenance && <p className="sub">No further details are recorded for this skill. The rulebooks (Rules view) have its general rules.</p>}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ equipment tab
function EquipmentTab({ d }: { d: Dossier }) {
  const [q, setQ] = useState("");
  const eq = d.equipment;
  const bySlot = useMemo(() => {
    const m: Record<string, Item[]> = {};
    eq.equipped.forEach((i) => (m[i.slot] ??= []).push(i));
    return m;
  }, [eq.equipped]);
  const slots = ["weapon", "armor", "head", "hands", "feet", "accessory", "other"];
  const carried = eq.carried.filter((i) => !q || `${i.name} ${i.description ?? ""} ${i.effects.join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  const special = [...eq.equipped, ...eq.carried].filter((i) => i.special);
  return (
    <div className="eq-grid">
      <section className="dpanel eq-slots">
        <h3>Equipped</h3>
        <div className="slot-grid">{slots.map((slot) => {
          const Icon = SLOT_ICON[slot] ?? Package;
          const list = bySlot[slot] ?? [];
          if (!list.length && (slot === "other" || slot === "accessory")) return null;
          return (
            <div key={slot} className={`slot ${list.length ? "" : "empty"}`}>
              <div className="slot-label"><Icon size={13} /> {eq.slot_labels[slot]}</div>
              {list.length ? list.map((i) => <ItemCard key={i.id} i={i} />) : <div className="slot-empty">nothing recorded</div>}
            </div>
          );
        })}</div>
      </section>
      {(special.length > 0 || eq.linked_shared.length > 0) && (
        <section className="dpanel eq-special">
          <h3>Special equipment &amp; shared gear</h3>
          <div className="sub" style={{ marginBottom: 8 }}>Devices with attachments or construction details, and shared-stash items whose records mention {d.name.split(" ")[0]}.</div>
          <div className="item-list">{[...special, ...eq.linked_shared].map((i) => <ItemCard key={`${i.shared ? "s" : "o"}${i.id}`} i={i} />)}</div>
        </section>
      )}
      <section className="dpanel eq-carried">
        <h3>Carried inventory</h3>
        <div className="search"><Search size={14} /><input className="input" placeholder="Search carried items…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search carried items" /></div>
        {carried.length === 0 ? <Empty>{eq.carried.length ? "No items match." : "No carried (unequipped) items recorded."}</Empty> : <div className="item-list">{carried.map((i) => <ItemCard key={i.id} i={i} />)}</div>}
      </section>
      <section className="dpanel eq-commit">
        <h3>Reserved, planned &amp; promised</h3>
        {eq.commitments.length === 0 ? <Empty>No reservations, projects or promises involve this character.</Empty> : (
          <ul className="commit-list">{eq.commitments.map((q2) => (
            <li key={q2.id}><Chip tone={q2.kind === "reservation" ? "amber" : q2.kind === "project" || q2.kind === "crafting" ? "cyan" : "violet"}>{humanize(q2.kind)}</Chip> {q2.title}
              {q2.kind === "reservation" && <span className="sub"> · not owned yet</span>}</li>))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ItemCard({ i }: { i: Item }) {
  const [open, setOpen] = useState(false);
  const Icon = SLOT_ICON[i.slot] ?? Package;
  const props = Object.entries(i.properties).filter(([k]) => !["status", "current_status", "provenance"].includes(k));
  return (
    <div className={`item-card ${i.rarity ? `rarity-${i.rarity.toLowerCase()}` : ""} ${open ? "open" : ""}`}>
      <button className="ic-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Icon size={18} aria-hidden />
        <div className="ic-main">
          <div className="ic-name">{i.name}{i.quantity != null && i.quantity !== 1 ? <span className="mono"> ×{i.quantity}</span> : i.quantity == null ? <span className="mono" title="Count not established"> ×?</span> : null}</div>
          <div className="ic-tags">{i.rarity && <span className="tag rarity">{i.rarity}</span>}{i.equipped && <span className="tag">equipped</span>}{i.deployed && <span className="tag">deployed</span>}
            {i.shared && <span className="tag shared">shared stash</span>}{i.effects[0] && <span>{i.effects[0]}</span>}</div>
        </div>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <div className="ic-details">
          {i.description && <p>{i.description}</p>}
          {i.status && <p><b>Status:</b> {i.status}</p>}
          {i.effects.length > 0 && <ul>{i.effects.map((e, k) => <li key={k}>{e}</li>)}</ul>}
          {i.notes.length > 0 && <ul className="sub">{i.notes.map((e, k) => <li key={k}>{e}</li>)}</ul>}
          {props.length > 0 && <Tree value={Object.fromEntries(props)} />}
          {i.provenance && <p className="sub"><b>Provenance:</b> {i.provenance}</p>}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ story tab
function StoryTab({ d }: { d: Dossier }) {
  const s = d.story;
  const ap = s.appearance as Record<string, string> | null;
  const entries: [string, unknown][] = [["Past trauma", s.past_trauma], ["Loose end", s.loose_end], ["Regret", s.regret]];
  return (
    <div className="story-grid">
      <section className="dpanel">
        <h3>Personality &amp; role</h3>
        {s.role && <p className="story-lead">{s.role}</p>}
        {s.personality && <p>{s.personality}</p>}
        {(s.traits as string[]).length > 0 && <div className="chips">{(s.traits as string[]).map((t) => <Chip key={t}>{t}</Chip>)}</div>}
        {(s.tactics as string[]).length > 0 && <div className="build-tactics" style={{ marginTop: 10 }}><Swords size={13} /> {(s.tactics as string[]).join(" ")}</div>}
        {s.profile && (s.profile.personality || s.profile.known_motivation) && (
          <div className="known-profile"><h4>What the party knows</h4>{s.profile.personality && <p>{s.profile.personality}</p>}{s.profile.known_motivation && <p className="sub">{s.profile.known_motivation}</p>}</div>
        )}
        {!s.role && !s.personality && !(s.traits as string[]).length && !s.profile && <Empty>No personality or role is recorded.</Empty>}
      </section>
      <section className="dpanel">
        <h3>Assignment</h3>
        {s.assignment ? <p><Users size={13} /> <b>{s.assignment.label}</b>{s.assignment.activity ? ` · ${s.assignment.activity}` : ""}</p> : <p className="sub">No party group recorded.</p>}
        {s.location && <p><MapPin size={13} /> {s.location}</p>}
        <h3 style={{ marginTop: 14 }}>Commitments &amp; personal quests</h3>
        {(s.quests as any[]).length === 0 ? <Empty>None involve this character.</Empty> : <ul className="commit-list">{(s.quests as any[]).map((q) => <li key={q.id}><Chip>{humanize(q.kind)}</Chip> {q.title}</li>)}</ul>}
      </section>
      <section className="dpanel">
        <h3>Background</h3>
        {Object.keys(s.background ?? {}).length === 0 && entries.every(([, v]) => !v) ? <Empty>No background is recorded.</Empty> : (
          <>
            {Object.keys(s.background ?? {}).length > 0 && <div className="bg-grid">{Object.entries(s.background).map(([k, v]) => <div key={k}><div className="cstat-k">{humanize(k)}</div><div>{String(v)}</div></div>)}</div>}
            {entries.filter(([, v]) => v).map(([k, v]) => <Expandable key={k} title={k}>{String(v)}</Expandable>)}
          </>
        )}
      </section>
      <section className="dpanel">
        <h3>Relationships &amp; connections</h3>
        {(s.relationships as any[]).length === 0 ? <p className="sub">No relationships are recorded as known to the party.</p> : (
          <ul className="commit-list">{(s.relationships as any[]).map((r, i) => <li key={i}><Chip tone="violet">{humanize(r.kind)}</Chip> {r.a_name === d.name ? r.b_name : r.a_name}{r.description ? ` · ${r.description}` : ""}</li>)}</ul>
        )}
        {Object.entries(s.other ?? {}).map(([k, v]) => <Expandable key={k} title={humanize(k)}>{typeof v === "string" ? v : <Tree value={v} />}</Expandable>)}
      </section>
      {ap && (
        <section className="dpanel story-wide">
          <h3>Appearance</h3>
          <div className="chips">{[ap.demographic, ap.age ?? ap.size, ap.class_title].filter(Boolean).map((x) => <Chip key={x}>{x}</Chip>)}</div>
          {ap.physique && <p>{ap.physique}</p>}{ap.outfit && <p>{ap.outfit}</p>}
          {ap.signature && <p><b>Signature:</b> {ap.signature}</p>}{ap.expression && <p><b>Expression:</b> {ap.expression}</p>}
        </section>
      )}
    </div>
  );
}

function Expandable({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`expandable ${open ? "open" : ""}`}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {title}</button>
      {open && <div className="expandable-body">{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ history tab
const HISTORY_ICON: Record<string, typeof Award> = { level: Gauge, skill: Zap, item: Package, achievement: Award, injury: HeartPulse, milestone: Wrench };

function HistoryTab({ d, raw }: { d: Dossier; raw: Record<string, unknown> | null }) {
  const [kind, setKind] = useState<string>("all");
  const kinds = Array.from(new Set(d.history.map((h) => h.kind)));
  const shown = kind === "all" ? d.history : d.history.filter((h) => h.kind === kind);
  return (
    <div>
      <div className="chips cat-filters">
        <button className={`chip ${kind === "all" ? "cyan" : ""}`} onClick={() => setKind("all")}>All {d.history.length}</button>
        {kinds.map((k) => <button key={k} className={`chip ${kind === k ? "cyan" : ""}`} onClick={() => setKind(k)}>{humanize(k)}</button>)}
      </div>
      {shown.length === 0 ? <Empty>No progression is recorded for this character yet. Levels, skill advances, gear and achievements from play will appear here.</Empty> : (
        <ol className="timeline">{shown.map((h, i) => {
          const Icon = HISTORY_ICON[h.kind] ?? Wrench;
          return (
            <li key={i} className={`tl-${h.kind}`}>
              <span className="tl-icon"><Icon size={14} /></span>
              <div><div className="tl-text">{h.text}</div><div className="tl-meta">{humanize(h.source)}{h.when != null ? ` · t=${h.when}` : ""}</div></div>
            </li>
          );
        })}</ol>
      )}
      <Expandable title="Full imported record (raw)">{raw ? <Tree value={raw} /> : <span className="sub">Loading…</span>}</Expandable>
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="dossier-empty">{children}</div>;
}
