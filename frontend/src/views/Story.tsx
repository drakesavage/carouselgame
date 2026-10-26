import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowDown, BookOpen, ChevronRight, Cog, Dices, Eye, Footprints, Hand, Hourglass, MessageSquare, RotateCcw, Send, Shield, ShieldCheck, Sparkles, Swords, Tent, Users } from "lucide-react";
import { api, ApiError, Campaign, CharacterCard, Message, newKey, RollCard, TurnStatus } from "../api";
import { Chip, Empty, Markdown, Portrait } from "../ui";
import { RulePageDialog } from "./Reference";
import { play } from "../prefs";
import { ReportButton } from "./Report";
import { CampaignSidebar, CombatPanel, Decision, DiscoveryCards, IMPORTANT_CARDS, portraitSrc, RosterPanel, SceneBanner, splitDecision, StoryText, useManifest } from "./Hud";

const QUIPS = [
  "Consulting the Showrunner…", "Calculating maximum humiliation…", "Negotiating with sponsors…", "Rolling actual dice. No, really.",
  "Reviewing your poor life choices…", "Buffering dramatic tension…", "Auditing your inventory for sins…", "Asking the NPCs how they feel…",
  "Checking the rulebook so you can't…", "Rendering consequences…",
];
const STAGES = ["loading", "gm_thinking", "resolving", "saving"];
const STAGE_LABEL: Record<string, string> = { loading: "load state", gm_thinking: "GM thinking", resolving: "tools & dice", saving: "persist" };

const RECAP_PROMPT = "Give me a \"Previously on Dungeon Crawler Carl\" recap from the campaign records to re-baseline us, then set the current scene with where everyone is and what decision is in front of me. (Recap/orientation only — not a substantive decision.)";
const QUICK = [
  { icon: Eye, label: "Look around", text: "I take a careful look around. " },
  { icon: MessageSquare, label: "Talk to…", text: "I talk to " },
  { icon: Users, label: "Party check-in", text: "I check in with the party — how is everyone doing? " },
  { icon: Tent, label: "Rest", text: "We rest here for " },
  { icon: BookOpen, label: "Rules question", text: "Rules question (no in-world action): " },
];
const COMBAT_QUICK = [
  { icon: Shield, label: "Evade", text: "I spend an Action to Evade. " },
  { icon: Swords, label: "Attack…", text: "I attack " },
  { icon: Hand, label: "Help / Intervene", text: "I spend an Action to help " },
  { icon: Footprints, label: "Move", text: "I move " },
  { icon: BookOpen, label: "Rules question", text: "Rules question (no in-world action): " },
];

type Pending = { key: string; text: string; turnId?: number; status: "sending" | "processing" | "failed"; progress?: TurnStatus["progress"]; error?: string; unreachable?: boolean };
// the request never reached the game: a proxy with nothing behind it (502/503/504) or no network at all
const unreachable = (e: unknown) => (e instanceof ApiError && [502, 503, 504].includes(e.status)) || e instanceof TypeError;

const PAGE = 80;  // the server's default page of messages

