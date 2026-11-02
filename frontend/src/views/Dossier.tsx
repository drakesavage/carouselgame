import { KeyboardEvent, ReactNode, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { BookOpen, ChevronDown, ChevronRight, History, LayoutGrid, Loader2, MapPin, ShieldCheck, Upload, UserRoundPen, Users, X } from "lucide-react";
import { api, CarouselStats, Life, Ticket } from "../api";
import { LifeChip } from "./People";
import { Chip, cropStyle, humanize, Portrait, PortraitCrop, sized, Tree } from "../ui";
import { ArtButton, CropButton } from "./Art";
import { TicketCard } from "./Tickets";
import "../dossier.css";

// ------------------------------------------------------------------ data (GET /api/campaigns/{cid}/characters/{id}/dossier)
type HistoryEntry = { kind: string; text: string; when: number | null; source: string; at?: string };
type Dossier = {
  id: number; slug: string; name: string; kind: string; is_player: boolean; status: string; life: Life; in_party: boolean; roster_category: string | null;
  portrait_path: string | null; portrait_crop: PortraitCrop | null;
  role: { id: string | null; reason: string | null; aspect_id: string | null; advanced_role_id: string | null };
  stats: CarouselStats; rewards?: { stars: number; pending_stat_points: number }; statuses: { id: number; name: string; polarity: string | null; description: string | null }[]; tickets?: Ticket[];
  group: { key: string; label: string; location: string | null; activity: string | null } | null; location: string | null;
  story: Record<string, any>; history: HistoryEntry[];
  private_sheet: "generated" | "authored" | null;
};

const TABS = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "story", label: "Story", icon: BookOpen },
  { id: "history", label: "History", icon: History },
] as const;
type TabId = (typeof TABS)[number]["id"];

const roleName = (id: string | null) => (id ? humanize(id.replace(/^(base|aspect|advanced)\./, "").replace(/\./g, " ")) : null);

