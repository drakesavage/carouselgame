import { createContext, ReactNode, useContext, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle, ArrowUpCircle, Award, Backpack, Bookmark, ChevronDown, ChevronRight, Clock3, Flag, Footprints, Hammer, Handshake,
  MapPin, Skull, Sparkles, Sun, Swords, TrendingUp, Trophy, Wrench, Zap,
} from "lucide-react";
import { Campaign, CharacterCard, Encounter, fmtMinutes, Group, Manifest, Quest } from "../api";
import { Bar, Chip, humanize, Panel, Portrait, sized } from "../ui";
import { ArtButton, ArtQueue } from "./Art";

// ------------------------------------------------------------------ local artwork
export const ManifestContext = createContext<Manifest>({ locations: {}, characters: {} });
export const useManifest = () => useContext(ManifestContext);

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

/** The player character's group: who is physically with them right now. */
export function pcGroup(campaign: Campaign) {
  const key = campaign.roster.find((c) => c.id === campaign.active_character_id)?.assignment;
  return campaign.groups.find((g) => g.key === key) ?? null;
}

/** Compact scene header pinned above the transcript: where, what, who is here. */
export function SceneBanner({ campaign, onArt, openCharacter }: { campaign: Campaign; onArt?: () => void; openCharacter?: (id: number) => void }) {
  const m = useManifest();
  const [open, setOpen] = useState(false);
  const s = campaign.scene;
  const art = (s && campaign.art?.scene[String(s.id)]) || locationArt(m, s?.location, s?.title, campaign.current_location);
  const here = pcGroup(campaign);
  const others = (here?.members ?? []).filter((c) => c.id !== campaign.active_character_id);
  const safe = campaign.safe_room && sameplace(s?.location, campaign.current_location);
  const now = campaign.active_objective ?? campaign.quests.find((q) => q.immediate)?.title;
  return (
    <header className={`banner ${open ? "open" : ""}`} aria-label="Current scene">
      {art ? <img className="banner-art" src={sized(art, 1024)!} alt="" /> : <BannerArt seed={s?.location ?? campaign.current_location ?? campaign.name} />}
      <div className="banner-shade" />
      <div className="banner-body">
        <div className="banner-kicker">
          <MapPin size={12} /> {s?.location ?? campaign.current_location ?? "Unknown location"}
          <span className={`safety ${safe ? "safe" : "exposed"}`}>{safe ? "saferoom" : "exposed"}</span>
        </div>
        <div className="banner-title">{s?.title ?? "No scene recorded"}{s && <span className="banner-paint"><ArtButton cid={campaign.id} kind="scene" subjectId={s.id} subjectName={s.title} label="" onChanged={onArt} /></span>}</div>
        <div className="banner-meta">
          {others.length > 0 && (
            <span className="banner-with" title={`With you: ${others.map((c) => c.name).join(", ")}`}>
              <span className="sr-only">With you:</span>
              {others.slice(0, 7).map((c) => (
                <button key={c.id} className="mini-face" onClick={() => openCharacter?.(c.id)} aria-label={`${c.name}: open dossier`} title={c.name}>
                  <Portrait name={c.name} src={portraitSrc(c, m)} crop={c.portrait_crop} size={22} />
                </button>
              ))}
              {others.length > 7 && <span className="muted">+{others.length - 7}</span>}
            </span>
          )}
          <span><Sun size={12} /> Day {campaign.day.day} · reset in {fmtMinutes(campaign.day.minutes_to_reset)}</span>
          {s?.situation && <button className="banner-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Situation {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>}
        </div>
        {now && <div className="banner-now narrow-only"><Flag size={11} /> {now}</div>}
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

/** A stable hue per speaker, so each voice keeps its colour across the transcript. */
export function speakerHue(name: string) {
  return hash(name.toLowerCase()) % 360;
}

const nodeText = (n: any): string => (n?.type === "text" ? n.value : (n?.children ?? []).map(nodeText).join(""));
const OPENS_QUOTE = /^\s*[:\-—]?\s*["“‘']/;

/** The paragraph that asks the player for a decision (the narration's closing question), if any. */
export function splitDecision(text: string): [string, string | null] {
  const paras = text.trimEnd().split(/\n\s*\n/);
  const last = paras[paras.length - 1]?.trim() ?? "";
  const asks = /\?\**\s*$/.test(last) || /^\**(your )?(next )?(decision|move)\b/i.test(last);
  if (paras.length < 2 || !asks || last.length > 320 || /^[-|>#]|^\*\s/.test(last)) return [text, null];
  return [paras.slice(0, -1).join("\n\n"), last];
}

/**
 * Narration with visual-novel dialogue: a paragraph led by a bold "Name:" label is a spoken line when the rest opens
 * with a quotation ("**Nate:** “Together is good.”"), shown with the speaker's portrait and colour; "Erin, over HUD:"
 * names the channel. Any other bold label ("**Floor time:** …", "**Margit:** She accepted…") is a record line, not speech.
 */
export function StoryText({ text, roster }: { text: string; roster: CharacterCard[] }) {
  const m = useManifest();
  const components = {
    h1: ({ children }: any) => <h2 className="story-heading">{children}</h2>,
    h2: ({ children }: any) => <h2 className="story-heading">{children}</h2>,
    h3: ({ children }: any) => <h3 className="story-heading small">{children}</h3>,
    p: ({ node, children }: any) => {
      const first: any = node?.children?.[0];
      const label = first?.type === "element" && first.tagName === "strong" ? nodeText(first).trim() : "";
      const rest = (node?.children ?? []).slice(1).map(nodeText).join("");
      const labelled = label.length > 0 && label.length < 48 && (/:$/.test(label) || /^\s*:/.test(rest));
      if (!labelled) return <p>{children}</p>;
      const [rawName, channel] = label.replace(/:$/, "").split(/\s*(?:,|—|–| - )\s*/, 2);
      const who = findSpeaker(roster, rawName);
      const kids = Array.isArray(children) ? children.slice(1) : [];
      if (!OPENS_QUOTE.test(rest)) return <p className="record-line">{children}</p>;
      const name = who?.name ?? rawName;
      return (
        <div className="vn-line" style={{ ["--hue" as any]: speakerHue(name) }}>
          <Portrait name={name} src={portraitSrc(who, m)} crop={who?.portrait_crop} size={52} />
          <div className="vn-body">
            <div className="vn-name">{name}{channel && <span className="vn-channel">{channel}</span>}</div>
            <p className="vn-text">{kids}</p>
          </div>
        </div>
      );
    },
  };
  return (
    <div className="narration story">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{text}</ReactMarkdown>
    </div>
  );
}

/** The decision in front of the player, set apart as the last thing before the input. */
export function Decision({ text }: { text: string }) {
  return (
    <div className="decision narration story" role="note" aria-label="Your decision">
      <span className="decision-tag">Your move</span>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
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
export function role(c: CharacterCard): [string, string] {
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
  const grouped = new Set(groups.flatMap((g) => g.members.map((c) => c.id)));
  const others = campaign.roster.filter((c) => !grouped.has(c.id) && c.id !== pcId);
  // counts come from the groups (who is physically together), not from the stored party flag
  const withPc = groups.find((g) => g.key === pcGroup)?.members.length ?? 0;
  const away = groups.filter((g) => g.key !== pcGroup && g.key !== "_party").reduce((n, g) => n + g.members.length, 0);
  const here = groups.filter((g) => g.key === pcGroup || g.key === "_party");
  const offscreen = groups.filter((g) => g.key !== pcGroup && g.key !== "_party" && g.members.length > 0);
  return (
    <Panel title="Roster" accent={pcGroup ? `${withPc} here · ${away} offscreen` : campaign.roster.length || campaign.party.length}>
      <div className="roster">
        {here.map((g) => (
          <section key={g.key} className="roster-group here" aria-label={g.key === "_party" ? g.label : "You are here"}>
            <div className="roster-head">
              <span className="roster-label">{g.key === "_party" ? g.label : "You are here"}</span>
              {g.location && <span className="roster-loc"><MapPin size={10} /> {g.location}</span>}
            </div>
            {[...g.members].sort((a, b) => (a.id === pcId ? -1 : b.id === pcId ? 1 : 0)).map((c) => <Tile key={c.id} c={c} pc={c.id === pcId} openCharacter={openCharacter} />)}
          </section>
        ))}
        {offscreen.length > 0 && <div className="roster-divider">Offscreen party</div>}
        {offscreen.map((g) => <OffscreenGroup key={g.key} g={g} openCharacter={openCharacter} />)}
        {others.length > 0 && (
          <Collapsible label={`Contacts & elsewhere (${others.length})`}>
            {others.map((c) => <Tile key={c.id} c={c} pc={false} openCharacter={openCharacter} />)}
          </Collapsible>
        )}
      </div>
    </Panel>
  );
}

function Collapsible({ label, extra, children, summary }: { label: string; extra?: ReactNode; summary?: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className={`roster-group ${open ? "open" : ""}`}>
      <button className="roster-head as-button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}<span className="roster-label">{label}</span>{extra}
      </button>
      {open ? children : summary}
    </section>
  );
}

/** An offscreen group: collapsed to faces, place and orders; expands to full tiles. */
function OffscreenGroup({ g, openCharacter }: { g: Group; openCharacter: (id: number) => void }) {
  const m = useManifest();
  const hurt = g.members.filter((c) => c.hp_max && c.hp_current != null && c.hp_current / c.hp_max <= 0.3 || c.statuses.some((s) => s.polarity === "debuff"));
  const activity = g.activity?.replace(/\s*\(checkpoints\/latest\.md\)$/, "");
  return (
    <Collapsible
      label={`${g.label} (${g.members.length})`}
      extra={hurt.length > 0 ? <span className="roster-alert" title={`Needs attention: ${hurt.map((c) => c.name).join(", ")}`}><AlertTriangle size={11} /> {hurt.length}</span> : undefined}
      summary={
        <div className="roster-summary">
          <div className="faces">{g.members.map((c) => (
            <button key={c.id} className="mini-face" onClick={() => openCharacter(c.id)} title={c.name} aria-label={`${c.name}: open dossier`}>
              <Portrait name={c.name} src={portraitSrc(c, m)} crop={c.portrait_crop} size={24} />
            </button>))}
          </div>
          <div className="roster-loc-line"><MapPin size={10} /> {g.location ?? <span className="muted">position not recorded</span>}</div>
          {activity && <div className="roster-activity">{activity}</div>}
        </div>
      }
    >
      <div className="roster-loc-line"><MapPin size={10} /> {g.location ?? <span className="muted">position not recorded</span>}</div>
      {activity && <div className="roster-activity">{activity}</div>}
      {g.members.map((c) => <Tile key={c.id} c={c} pc={false} openCharacter={openCharacter} />)}
    </Collapsible>
  );
}

// ------------------------------------------------------------------ campaign sidebar
const KIND_ICON: Record<string, typeof Flag> = { quest: Flag, project: Hammer, crafting: Wrench, commitment: Handshake, reservation: Bookmark };

export function Objective({ q, now }: { q: Quest; now: number }) {
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

type Alert = { key: string; tone: "red" | "amber"; text: string; quest?: number; character?: number };

/** What needs the player's attention now: overdue or imminent deadlines, and hurt or afflicted people in the PC's group. */
export function urgentAlerts(campaign: Campaign): Alert[] {
  const out: Alert[] = [];
  const now = campaign.game_time_minutes;
  for (const q of campaign.quests) {
    if (q.due_at_minutes == null) continue;
    const due = q.due_at_minutes - now;
    if (due < 0) out.push({ key: `q${q.id}`, tone: "red", text: `Overdue: ${q.title}`, quest: q.id });
    else if (due < 180) out.push({ key: `q${q.id}`, tone: "amber", text: `${q.title} in ${fmtMinutes(due)}`, quest: q.id });
  }
  for (const c of pcGroup(campaign)?.members ?? []) {
    const low = c.hp_max && c.hp_current != null && c.hp_current / c.hp_max <= 0.3;
    const bad = c.statuses.filter((s) => s.polarity === "debuff").map((s) => s.name);
    if (low || bad.length) out.push({ key: `c${c.id}`, tone: low ? "red" : "amber", character: c.id,
      text: `${c.name.split(" ")[0]}: ${[low ? `HP ${c.hp_current}/${c.hp_max}` : "", ...bad].filter(Boolean).join(", ")}` });
  }
  const fc = campaign.floor_clock.remaining_minutes;
  if (fc != null && fc < 12 * 60) out.unshift({ key: "floor", tone: "red", text: `Floor collapses in ${fmtMinutes(fc)}` });
  return out;
}

export function CampaignSidebar({ campaign, onArt, openQuest, openCharacter }: {
  campaign: Campaign; onArt?: () => void; openQuest?: (id?: number) => void; openCharacter?: (id: number) => void;
}) {
  const fc = campaign.floor_clock;
  const hours = fc.remaining_minutes != null ? fc.remaining_minutes / 60 : null;
  const tone = hours == null ? "" : hours < 12 ? "danger" : hours > 72 ? "calm" : "";
  const adv = campaign.clocks.find((c) => c.key === "advancement_cycles");
  const safe = campaign.safe_room && sameplace(campaign.scene?.location, campaign.current_location);
  const linked = campaign.quests.filter((q) => q.immediate);
  const alerts = urgentAlerts(campaign);
  return (
    <>
      <Panel title={`Floor ${campaign.current_floor ?? "?"}`} accent={<span className={`safety ${safe ? "safe" : "exposed"}`}>{safe ? "saferoom" : "exposed"}</span>} className="floor-panel">
        <div className="sub">Collapse in</div>
        <div className={`countdown ${tone}`}>{fc.remaining_minutes != null ? fmtMinutes(fc.remaining_minutes) : "unknown"}</div>
        <div className="sub"><Sun size={11} /> Day {campaign.day.day} · daily reset in {fmtMinutes(campaign.day.minutes_to_reset)}</div>
        {!safe && campaign.safe_room ? <div className="sub" style={{ color: "var(--amber)" }} title={`Campaign record: ${campaign.current_location}`}>The record still says saferoom; the scene is elsewhere.</div> : null}
        {adv && (
          <div className="adv-line" title="Advancement cycles toward the next checkpoint">
            <span className="k">Advancement</span>
            <div className="segments" style={{ gridTemplateColumns: `repeat(${adv.maximum ?? 12}, 1fr)` }}>
              {Array.from({ length: adv.maximum ?? 12 }, (_, i) => <i key={i} className={i < adv.current ? "on" : ""} />)}
            </div>
            <span className="mono">{adv.current}/{adv.maximum}</span>
          </div>
        )}
      </Panel>
      {campaign.encounter && <CombatPanel enc={campaign.encounter} campaign={campaign} onArt={onArt} />}
      <Panel title={<><Flag size={13} /> Now</>} className="now-panel">
        {/* linked quests are the immediate objective; the stored string is its summary */}
        {campaign.active_objective ? <div className="now-text">{campaign.active_objective}</div>
          : linked.length === 0 && <div className="muted">No immediate objective recorded.</div>}
        {linked.map((q) => (
          <button key={q.id} className="now-quest" onClick={() => openQuest?.(q.id)}><Flag size={11} /> <span className="grow">{q.title}</span><ChevronRight size={12} /></button>
        ))}
        <button className="link-btn" onClick={() => openQuest?.()}>All objectives ({campaign.quests.length}) <ChevronRight size={12} /></button>
      </Panel>
      {alerts.length > 0 && (
        <Panel title={<><AlertTriangle size={13} /> Alerts</>} accent={alerts.length} className="alerts-panel">
          <ul className="alerts">
            {alerts.map((a) => (
              <li key={a.key} className={a.tone}>
                {a.quest || a.character ? (
                  <button onClick={() => (a.quest ? openQuest?.(a.quest) : openCharacter?.(a.character!))}>{a.text} <ChevronRight size={11} /></button>
                ) : <span>{a.text}</span>}
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <ArtQueue cid={campaign.id} onChanged={onArt} />
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
          {campaign?.art?.mob[f.name.toLowerCase()] ? <img className="foe-art" src={sized(campaign.art.mob[f.name.toLowerCase()], 64)!} alt="" />
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
