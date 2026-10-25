import { createContext, ReactNode, useContext, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle, ArrowUpCircle, Award, Backpack, Bookmark, ChevronDown, ChevronRight, Clock3, Flag, Footprints, Hammer, Handshake,
  MapPin, Skull, Sparkles, Sun, Swords, TrendingUp, Trophy, Wrench, Zap,
} from "lucide-react";
import { Campaign, CharacterCard, Encounter, fmtMinutes, Manifest, Quest } from "../api";
import { Bar, Chip, humanize, Panel, Portrait } from "../ui";
import { ArtButton, ArtQueue } from "./Art";

// ------------------------------------------------------------------ local artwork
export const ManifestContext = createContext<Manifest>({ locations: {}, characters: {} });
const useManifest = () => useContext(ManifestContext);

function locationArt(m: Manifest, ...names: (string | null | undefined)[]): string | null {
  const hay = names.filter(Boolean).join(" ").toLowerCase();
  const key = Object.keys(m.locations).sort((a, b) => b.length - a.length).find((k) => hay.includes(k.toLowerCase()));
  return key ? `media/assets/${encodeURIComponent(m.locations[key])}` : null;
}

/** Uploaded portrait first, then manifest art (data/assets), else the generated placeholder. */
export function portraitSrc(c: CharacterCard | undefined, m: Manifest): string | null {
  if (!c) return null;
  if (c.portrait_path) return `media/portraits/${encodeURIComponent(c.portrait_path)}`;
  return m.characters[c.slug] ? `media/assets/${encodeURIComponent(m.characters[c.slug])}` : null;
}

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Generated skyline banner when no local art exists for a location. */
function BannerArt({ seed }: { seed: string }) {
  const h = hash(seed);
  const hue = h % 360;
  const towers = Array.from({ length: 18 }, (_, i) => {
    const r = (h >> (i % 24)) & 0xff;
    const w = 30 + (r % 40);
    const height = 30 + ((r * 7) % 90);
    return { x: i * 56 + (r % 20), w, height };
  });
  return (
    <svg className="banner-art" viewBox="0 0 1000 160" preserveAspectRatio="xMidYMid slice" aria-hidden>
      <defs>
        <linearGradient id={`sky${h}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={`hsl(${hue} 55% 14%)`} />
          <stop offset="1" stopColor={`hsl(${(hue + 40) % 360} 60% 5%)`} />
        </linearGradient>
      </defs>
      <rect width="1000" height="160" fill={`url(#sky${h})`} />
      <circle cx={120 + (h % 700)} cy="46" r="26" fill={`hsl(${(hue + 180) % 360} 90% 70% / 0.18)`} />
      {towers.map((t, i) => (
        <g key={i}>
          <rect x={t.x} y={160 - t.height} width={t.w} height={t.height} fill={`hsl(${hue} 40% ${8 + (i % 4)}%)`} />
          {Array.from({ length: Math.floor(t.height / 18) }, (_, j) => (
            <rect key={j} x={t.x + 6} y={166 - t.height + j * 18} width={t.w - 12} height="3" fill={`hsl(${(hue + 160) % 360} 90% 65% / ${(i + j) % 3 === 0 ? 0.35 : 0.08})`} />
          ))}
        </g>
      ))}
    </svg>
  );
}

function sameplace(a?: string | null, b?: string | null) {
  if (!a || !b) return !a;
  const first = (x: string) => x.split(/[,—-]/)[0].trim().toLowerCase();
  return first(a) === first(b);
}

export function SceneBanner({ campaign, onArt }: { campaign: Campaign; onArt?: () => void }) {
  const m = useManifest();
  const [open, setOpen] = useState(false);
  const s = campaign.scene;
  const art = (s && campaign.art?.scene[String(s.id)]) || locationArt(m, s?.location, s?.title, campaign.current_location);
  const group = campaign.groups.find((g) => g.key === s?.group_key);
  return (
    <header className={`banner ${open ? "open" : ""}`}>
      {art ? <img className="banner-art" src={art} alt="" /> : <BannerArt seed={s?.location ?? campaign.current_location ?? campaign.name} />}
      <div className="banner-shade" />
      <div className="banner-body">
        <div className="banner-kicker"><MapPin size={12} /> {s?.location ?? campaign.current_location ?? "Unknown location"}{campaign.safe_room && sameplace(s?.location, campaign.current_location) ? " · saferoom" : ""}</div>
        <div className="banner-title">{s?.title ?? "No scene recorded"}{s && <span className="banner-paint"><ArtButton cid={campaign.id} kind="scene" subjectId={s.id} subjectName={s.title} label="" onChanged={onArt} /></span>}</div>
        <div className="banner-meta">
          <span><Sun size={12} /> Day {campaign.day.day} · reset in {fmtMinutes(campaign.day.minutes_to_reset)}</span>
          {group && <span><Footprints size={12} /> {group.label} ({group.members.length})</span>}
          {s?.situation && <button className="banner-toggle" onClick={() => setOpen((o) => !o)}>{open ? "Hide situation" : "Situation"} {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>}
        </div>
        {open && s?.situation && <div className="banner-situation">{s.situation}</div>}
      </div>
    </header>
  );
}

// ------------------------------------------------------------------ story text with dialogue
function findSpeaker(roster: CharacterCard[], label: string): CharacterCard | undefined {
  const l = label.toLowerCase().replace(/[:\s]+$/, "");
  return roster.find((c) => c.name.toLowerCase() === l) ?? roster.find((c) => c.name.toLowerCase().split(" ")[0] === l) ?? roster.find((c) => c.slug === l);
}

export function StoryText({ text, roster }: { text: string; roster: CharacterCard[] }) {
  const m = useManifest();
  return (
    <div className="narration story">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h2 className="story-heading">{children}</h2>,
          h2: ({ children }) => <h2 className="story-heading">{children}</h2>,
          h3: ({ children }) => <h3 className="story-heading small">{children}</h3>,
          p: ({ node, children }) => {
            const first: any = node?.children?.[0];
            const label = first?.type === "element" && first.tagName === "strong" ? String(first.children?.[0]?.value ?? "") : "";
            if (label && /:\s*$/.test(label) && label.length < 40) {
              const who = findSpeaker(roster, label);
              const kids = Array.isArray(children) ? children.slice(1) : [];
              return (
                <div className="dialogue">
                  <Portrait name={who?.name ?? label.replace(/:\s*$/, "")} src={portraitSrc(who, m)} crop={who?.portrait_crop} size={34} />
                  <div><span className="speaker">{who?.name ?? label.replace(/:\s*$/, "")}</span><p>{kids}</p></div>
                </div>
              );
            }
            return <p>{children}</p>;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

// ------------------------------------------------------------------ discovery cards
const CARD: Record<string, { icon: typeof Award; tone: string; label: string }> = {
  item_added: { icon: Backpack, tone: "gold", label: "Loot" },
  achievement: { icon: Trophy, tone: "gold", label: "New achievement" },
  level_awarded: { icon: ArrowUpCircle, tone: "gold", label: "Level up" },
  advancement_checkpoint: { icon: Sparkles, tone: "gold", label: "Advancement checkpoint" },
  skill_changed: { icon: TrendingUp, tone: "cyan", label: "Skill" },
  stats_allocated: { icon: TrendingUp, tone: "cyan", label: "Stats" },
  ai_favor_changed: { icon: Zap, tone: "magenta", label: "AI Favor" },
  quest_added: { icon: Flag, tone: "cyan", label: "New objective" },
  quest_completed: { icon: Award, tone: "green", label: "Objective complete" },
  quest_failed: { icon: Flag, tone: "red", label: "Objective failed" },
  status_added: { icon: AlertTriangle, tone: "amber", label: "Condition" },
  character_down: { icon: Skull, tone: "red", label: "Down" },
  character_death: { icon: Skull, tone: "red", label: "Death" },
  mob_defeated: { icon: Swords, tone: "magenta", label: "Defeated" },
  encounter_start: { icon: Swords, tone: "red", label: "Combat" },
  encounter_end: { icon: Swords, tone: "green", label: "Combat over" },
  floor_collapse: { icon: Clock3, tone: "red", label: "Floor collapse" },
  dungeon_day: { icon: Sun, tone: "amber", label: "New dungeon day" },
  group_moved: { icon: Footprints, tone: "", label: "On the move" },
  scene_started: { icon: MapPin, tone: "", label: "Scene" },
};
export const IMPORTANT_CARDS = new Set(["achievement", "level_awarded", "advancement_checkpoint", "character_down", "character_death", "floor_collapse", "quest_completed"]);

export function DiscoveryCards({ cards }: { cards: { type: string; summary: string }[] }) {
  if (!cards.length) return null;
  return (
    <div className="dcards">
      {cards.map((c, i) => {
        const spec = CARD[c.type] ?? { icon: Sparkles, tone: "", label: humanize(c.type) };
        const tone = c.type === "status_added" && /injur|dying|poison|burn|blood/i.test(c.summary) ? "red" : spec.tone;
        return (
          <div key={i} className={`dcard ${tone}`} style={{ animationDelay: `${i * 70}ms` }}>
            <spec.icon size={16} />
            <div><div className="dcard-label">{spec.label}</div><div className="dcard-text">{c.summary}</div></div>
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ roster grouped by assignment
const ROLE: Record<string, [string, string]> = {
  player_character: ["You", "cyan"], core_party: ["Party", ""], pets_companions: ["Companion", "violet"], allied_npcs: ["Ally", ""],
  external_contacts: ["Contact", "amber"], deceased_npcs: ["Fallen", "red"],
};
function role(c: CharacterCard): [string, string] {
  if (/manager/i.test(c.status)) return ["Manager", "magenta"];
  return ROLE[c.roster_category ?? ""] ?? [humanize(c.kind), ""];
}

function Tile({ c, pc, openCharacter }: { c: CharacterCard; pc: boolean; openCharacter: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const m = useManifest();
  const [label, tone] = role(c);
  return (
    <div className={`tile ${pc ? "pc" : ""} ${open ? "open" : ""}`}>
      <button className="tile-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Portrait name={c.name} src={portraitSrc(c, m)} crop={c.portrait_crop} size={40} />
        <div className="tile-main">
          <div className="row" style={{ gap: 6 }}>
            <span className="pname grow" title={c.name}>{c.name.split(" ")[0]}</span>
            {c.level != null && <span className="mono muted" style={{ fontSize: 11 }}>L{c.level}</span>}
            <span className={`role ${tone}`}>{label}</span>
          </div>
          {c.hp_max != null ? <><Bar kind="hp" cur={c.hp_current} max={c.hp_max} /><Bar kind="mana" cur={c.mana_current} max={c.mana_max} /></> :
            <div className="pclass">{[c.race ?? c.species, c.class_name].filter(Boolean).join(" · ") || c.status}</div>}
        </div>
      </button>
      {c.statuses.length > 0 && !open && <div className="tile-status">{c.statuses.slice(0, 3).map((s) => <i key={s.id} className={`sdot ${s.polarity}`} title={s.name} />)}</div>}
      {open && (
        <div className="tile-detail">
          <div><b>{c.name}</b></div>
          <div className="pclass">{[c.race ?? c.species, c.class_name].filter(Boolean).join(" · ")}{c.ai_favor != null ? ` · AI Favor ${c.ai_favor}` : ""}</div>
          {c.statuses.length > 0 && <div className="chips">{c.statuses.map((s) => <Chip key={s.id} tone={s.polarity === "buff" ? "green" : s.polarity === "debuff" ? "red" : ""}>{s.name}</Chip>)}</div>}
          {c.equipped.length > 0 && <div className="sub">Equipped: {c.equipped.map((e) => e.name).join(", ")}</div>}
          <button className="btn small" onClick={() => openCharacter(c.id)}>Open dossier</button>
        </div>
      )}
    </div>
  );
}

export function RosterPanel({ campaign, openCharacter }: { campaign: Campaign; openCharacter: (id: number) => void }) {
  const pcId = campaign.active_character_id;
  const pcGroup = campaign.roster.find((c) => c.id === pcId)?.assignment;
  const groups = useMemo(() => {
    if (!campaign.groups.length) return [{ key: "_party", label: "Party", location: campaign.current_location, activity: null, members: campaign.party }];
    return [...campaign.groups].sort((a, b) => (a.key === pcGroup ? -1 : b.key === pcGroup ? 1 : 0));
  }, [campaign, pcGroup]);
  const grouped = new Set(groups.flatMap((g) => g.members.map((m) => m.id)));
  const others = campaign.roster.filter((c) => !grouped.has(c.id) && c.id !== pcId);
  const [showOthers, setShowOthers] = useState(false);
  // counts come from the groups (who is physically together), not from the stored party flag
  const pcName = campaign.roster.find((c) => c.id === pcId)?.name.split(" ")[0] ?? "you";
  const withPc = groups.find((g) => g.key === pcGroup)?.members.length ?? 0;
  const away = groups.filter((g) => g.key !== pcGroup && g.key !== "_party").reduce((n, g) => n + g.members.length, 0);
  return (
    <Panel title="Roster" accent={pcGroup ? `${withPc} with ${pcName} · ${away} elsewhere` : campaign.roster.length || campaign.party.length}>
      <div className="roster">
        {groups.map((g) => (
          <section key={g.key} className={`roster-group ${g.key === pcGroup || g.key === "_party" ? "here" : ""}`}>
            <div className="roster-head">
              <span className="roster-label">{g.key === pcGroup ? `With ${pcName}` : g.key === "_party" ? g.label : `Elsewhere: ${g.label}`}</span>
              {g.location ? <span className="roster-loc"><MapPin size={10} /> {g.location}</span>
                : g.key !== "_party" && <span className="roster-loc muted"><MapPin size={10} /> position not recorded</span>}
            </div>
            {g.activity && g.key !== pcGroup && <div className="roster-activity">{g.activity.replace(/\s*\(checkpoints\/latest\.md\)$/, "")}</div>}
            {g.members.sort((a, b) => (a.id === pcId ? -1 : b.id === pcId ? 1 : 0)).map((c) => <Tile key={c.id} c={c} pc={c.id === pcId} openCharacter={openCharacter} />)}
          </section>
        ))}
        {others.length > 0 && (
          <section className="roster-group">
            <button className="roster-head as-button" onClick={() => setShowOthers((s) => !s)}>
              {showOthers ? <ChevronDown size={12} /> : <ChevronRight size={12} />}<span className="roster-label">Contacts &amp; elsewhere ({others.length})</span>
            </button>
            {showOthers && others.map((c) => <Tile key={c.id} c={c} pc={false} openCharacter={openCharacter} />)}
          </section>
        )}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------ campaign sidebar
const KIND_ICON: Record<string, typeof Flag> = { quest: Flag, project: Hammer, crafting: Wrench, commitment: Handshake, reservation: Bookmark };

function Objective({ q, now }: { q: Quest; now: number }) {
  const [open, setOpen] = useState(false);
  const Icon = KIND_ICON[q.kind] ?? Flag;
  const details = Object.entries(q.details ?? {}).filter(([k]) => k !== "ledger_source" && !k.includes("/"));
  const due = q.due_at_minutes != null ? q.due_at_minutes - now : null;
  return (
    <div className={`objective ${q.kind}`}>
      <button className="objective-head" onClick={() => setOpen((o) => !o)}>
        <Icon size={13} />
        <span className="grow">{q.title}</span>
        {due != null && <Chip tone={due < 0 ? "red" : due < 120 ? "amber" : ""}>{due < 0 ? "overdue" : fmtMinutes(due)}</Chip>}
        {q.immediate ? <Chip tone="cyan" title="Part of the immediate objective">now</Chip> : q.priority === 1 && <Chip tone="magenta">priority</Chip>}
      </button>
      {open && (
        <div className="objective-body">
          {q.description && !q.description.startsWith("Ledger objective id:") && <div className="sub">{q.description}</div>}
          {q.progress && <div>Progress: {q.progress}</div>}
          {details.map(([k, v]) => <div key={k} className="kvline"><span>{humanize(k)}</span><span>{typeof v === "object" ? JSON.stringify(v) : String(v)}</span></div>)}
          {q.details && "conflict" in q.details && <div className="sub" style={{ color: "var(--amber)" }}>Records disagree — settle this in play.</div>}
        </div>
      )}
    </div>
  );
}

export function CampaignSidebar({ campaign, onArt }: { campaign: Campaign; onArt?: () => void }) {
  const fc = campaign.floor_clock;
  const hours = fc.remaining_minutes != null ? fc.remaining_minutes / 60 : null;
  const tone = hours == null ? "" : hours < 12 ? "danger" : hours > 72 ? "calm" : "";
  const adv = campaign.clocks.find((c) => c.key === "advancement_cycles");
  return (
    <>
      <Panel title="Floor" accent={campaign.current_floor ?? "?"} className="floor-panel">
        <div className="sub">Floor collapse in</div>
        <div className={`countdown ${tone}`}>{fc.remaining_minutes != null ? fmtMinutes(fc.remaining_minutes) : "unknown"}</div>
        <div className="sub" style={{ marginBottom: 8 }}><Sun size={11} /> Day {campaign.day.day} · daily reset in {fmtMinutes(campaign.day.minutes_to_reset)}</div>
        <div className="stat-line"><span className="k">Location</span><span style={{ textAlign: "right" }}>{campaign.scene?.location ?? campaign.current_location ?? "unknown"}</span></div>
        <div className="stat-line"><span className="k">Saferoom</span><span>{campaign.safe_room && sameplace(campaign.scene?.location, campaign.current_location)
          ? <Chip tone="green">protected</Chip> : campaign.safe_room ? <Chip tone="amber" title={`Campaign record: ${campaign.current_location}`}>record says saferoom</Chip> : <Chip tone="red">exposed</Chip>}</span></div>
        {adv && (
          <div style={{ marginTop: 8 }}>
            <div className="row"><span className="k sub grow">Advancement cycles</span><span className="mono">{adv.current}/{adv.maximum}</span></div>
            <div className="segments" style={{ gridTemplateColumns: `repeat(${adv.maximum ?? 12}, 1fr)` }}>
              {Array.from({ length: adv.maximum ?? 12 }, (_, i) => <i key={i} className={i < adv.current ? "on" : ""} />)}
            </div>
          </div>
        )}
      </Panel>
      {campaign.encounter && <CombatPanel enc={campaign.encounter} campaign={campaign} onArt={onArt} />}
      <ArtQueue cid={campaign.id} onChanged={onArt} />
      {/* linked quests are the immediate objective (tagged "now" below); the stored string only shows when none are linked */}
      {campaign.active_objective && !campaign.quests.some((q) => q.immediate) && <Panel title="Immediate objective"><div style={{ fontSize: 13.5 }}>{campaign.active_objective}</div></Panel>}
      <Panel title="Objectives" accent={campaign.quests.length || ""}>
        {campaign.quests.length ? campaign.quests.map((q) => <Objective key={q.id} q={q} now={campaign.game_time_minutes} />) : <div className="muted">None active.</div>}
      </Panel>
      {campaign.recent_events.length > 0 && (
        <Panel title="Recently">
          <div className="recent">{campaign.recent_events.map((e) => <div key={e.id}>{e.summary}</div>)}</div>
        </Panel>
      )}
    </>
  );
}

// ------------------------------------------------------------------ combat
const PHASES: Record<string, string> = { surprise: "Surprise", mob_declaration: "Mobs declaring", crawler_reaction: "Your reaction — Interrupts?",
  crawler_action: "Crawler actions", ended: "Ended" };

export function CombatPanel({ enc, campaign, onArt }: { enc: Encounter; campaign?: Campaign; onArt?: () => void }) {
  const party = enc.combatants.filter((c) => c.side === "party");
  const foes = enc.combatants.filter((c) => c.side === "foe");
  return (
    <Panel title={<><Swords size={13} /> Combat</>} accent={`Round ${enc.round}`} className="combat">
      <div style={{ fontWeight: 600 }}>{enc.name}</div>
      {enc.phase && <div className="row" style={{ margin: "4px 0 8px" }}><Chip tone={enc.phase === "crawler_reaction" ? "magenta" : "cyan"}>{PHASES[enc.phase] ?? enc.phase}</Chip></div>}
      {foes.map((f) => (
        <div key={f.id} className="combatant">
          {campaign?.art?.mob[f.name.toLowerCase()] ? <img className="foe-art" src={campaign.art.mob[f.name.toLowerCase()]} alt="" />
            : campaign && <ArtButton cid={campaign.id} kind="mob" subjectName={f.name} label="" onChanged={onArt} />}
          <span className={f.defeated ? "muted" : ""} style={{ textDecoration: f.defeated ? "line-through" : undefined }}>{f.name}</span>
          <div className="bar hp" style={{ width: 80 }}><i style={{ width: `${f.hb_percent ?? 0}%`, background: "linear-gradient(90deg,#b8323a,#ff5257)" }} /></div>
          <span className="mono muted">{f.defeated ? "down" : `${f.hb_percent}%`}</span>
        </div>
      ))}
      <div className="h2" style={{ margin: "10px 0 4px", fontSize: 11 }}>Party</div>
      {party.map((p) => (
        <div key={p.id} className="combatant">
          <span>{p.name.split(" ")[0]}</span>
          <span className="pips">{[0, 1].map((i) => <i key={i} className={i < (p.actions_left ?? 0) ? "on" : ""} />)}</span>
          <span className="mono" style={{ fontSize: 11 }}>
            {p.dead ? <span style={{ color: "var(--red)" }}>DEAD</span> : p.dying ? <span style={{ color: "var(--red)" }}>DYING {p.dying.split(" ")[0]}</span>
              : p.evading ? <span style={{ color: "var(--cyan)" }}>evading</span> : `${p.hp_current ?? "?"}/${p.hp_max ?? "?"}`}
          </span>
        </div>
      ))}
    </Panel>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="mechanics-section"><div className="mechanics-title">{title}</div>{children}</div>;
}
