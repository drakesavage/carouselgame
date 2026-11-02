import { ReactNode, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Flag, Handshake, MapPin, MessageCircle, Search, Skull } from "lucide-react";
import { api, fmtMinutes, Life, Person } from "../api";
import { Chip, Empty, Portrait } from "../ui";
import { portraitSrc, role, useManifest } from "./Hud";
import { PosterBoard } from "./PosterBoard";
import { LodgeRoster } from "./AroundPanel";
import "../people.css";

/** Life state as a chip; nothing for the ordinary active case. */
export function LifeChip({ life }: { life?: Life | null }) {
  if (!life || life.state === "active" && /^active\b/i.test(life.label)) return null;  // "Active manager": the role tag says the rest
  const tone = life.state === "deceased" ? "red" : life.state === "missing" ? "amber" : life.state === "contact" ? "violet" : "";
  return <Chip tone={tone} title={life.note ?? undefined}>{life.state === "deceased" && <Skull size={11} />} {life.label}</Chip>;
}

const FILTERS = [
  { id: "all", label: "Everyone" },
  { id: "here", label: "With you" },
  { id: "away", label: "Offscreen" },
  { id: "contact", label: "Contacts" },
  { id: "gone", label: "Fallen & missing" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];

const firstSentence = (s?: string | null) => (s ? (s.match(/^.*?[.!?](\s|$)/)?.[0] ?? s).trim() : "");

/**
 * People as the party knows them: who they are to the party, where they are, what was promised, and the last thing seen
 * of them. Compact cards that expand; the server sends only player-visible records.
 */
export function PeopleView({ cid, version, pcAssignment, pcName, openCharacter, openQuest }: {
  cid: number; version: number; pcAssignment?: string | null; pcName?: string; openCharacter: (id: number) => void; openQuest: (id?: number) => void;
}) {
  const [data, setData] = useState<Person[] | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => { setData(null); api.npcs(cid).then(setData).catch(() => setData([])); }, [cid, version]);
  const shown = useMemo(() => (data ?? []).filter((p) => {
    const gone = p.life.state === "deceased" || p.life.state === "missing" || p.life.state === "retired";
    if (filter === "here" && (gone || !pcAssignment || p.assignment !== pcAssignment)) return false;
    if (filter === "away" && (gone || !p.assignment || p.assignment === pcAssignment)) return false;
    if (filter === "contact" && p.life.state !== "contact") return false;
    if (filter === "gone" && !gone) return false;
    if (!q.trim()) return true;
    const hay = [p.name, p.allegiance, p.where, p.personality, p.known_motivation, ...p.relationship_notes].join(" ").toLowerCase();
    return hay.includes(q.trim().toLowerCase());
  }), [data, q, filter, pcAssignment]);
  if (!data) return <div className="muted">Loading…</div>;
  const sections: [string, Person[]][] = [
    ["Party & allies", shown.filter((p) => p.life.state === "active")],
    ["Contacts", shown.filter((p) => p.life.state === "contact")],
    ["Fallen, missing & retired", shown.filter((p) => p.life.state === "deceased" || p.life.state === "missing" || p.life.state === "retired")],
  ];
  return (
    <div className="view-inner people">
      <div className="h1">People</div>
      <div className="sub">Allies, companions and contacts: what the party has seen and been told of them.</div>
      <div className="people-tools">
        <label className="people-search"><Search size={14} aria-hidden /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search people, places, notes" aria-label="Search people" /></label>
        <div className="seg" role="group" aria-label="Show">
          {FILTERS.map((f) => <button key={f.id} className={filter === f.id ? "active" : ""} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>{f.label}</button>)}
        </div>
      </div>
      <PosterBoard cid={cid} version={version} />
      <LodgeRoster cid={cid} version={version} />
      {shown.length === 0 && <Empty>No one matches.</Empty>}
      {sections.filter(([, list]) => list.length).map(([title, list]) => (
        <section key={title}>
          <div className="h2">{title} <span className="muted">{list.length}</span></div>
          <div className="people-grid">{list.map((p) => <PersonCard key={p.id} p={p} here={!!pcAssignment && p.assignment === pcAssignment} pcName={pcName?.split(" ")[0]} openCharacter={openCharacter} openQuest={openQuest} />)}</div>
        </section>
      ))}
    </div>
  );
}