export function StoryView({ campaign, onState, onNotices, openCharacter, openQuest }: {
  campaign: Campaign; onState: () => void; onNotices: (n: { text: string; style: string }[]) => void; openCharacter: (id: number) => void;
  openQuest: (id?: number) => void;
}) {
  const cid = campaign.id;
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [draft, setDraft] = useState(() => { try { return localStorage.getItem(`draft:${cid}`) ?? ""; } catch { return ""; } });
  const [quip, setQuip] = useState(QUIPS[0]);
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pc = campaign.roster.find((p) => p.id === campaign.active_character_id) ?? campaign.party.find((p) => p.id === campaign.active_character_id);

  // Scroll continuity: follow the story only while the reader is at the bottom; a finished turn scrolls to the start of
  // the new exchange (long narration reads from its top); older pages load above without moving what is on screen.
  const known = useRef<Set<number> | null>(null);  // message ids present at first load: no entrance animation
  const atBottom = useRef(true);
  const anchor = useRef<{ kind: "bottom" } | { kind: "message"; id: number } | { kind: "keep"; height: number; top: number } | null>({ kind: "bottom" });
  const [unseen, setUnseen] = useState(false);
  const [more, setMore] = useState(false);

  const loadMessages = useCallback(async () => {
    const list = await api.messages(cid);
    known.current = new Set(list.map((x) => x.id));
    anchor.current = { kind: "bottom" };
    setMore(list.length >= PAGE);
    setMessages(list);
  }, [cid]);
  useEffect(() => { setMessages(null); loadMessages(); }, [loadMessages]);
  useEffect(() => { try { localStorage.setItem(`draft:${cid}`, draft); } catch { /* storage unavailable */ } }, [draft, cid]);
  useLayoutEffect(() => {
    const el = scroller.current, a = anchor.current;
    if (!el || !a) return;
    anchor.current = null;
    if (a.kind === "bottom") el.scrollTo({ top: el.scrollHeight, behavior: "instant" as ScrollBehavior });
    else if (a.kind === "keep") el.scrollTo({ top: el.scrollHeight - a.height + a.top, behavior: "instant" as ScrollBehavior });
    else el.querySelector(`[data-mid="${a.id}"]`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [messages, pending?.status]);
  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (atBottom.current) setUnseen(false);
  };
  const earlier = async () => {
    const el = scroller.current;
    if (!messages?.length || !el) return;
    const older = await api.messages(cid, messages[0].id);
    setMore(older.length >= PAGE);
    older.forEach((x) => known.current?.add(x.id));
    anchor.current = { kind: "keep", height: el.scrollHeight, top: el.scrollTop };
    setMessages([...older, ...messages]);
  };
  const toLatest = () => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); setUnseen(false); };

  // Resume a turn that was processing when the page was (re)loaded.
  useEffect(() => {
    api.activeTurn(cid).then((t) => { if (t) setPending({ key: "", text: "", turnId: t.turn_id, status: "processing", progress: t.progress }); }).catch(() => {});
  }, [cid]);

  useEffect(() => {
    if (pending?.status !== "processing") return;
    const q = setInterval(() => setQuip(QUIPS[Math.floor(Math.random() * QUIPS.length)]), 3500);
    return () => clearInterval(q);
  }, [pending?.status]);

  useEffect(() => {
    if (pending?.status !== "processing" || !pending.turnId) return;
    let alive = true;
    const tick = async () => {
      try {
        const t = await api.turn(pending.turnId!);
        if (!alive) return;
        if (t.status === "completed") finish(t);
        else if (t.status === "failed") setPending((p) => p && { ...p, status: "failed", error: t.error ?? "The turn failed." });
        else setPending((p) => p && { ...p, progress: t.progress });
      } catch { /* transient */ }
    };
    const iv = setInterval(tick, 1200);
    return () => { alive = false; clearInterval(iv); };
  }, [pending?.status, pending?.turnId]);

  const finish = async (t: TurnStatus) => {
    const fresh = await api.messages(cid);
    const first = fresh.find((x) => x.turn_id === t.turn_id && !known.current?.has(x.id));
    if (first && atBottom.current) anchor.current = { kind: "message", id: first.id };
    else if (first) setUnseen(true);
    setMore((m) => m || fresh.length >= PAGE);
    setMessages((old) => {
      // keep any older pages already loaded above the latest page
      const ids = new Set(fresh.map((x) => x.id));
      return [...(old ?? []).filter((x) => !ids.has(x.id) && x.id < (fresh[0]?.id ?? 0)), ...fresh];
    });
    setPending(null);
    onState();
    const gm = [...fresh].reverse().find((m) => m.role === "gm" && m.turn_id === t.turn_id);
    if (gm?.meta.rolls?.length) play("dice");
    const big = (gm?.meta.cards ?? []).filter((c) => IMPORTANT_CARDS.has(c.type))
      .map((c) => ({ text: c.summary, style: /down|death|collapse/.test(c.type) ? "warning" : "achievement" }));
    if (t.result?.notices?.length || big.length) onNotices([...(t.result?.notices ?? []), ...big]);
  };

  const send = async (text: string, key = newKey()) => {
    if (!text.trim() || (pending && pending.status !== "failed")) return;
    anchor.current = { kind: "bottom" };
    setPending({ key, text, status: "sending" });
    try {
      const t = await api.submitTurn(cid, text.trim(), key);
      if (t.status === "completed") { setDraft(""); await finish(t); }
      else if (t.status === "failed") setPending({ key, text, turnId: t.turn_id, status: "failed", error: t.error ?? "failed" });
      else { setDraft(""); setPending({ key, text, turnId: t.turn_id, status: "processing", progress: t.progress }); }
    } catch (e) {
      setPending({ key, text, status: "failed", error: e instanceof ApiError ? e.message : String(e), unreachable: unreachable(e) });
    }
  };

  const busy = !!pending && pending.status !== "failed";
  const lastGm = messages ? [...messages].reverse().find((x) => x.role === "gm")?.id : undefined;
  return (
    <div className="story-layout">
      <aside className="side left">
        <RosterPanel campaign={campaign} openCharacter={openCharacter} />
        {campaign.encounter && <div className="combat-left"><CombatPanel enc={campaign.encounter} campaign={campaign} onArt={onState} /></div>}
      </aside>
      <div className="story-col">
        <SceneBanner campaign={campaign} onArt={onState} openCharacter={openCharacter} />
        <div className="combat-top">{campaign.encounter && <CombatPanel enc={campaign.encounter} campaign={campaign} onArt={onState} />}</div>
        <div className="transcript" ref={scroller} onScroll={onScroll} role="log" aria-label="Story transcript">
          <div className="transcript-inner">
            {more && <button className="btn small ghost earlier" onClick={earlier}>Load earlier messages</button>}
            {messages === null ? <div className="muted">Loading transcript…</div> : messages.length === 0 && !pending ? (
              <Empty>
                <div className="h2" style={{ marginTop: 0 }}>No play recorded in this console yet</div>
                <p>Start with a recap of where the campaign left off, or just say what {pc?.name.split(" ")[0] ?? "you"} does.</p>
                <button className="btn primary" onClick={() => send(RECAP_PROMPT)}><Sparkles size={15} /> Previously on…</button>
              </Empty>
            ) : messages.map((m) => <MessageView key={m.id} cid={cid} m={m} pc={pc} roster={campaign.roster} fresh={!known.current?.has(m.id)} latest={m.id === lastGm && !pending} />)}
            {pending && (
              <>
                {pending.text && <PlayerLine pc={pc} text={pending.text} />}
                {pending.status === "failed" ? (
                  <div className="error-box">
                    <div className="row"><AlertTriangle size={16} color="var(--red)" />
                      <b>{pending.unreachable ? "The game server didn't answer." : "The GM couldn't finish that turn."}</b></div>
                    <div className="mono" style={{ margin: "6px 0 10px", whiteSpace: "pre-wrap", fontSize: 12 }}>{pending.error}</div>
                    <div className="sub" style={{ marginBottom: 8 }}>{pending.unreachable
                      ? "Your turn never reached the game, so nothing happened. Check that the server is running on the game PC (start.cmd), then Retry."
                      : "Nothing was applied to the game state. Dice already rolled stay in the audit log."}</div>
                    <div className="row">
                      {pending.text && <button className="btn" onClick={() => send(pending.text, pending.key)}><RotateCcw size={14} /> Retry</button>}
                      <button className="btn ghost" onClick={() => { setDraft(pending.text); setPending(null); textarea.current?.focus(); }}>Edit &amp; resend</button>
                      <ReportButton cid={cid} turnId={pending.turnId ?? null} compact={false} />
                    </div>
                  </div>
                ) : (
                  <div className="working">
                    <div className="row"><Hourglass size={15} color="var(--cyan)" /><span className="quip">{quip}</span><span className="spacer" />
                      <span className="mono muted">{pending.progress ? `${Math.round(pending.progress.elapsed_s)}s` : ""}</span></div>
                    <div className="stages">
                      {STAGES.map((s) => {
                        const idx = STAGES.indexOf(pending.progress?.stage ?? "loading");
                        const me = STAGES.indexOf(s);
                        return <span key={s} className={`stage ${me === idx ? "on" : me < idx ? "done" : ""}`}>{STAGE_LABEL[s]}</span>;
                      })}
                      {pending.progress?.detail && <span className="stage">{pending.progress.detail}</span>}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
        {unseen && <button className="btn small to-latest" onClick={toLatest}><ArrowDown size={13} /> New narration</button>}
        <div className="composer">
          <textarea ref={textarea} value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy}
            placeholder={`What does ${pc?.name.split(" ")[0] ?? "your crawler"} do? Describe actions, dialogue, plans — anything.`}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(draft); } }} />
          <div className="row wrap">
            <div className="quick">
              <button className="btn small" disabled={busy} onClick={() => send(RECAP_PROMPT)} title="Ask the GM for a recap and scene-setting"><Sparkles size={13} /> Previously on…</button>
              {(campaign.encounter ? COMBAT_QUICK : QUICK).map((q) => (
                <button key={q.label} className="btn small ghost" disabled={busy} onClick={() => { setDraft((d) => (d ? d + " " : "") + q.text); textarea.current?.focus(); }}>
                  <q.icon size={13} /> {q.label}
                </button>
              ))}
            </div>
            <span className="spacer" />
            <span className="kbd">Ctrl+Enter</span>
            <button className="btn primary" disabled={busy || !draft.trim()} onClick={() => send(draft)}><Send size={14} /> Act</button>
          </div>
        </div>
      </div>
      <aside className="side right" aria-label="Floor and objective"><CampaignSidebar campaign={campaign} onArt={onState} openQuest={openQuest} openCharacter={openCharacter} /></aside>
    </div>
  );
}

