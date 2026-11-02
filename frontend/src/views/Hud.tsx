import { createContext, ReactNode, useContext, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, ChevronDown, ChevronRight, Flag, Footprints, MapPin, Skull, Sparkles, TrendingUp, Award } from "lucide-react";
import { Campaign, CharacterCard, Group, Manifest } from "../api";
import { Chip, humanize, Panel, placeKey, Portrait, sized } from "../ui";
import { ArtButton } from "./Art";

// ------------------------------------------------------------------ local artwork
export const ManifestContext = createContext<Manifest>({ locations: {}, characters: {} });
export const useManifest = () => useContext(ManifestContext);

function locationArt(m: Manifest, ...names: (string | null | undefined)[]): string | null {
  const hay = names.filter(Boolean).join(" ").toLowerCase();
  const key = Object.keys(m.locations).sort((a, b) => b.length - a.length).find((k) => hay.includes(k.toLowerCase()));
  return key ? `media/assets/${encodeURIComponent(m.locations[key])}` : null;
}

/** Location art for a location string, most specific place first ("Gilded Feathers, Heavensworth" -> Gilded Feathers, then Heavensworth). */
export function locationArtFor(campaign: Campaign, location?: string | null): string | null {
  const art = campaign.art?.location ?? {};
  for (const part of (location ?? "").split(/,|—| - /).map((p) => p.trim()).filter(Boolean)) {
    const hit = art[placeKey(part)];
    if (hit) return hit;
  }
  return null;
}

/** Uploaded portrait first, then manifest art (data/assets), else the parchment initial. */
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

/** The establishing shot over the transcript: a 16:9 frame captioned with the place and the time. Missing art shows a
 * dark projection card with the place name, never a blank box. */