function PersonCard({ p, here, pcName, openCharacter, openQuest }: { p: Person; here: boolean; pcName?: string; openCharacter: (id: number) => void; openQuest: (id?: number) => void }) {
  const m = useManifest();
  const [open, setOpen] = useState(false);
  const [label, tone] = role(p);
  const gone = p.life.state === "deceased";
  const summary = firstSentence(p.personality) || firstSentence(p.known_motivation);
  return (
    <article className={`person ${open ? "open" : ""} ${gone ? "gone" : ""}`}>
      <button className="person-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Portrait name={p.name} src={portraitSrc(p, m)} crop={p.portrait_crop} size={56} />
        <div className="person-main">
          <div className="person-name">{p.name}<span className={`role ${tone}`}>{label}</span></div>
          <div className="chips">
            <LifeChip life={p.life} />
            {p.identity?.pronouns && <Chip title={[p.identity.gender, p.identity.apparent_age].filter(Boolean).join(", ") || undefined}>{p.identity.pronouns}</Chip>}
            {p.allegiance && <Chip>{p.allegiance}</Chip>}
            {here && !gone && <Chip tone="cyan">with {pcName ?? "you"}</Chip>}
          </div>
          {!gone && (
            <div className="person-where"><MapPin size={11} aria-hidden />
              {p.group ? <>{p.group.label}{p.where ? ` · ${p.where}` : <span className="muted"> · position not recorded</span>}</>
                : p.where ?? <span className="muted">whereabouts not recorded</span>}
            </div>
          )}
          {gone && p.location && <div className="person-where"><Skull size={11} aria-hidden /> {p.location}</div>}
          {summary && !open && <div className="person-summary">{summary}</div>}
        </div>
        <span className="person-side">
          {p.attitude != null && <Attitude v={p.attitude} />}
          {p.commitments.length > 0 && <span className="person-count" title="Open promises and commitments"><Handshake size={12} /> {p.commitments.length}</span>}
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open && (
        <div className="person-body">
          {p.personality && <Field label="Personality">{p.personality}</Field>}
          {p.known_motivation && <Field label="Known motivation">{p.known_motivation}</Field>}
          {p.voice && <Field label="Voice">{p.voice}</Field>}
          {p.relationship_notes.length > 0 && (
            <Field label="What the party knows"><ul>{p.relationship_notes.map((n, i) => <li key={i}>{n}</li>)}</ul></Field>
          )}
          {p.group?.activity && !gone && <Field label="Current orders">{p.group.activity.replace(/\s*\(checkpoints\/latest\.md\)$/, "")}</Field>}
          <Field label="Promises & commitments">
            {p.commitments.length ? p.commitments.map((c) => (
              <button key={c.id} className="person-link" onClick={() => openQuest(c.id)}><Flag size={11} /> {c.title} <span className="muted">({c.kind})</span></button>
            )) : <span className="muted">None recorded.</span>}
          </Field>
          <Field label="Last interaction">
            {p.last_interaction ? <><MessageCircle size={11} /> {p.last_interaction.text}
              {p.last_interaction.game_time_minutes != null && <span className="muted mono"> · T+{fmtMinutes(p.last_interaction.game_time_minutes)}</span>}</>
              : <span className="muted">Nothing recorded in the console yet.</span>}
          </Field>
          {p.life.note && <div className="sub">{p.life.note}</div>}
          <button className="btn small" onClick={() => openCharacter(p.id)}>Open dossier</button>
        </div>
      )}
    </article>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="person-field"><div className="person-label">{label}</div><div>{children}</div></div>;
}

function Attitude({ v }: { v: number }) {
  const word = v <= -2 ? "hostile" : v < 0 ? "wary" : v === 0 ? "neutral" : v < 2 ? "friendly" : "devoted";
  return (
    <span className="attitude" title={`Attitude ${v > 0 ? "+" : ""}${v} (${word})`} aria-label={`Attitude ${word}`}>
      {[-3, -2, -1, 0, 1, 2, 3].map((x) => <i key={x} className={x === v ? (v < 0 ? "neg" : v > 0 ? "pos" : "zero") : ""} />)}
    </span>
  );
}