function PlayerLine({ pc, text }: { pc?: CharacterCard; text: string }) {
  const m = useManifest();
  const name = pc?.name ?? "You";
  return (
    <div className="msg-player">
      <div className="bubble"><span className="msg-label">{name.split(" ")[0]}</span>{text}</div>
      <Portrait name={name} src={portraitSrc(pc, m)} crop={pc?.portrait_crop} size={34} />
    </div>
  );
}

function MessageView({ cid, m, pc, roster, fresh, latest }: { cid: number; m: Message; pc?: CharacterCard; roster: CharacterCard[]; fresh: boolean; latest: boolean }) {
  if (m.role === "player") return <div data-mid={m.id}><PlayerLine pc={pc} text={m.content} /></div>;
  if (m.role === "system") return <div data-mid={m.id} className={fresh ? "fresh" : ""}><SystemNotice text={m.content} style={m.meta?.style} /></div>;
  const meta = m.meta ?? {};
  const [body, ask] = latest ? splitDecision(m.content) : [m.content, null];
  return (
    <div className={`msg-gm ${fresh ? "fresh" : ""}`} data-mid={m.id}>
      {meta.provider === "mock" && <div className="msg-label"><Chip tone="amber">mock GM</Chip></div>}
      <StoryText text={body} roster={roster} />
      <DiscoveryCards cards={meta.cards ?? []} />
      <Mechanics meta={meta} open={fresh && !!meta.rolls?.length} />
      <div className="msg-tools"><ReportButton cid={cid} turnId={m.turn_id} /></div>
      {ask && <Decision text={ask} />}
    </div>
  );
}