export function SceneBanner({ campaign, onArt, openCharacter }: { campaign: Campaign; onArt?: () => void; openCharacter?: (id: number) => void }) {
  const m = useManifest();
  const [open, setOpen] = useState(false);
  const s = campaign.scene;
  // this scene's own picture, else the place's (reused for every scene there), else local assets
  const art = (s && campaign.art?.scene[String(s.id)]) || locationArtFor(campaign, s?.location ?? campaign.current_location)
    || locationArt(m, s?.location, s?.title, campaign.current_location);
  const here = pcGroup(campaign);
  const others = (here?.members ?? []).filter((c) => c.id !== campaign.active_character_id);
  const safe = campaign.safe_room && sameplace(s?.location, campaign.current_location);
  const place = s?.location ?? campaign.current_location ?? "Somewhere in Carousel";
  const clock = campaign.clock;
  return (
    <figure className={`establishing ${open ? "open" : ""}`} aria-label="Establishing shot">
      {art ? <img className="establishing-art" src={sized(art, 1024)!} alt="" />
        : <div className="projection" aria-hidden><span>{place}</span></div>}
      <div className="establishing-shade" />
      <div className="establishing-body">
        <span className="establishing-kicker">Establishing shot</span>
        {s?.title && <div className="establishing-title">{s.title}{<span className="banner-paint"><ArtButton cid={campaign.id} kind="scene" subjectId={s.id} subjectName={s.title} label="" onChanged={onArt} /></span>}</div>}
        <div className="establishing-meta">
          {safe ? <span className="safe-badge">Safe place</span> : null}
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
          {s?.situation && <button className="banner-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Situation {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</button>}
        </div>
        {open && s?.situation && <div className="banner-situation">{s.situation}</div>}
      </div>
      <figcaption className="establishing-caption"><MapPin size={11} aria-hidden /> {place}{clock ? ` · Day ${clock.day}, ${clock.time}` : ""}</figcaption>
    </figure>
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
 * Narration with dialogue: a paragraph led by a bold "Name:" label is a spoken line when the rest opens with a quotation
 * ("**Mara:** “Together is good.”"), shown with the speaker's portrait and colour; "Jonah, on the phone:" names the
 * channel. Any other bold label is a record line, not speech.
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
  quest_added: { icon: Flag, tone: "", label: "New lead" },
  quest_completed: { icon: Award, tone: "green", label: "Lead resolved" },
  quest_failed: { icon: Flag, tone: "red", label: "Lead lost" },
  stats_allocated: { icon: TrendingUp, tone: "", label: "Stats" },
  status_added: { icon: AlertTriangle, tone: "amber", label: "Condition" },
  character_down: { icon: Skull, tone: "red", label: "Down" },
  character_death: { icon: Skull, tone: "red", label: "Death" },
  group_moved: { icon: Footprints, tone: "", label: "On the move" },
  scene_started: { icon: MapPin, tone: "", label: "Scene" },
};
/** Records worth a toast or a title card when a turn lands. */
export const IMPORTANT_CARDS = new Set(["character_down", "character_death", "quest_completed"]);

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

// ------------------------------------------------------------------ the cast, grouped by assignment
const ROLE: Record<string, [string, string]> = {
  player_character: ["You", "cyan"], core_party: ["Group", ""], pets_companions: ["Companion", "violet"], allied_npcs: ["Ally", ""],
  external_contacts: ["Contact", "amber"], deceased_npcs: ["Fallen", "red"],
};
export function role(c: CharacterCard): [string, string] {
  return ROLE[c.roster_category ?? ""] ?? [humanize(c.kind), ""];
}

/** The fate axes that aren't at their default, in plain words ("Dead · Wallpaper: missing · Watching"). */
const FATE_DEFAULTS = { physical_condition: "alive", wallpaper_status: "active", participation: "full", persistent_fate: "ongoing" } as const;
export function fateLine(c: CharacterCard): string | null {
  const out: string[] = [];
  if (c.physical_condition && c.physical_condition !== FATE_DEFAULTS.physical_condition) out.push(humanize(c.physical_condition));
  if (c.wallpaper_status && c.wallpaper_status !== FATE_DEFAULTS.wallpaper_status) out.push(`Wallpaper: ${humanize(c.wallpaper_status).toLowerCase()}`);
  if (c.participation && c.participation !== FATE_DEFAULTS.participation) out.push(humanize(c.participation));
  if (c.persistent_fate && c.persistent_fate !== FATE_DEFAULTS.persistent_fate) out.push(humanize(c.persistent_fate));
  return out.length ? out.join(" · ") : null;
}

function Tile({ c, pc, openCharacter }: { c: CharacterCard; pc: boolean; openCharacter: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const m = useManifest();
  const [label, tone] = role(c);
  const fate = fateLine(c);
  return (
    <div className={`tile ${pc ? "pc" : ""} ${open ? "open" : ""}`}>
      <button className="tile-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Portrait name={c.name} src={portraitSrc(c, m)} crop={c.portrait_crop} size={pc ? 56 : 44} />
        <div className="tile-main">
          <div className="row" style={{ gap: 6 }}>
            <span className="pname grow" title={c.name}>{c.name.split(" ")[0]}</span>
            {c.stats.plot_armor.effective != null && <span className="pa" title="Plot Armor (effective)">PA {c.stats.plot_armor.effective}</span>}
            <span className={`role ${tone}`}>{label}</span>
          </div>
          <div className="pclass">{roleLabel(c) ?? "No Archetype yet"}</div>
          {fate && <div className="pfate">{fate}</div>}
        </div>
      </button>
      {c.statuses.length > 0 && !open && <div className="tile-status">{c.statuses.slice(0, 3).map((s) => <i key={s.id} className={`sdot ${s.polarity}`} title={s.name} />)}</div>}
      {open && (
        <div className="tile-detail">
          <div><b>{c.name}</b></div>
          <div className="pclass">{[roleLabel(c), paLabel(c)].filter(Boolean).join(" · ")}</div>
          {c.statuses.length > 0 && <div className="chips">{c.statuses.map((s) => <Chip key={s.id} tone={s.polarity === "buff" ? "green" : s.polarity === "debuff" ? "red" : ""}>{s.name}</Chip>)}</div>}
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
    if (!campaign.groups.length) return [{ key: "_party", label: "The group", location: campaign.current_location, activity: null, members: campaign.party }];
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
    <Panel title="Starring cast" accent={pcGroup ? `${withPc} here · ${away} away` : campaign.roster.length || campaign.party.length}>
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
        {offscreen.length > 0 && <div className="roster-divider">Elsewhere in town</div>}
        {offscreen.map((g) => <OffscreenGroup key={g.key} g={g} openCharacter={openCharacter} />)}
        {others.length > 0 && (
          <Collapsible label={`Elsewhere (${others.length})`}>
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

/** "Final Girl" from base.final-girl; null before Carousel assigns an Archetype. */
export function roleLabel(c: CharacterCard): string | null {
  const id = c.advanced_role_id ?? c.aspect_id ?? c.role_id;
  return id ? id.replace(/^(base|aspect|advanced)\./, "").split(/[.-]/).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") : null;
}

export function paLabel(c: CharacterCard): string | null {
  const pa = c.stats.plot_armor;
  return pa.effective == null ? null : pa.effective === pa.base ? `Plot Armor ${pa.base}` : `Plot Armor ${pa.effective} (base ${pa.base})`;
}

/** A group elsewhere: collapsed to faces, place and orders; expands to full tiles. */
function OffscreenGroup({ g, openCharacter }: { g: Group; openCharacter: (id: number) => void }) {
  const m = useManifest();
  const hurt = g.members.filter((c) => c.statuses.some((s) => s.polarity === "debuff"));
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

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="mechanics-section"><div className="mechanics-title">{title}</div>{children}</div>;
}