// ------------------------------------------------------------------ dialog
export function CharacterDossierDialog({ cid, charId, onClose, onChanged }: { cid: number; charId: number | null; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<Dossier | null>(null);
  const [raw, setRaw] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const load = async () => {
    if (charId == null) return;
    try { setD(await api.dossier(cid, charId) as Dossier); } catch (e) { setErr(String(e)); }
  };
  useEffect(() => { setD(null); setRaw(null); setErr(null); load(); }, [cid, charId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (tab === "history" && charId != null && !raw) api.character(cid, charId).then((c) => setRaw(c.sheet as Record<string, unknown>)).catch(() => {}); }, [tab, cid, charId, raw]);
  const changed = async () => { await load(); onChanged(); };
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { if (body.current) body.current.scrollTop = 0; }, [tab]); // each tab opens at its top

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
                {tab === "overview" && <Overview cid={cid} d={d} onChanged={changed} />}
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
  return (
    <header className="dossier-hero">
      <div className="hero-portrait">
        {url ? <div className="hero-img" role="img" aria-label={d.name}
          style={d.portrait_crop ? cropStyle(sized(url, 512)!, { ...d.portrait_crop, k: Math.min(1, d.portrait_crop.k * 1.25) }) : { backgroundImage: `url("${sized(url, 256)}")` }} />
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
        </div>
        <div className="hero-line">{[roleName(d.role.id), roleName(d.role.aspect_id), roleName(d.role.advanced_role_id)].filter(Boolean).join(" · ") || "No Archetype yet"}</div>
        <div className="chips hero-chips">
          {d.is_player ? <Chip tone="cyan">Player character</Chip> : <Chip tone="cyan">{humanize(d.roster_category ?? d.kind)}</Chip>}
          {d.life.state === "active" && /^active\b/i.test(d.life.label) ? <Chip tone="green">{d.life.label}</Chip> : <LifeChip life={d.life} />}
          {d.story.identity?.pronouns && <Chip title={[d.story.identity.gender, d.story.identity.apparent_age].filter(Boolean).join(", ")}>{d.story.identity.pronouns}</Chip>}
          {d.group && <Chip title={d.group.activity ?? ""}><Users size={11} /> {d.group.label}</Chip>}
          {d.statuses.map((s) => <StatusChip key={s.id} s={s} />)}
        </div>
        {d.role.reason && <p className="hero-role">{d.role.reason}</p>}
        {d.location && <div className="hero-location"><MapPin size={12} /> {d.location}</div>}
        {!d.is_player && <GenerateButton cid={cid} d={d} onDone={onChanged} />}
      </div>
      <div className={`hero-vitals ${d.life.state === "deceased" ? "gone" : ""}`}>
        <PlotArmor pa={d.stats.plot_armor} />
      </div>
      <Dialog.Close className="btn ghost small dossier-close" aria-label="Close dossier"><X size={16} /></Dialog.Close>
    </header>
  );
}

/** Flesh out an NPC or companion: look, manner and known background on this page; their private backstory, motives and
 * secrets go to the GM only (the GM plays them from it, you discover it in play). */
function GenerateButton({ cid, d, onDone }: { cid: number; d: Dossier; onDone: () => void }) {
  const [state, setState] = useState<string>("idle");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.generateStatus(cid, d.id).then((s) => setState(s.state === "running" ? "running" : "idle")).catch(() => {}); }, [cid, d.id]);
  useEffect(() => {
    if (state !== "running") return;
    const iv = setInterval(async () => {
      const s = await api.generateStatus(cid, d.id).catch(() => null);
      if (!s || s.state === "running") return;
      setState("idle");
      if (s.state === "failed") setErr(s.error ?? "Generation failed.");
      else onDone();
    }, 2500);
    return () => clearInterval(iv);
  }, [state, cid, d.id, onDone]);
  if (d.private_sheet === "authored") return null;  // a hand-written private sheet is never replaced
  const again = d.private_sheet === "generated";
  const go = async () => {
    if (again && !window.confirm(`Regenerate ${d.name}? What's already on this page stays; the GM's private notes on them are rewritten.`)) return;
    setErr(null);
    try { const s = await api.generateCharacter(cid, d.id); setState(s.state); } catch (e) { setErr(String(e instanceof Error ? e.message : e)); }
  };
  return (
    <div className="generate-row">
      <button className="btn small" disabled={state === "running"} onClick={go}
        title="Write this person in full: look, personality and the background you'd know here; private backstory, motives and secrets for the GM only">
        {state === "running" ? <Loader2 size={12} className="spin" /> : <UserRoundPen size={12} />} {state === "running" ? "Writing…" : again ? "Regenerate" : "Generate"}
      </button>
      {state === "running" && <span className="sub">about a minute; you can keep playing</span>}
      {err && <span className="sub" style={{ color: "var(--red)" }}>{err}</span>}
    </div>
  );
}

/** Plot Armor is the sum of the five stats: base, after buffs (current), and after drawbacks such as Trope Master (effective). */
function PlotArmor({ pa }: { pa: CarouselStats["plot_armor"] }) {
  return (
    <div className="vital favor" title="Plot Armor: base / current (after buffs) / effective (after drawbacks)">
      <div className="vital-head"><ShieldCheck size={16} /> Plot Armor</div>
      <div className="vital-num big">{pa.effective ?? "?"}</div>
      <div className="sub">base {pa.base ?? "?"} · current {pa.current ?? "?"}</div>
    </div>
  );
}

function StatusChip({ s }: { s: { name: string; polarity: string | null; description: string | null } }) {
  return <Chip tone={s.polarity === "buff" ? "green" : s.polarity === "debuff" ? "red" : "amber"} title={s.description ?? humanize(s.polarity ?? "condition")}>{s.name}</Chip>;
}

// ------------------------------------------------------------------ overview
const STATS = ["Mettle", "Moxie", "Hustle", "Savvy", "Grit"] as const;