/** Mechanics, separate from the story: roll results at a glance in the summary, the workings one click away. */
function Mechanics({ meta, open }: { meta: Message["meta"]; open: boolean }) {
  const rolls = meta.rolls ?? [], changes = meta.changes ?? [], rules = meta.rules_cited ?? [];
  if (!rolls.length && !changes.length && !rules.length) return null;
  const count = [changes.length && `${changes.length} change${changes.length > 1 ? "s" : ""}`, rules.length && `${rules.length} rule${rules.length > 1 ? "s" : ""}`].filter(Boolean).join(" · ");
  return (
    <details className="mechanics" open={open}>
      <summary>
        <ChevronRight size={12} className="caret" /><Cog size={11} /> <span className="mechanics-title">Mechanics</span>
        {rolls.map((r) => <RollPill key={r.roll_id} r={r} />)}
        {count && <span className="mech-count">{count}</span>}
        <span className="spacer" />
        {meta.floor_clock?.remaining_display && <span className="clock-stamp" title="Floor clock after this turn">{meta.floor_clock.remaining_display}</span>}
      </summary>
      {!!rolls.length && <div className="rolls">{rolls.map((r) => <RollView key={r.roll_id} r={r} />)}</div>}
      {!!changes.length && (
        <div className="ledger">
          <div className="mechanics-sub">State changes</div>
          <ul>{changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      {!!rules.length && (
        <div className="ledger">
          <div className="mechanics-sub">Rules consulted</div>
          <div className="chips">{rules.map((r) => <CitedRule key={r} cite={r} />)}</div>
        </div>
      )}
    </details>
  );
}

/** A rule the GM consulted this turn: opens that rulebook page in the local page viewer. */
function CitedRule({ cite }: { cite: string }) {
  const [page, setPage] = useState<{ doc: number; page: number } | null>(null);
  const [missing, setMissing] = useState(false);
  const open = async () => {
    try { const c = await api.ruleCite(cite); setPage({ doc: c.document_id, page: c.page }); } catch { setMissing(true); }
  };
  return (
    <>
      <button className={`chip violet cite-btn`} onClick={open} disabled={missing} title={missing ? "This page isn't in the rulebook index" : "Open this rulebook page"}>
        <BookOpen size={11} /> {cite}
      </button>
      <RulePageDialog page={page} onClose={() => setPage(null)} />
    </>
  );
}

const CHECK_KINDS = new Set(["skill_check", "stat_check", "attack", "evade"]);
const natural = (r: RollCard) => (CHECK_KINDS.has(r.detail?.kind) ? (r.detail?.natural === 20 ? "nat20" : r.detail?.natural === 1 ? "nat1" : "") : "");

function RollPill({ r }: { r: RollCard }) {
  const d = r.detail ?? {};
  const who = d.character ? String(d.character).split(" ")[0] + " " : "";
  const what = d.skill ?? d.stat ?? r.reason ?? "roll";
  return (
    <span className={`roll-pill ${r.outcome ?? ""} ${natural(r)}`} title={d.summary ?? `${who}${what}: ${r.total}`}>
      <b>{r.total}</b> {who}{String(what).slice(0, 22)}{r.degree || r.outcome ? <i> · {r.degree ?? r.outcome}</i> : null}
    </span>
  );
}

export function SystemNotice({ text, style }: { text: string; style?: string }) {
  const head = style === "achievement" ? "NEW ACHIEVEMENT" : style === "warning" ? "SYSTEM WARNING" : style === "tooltip" ? "SYSTEM TOOLTIP" : "SYSTEM NOTIFICATION";
  return (
    <div className={`sys ${style ?? ""}`}>
      <div className="sys-head glitch"><ShieldCheck size={14} />{head}</div>
      <Markdown text={text} />
    </div>
  );
}

function RollView({ r }: { r: RollCard }) {
  const d = r.detail ?? {};
  const nat = natural(r);
  const cls = `${nat ? "crit " + nat : ""} ${r.outcome ?? ""}`;
  const line = d.summary ?? `${d.character ? d.character + " — " : ""}${r.reason ?? "Roll"}: ${r.expression} = ${r.total}${r.target != null ? ` vs ${r.target}` : ""}`;
  return (
    <details className={`roll ${cls}`}>
      <summary>
        <span className="d20">{d.natural ?? r.total}</span>
        <span className="roll-line">{line}</span>
        {(r.degree || r.outcome) && <span className="degree">{r.degree ?? r.outcome}</span>}
        <Dices size={14} color="var(--muted)" />
      </summary>
      <div className="roll-detail">
        <div className="dice">{r.dice.map((x, i) => <span key={i} className={`die ${x.kept ? "" : "dropped"}`} title={`d${x.sides}`}>{x.value}<span className="muted">/d{x.sides}</span></span>)}</div>
        {d.kind === "skill_check" || d.kind === "stat_check" ? (
          <div>
            {d.skill ? `${d.skill} Rank ${d.rank}${d.untrained ? " (untrained → Disadvantage)" : ""}` : "Stat Check"} · {String(d.stat).slice(0, 3).toUpperCase()} {d.stat_mod >= 0 ? "+" : ""}{d.stat_mod}
            {d.advantage !== "none" ? ` · ${d.advantage}` : ""}{d.modifiers?.map((m: any) => ` · ${m.source} ${m.value >= 0 ? "+" : ""}${m.value}`)}
            {d.help?.map((h: any) => ` · ${h.helper} Help ${h.value} (${h.dice})`)} · DC {d.dc}
          </div>
        ) : null}
        <div>Expression {r.expression} · total {r.total}{r.target != null ? ` · target ${r.target}` : ""} · roll #{r.roll_id} · OS CSPRNG</div>
      </div>
    </details>
  );
}