/** Reward stars (every five make a stat point) and spending a pending stat point on a base stat. */
function Rewards({ cid, d, onChanged }: { cid: number; d: Dossier; onChanged: () => void }) {
  const [stat, setStat] = useState<string>("mettle");
  const [err, setErr] = useState<string | null>(null);
  const r = d.rewards;
  if (!r || d.stats.stats.Mettle == null) return null;
  const spend = async () => { setErr(null); try { await api.spendStatPoint(cid, d.id, stat); onChanged(); } catch (e) { setErr(String((e as Error).message ?? e)); } };
  return (
    <div className="rewards">
      <span className="sub">Stars {r.stars}/5</span>
      {r.pending_stat_points > 0 && (
        <span className="row">
          <Chip tone="amber">{r.pending_stat_points} stat point{r.pending_stat_points > 1 ? "s" : ""}</Chip>
          <select className="select" value={stat} onChange={(e) => setStat(e.target.value)} aria-label="Stat to raise">
            {STATS.map((k) => <option key={k} value={k.toLowerCase()}>{k}</option>)}
          </select>
          <button className="btn small" onClick={spend}>Spend stat point</button>
        </span>
      )}
      {err && <div className="error-box">{err}</div>}
    </div>
  );
}

function Overview({ cid, d, onChanged }: { cid: number; d: Dossier; onChanged: () => void }) {
  const st = d.stats;
  const archetype = [roleName(d.role.id), roleName(d.role.aspect_id), roleName(d.role.advanced_role_id)].filter(Boolean).join(" · ");
  return (
    <div className="ov-grid">
      <div className="ov-col">
        <section className="dpanel ov-attrs">
          <h3>Archetype</h3>
          <p className="ov-archetype">{archetype || "Carousel hasn't assigned an Archetype yet."}</p>
          <h3>Plot Armor and stats</h3>
          {st.stats.Mettle == null ? <Empty>No stats until Carousel assigns an Archetype.</Empty> : (
            <div className="attr-row">{STATS.map((k) => (
              <div key={k} className="attr" title={st.current[k] !== st.stats[k] ? `base ${st.stats[k]}` : k}>
                <div className="attr-abbr">{k.slice(0, 3).toUpperCase()}</div>
                <div className="attr-val">{st.current[k] ?? "?"}</div>
                <div className="attr-name">{k}</div>
                {st.current[k] !== st.stats[k] && <div className="attr-base">base {st.stats[k]}</div>}
              </div>))}
            </div>
          )}
          {st.modifiers.length > 0 && (
            <div className="chips">{st.modifiers.map((m) => <Chip key={m.id} tone={m.op === "multiply" && m.value < 1 ? "red" : "green"} title={`${m.scope}`}>
              {m.target} {m.op === "multiply" ? `×${m.value}` : `${m.value >= 0 ? "+" : ""}${m.value}`} · {m.source}</Chip>)}</div>
          )}
          <Rewards cid={cid} d={d} onChanged={onChanged} />
        </section>
        <section className="dpanel ov-combat">
          <h3>Conditions</h3>
          <div className="chips">{d.statuses.length ? d.statuses.map((s) => <StatusChip key={s.id} s={s} />) : <span className="sub">none</span>}</div>
        </section>
      </div>
      <div className="ov-col">
        <section className="dpanel ov-effects">
          <h3>Tickets</h3>
          {!d.tickets?.length ? <Empty>No tickets.</Empty> : (
            <div className="ov-tickets">{d.tickets.map((t) => <TicketCard key={t.ref} t={t} large />)}</div>
          )}
        </section>
      </div>
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
        {(s.tactics as string[]).length > 0 && <div className="build-tactics" style={{ marginTop: 10 }}>{(s.tactics as string[]).join(" ")}</div>}
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
      {shown.length === 0 ? <Empty>Nothing is recorded for this character yet. Stars, stat points, tickets and story moments from play will appear here.</Empty> : (
        <ol className="timeline">{shown.map((h, i) => {
          const Icon = History;
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
