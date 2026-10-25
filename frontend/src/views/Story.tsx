import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, BookOpen, Cog, Dices, Eye, Footprints, Hand, Hourglass, MessageSquare, RotateCcw, Send, Shield, ShieldCheck, Sparkles, Swords, Tent, Users } from "lucide-react";
import { api, ApiError, Campaign, CharacterCard, Message, newKey, RollCard, TurnStatus } from "../api";
import { Chip, Empty, Markdown } from "../ui";
import { CampaignSidebar, CombatPanel, DiscoveryCards, IMPORTANT_CARDS, RosterPanel, SceneBanner, StoryText } from "./Hud";

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

type Pending = { key: string; text: string; turnId?: number; status: "sending" | "processing" | "failed"; progress?: TurnStatus["progress"]; error?: string };

export function StoryView({ campaign, onState, onNotices, openCharacter }: {
  campaign: Campaign; onState: () => void; onNotices: (n: { text: string; style: string }[]) => void; openCharacter: (id: number) => void;
}) {
  const cid = campaign.id;
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [draft, setDraft] = useState(() => { try { return localStorage.getItem(`draft:${cid}`) ?? ""; } catch { return ""; } });
  const [quip, setQuip] = useState(QUIPS[0]);
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pc = campaign.roster.find((p) => p.id === campaign.active_character_id) ?? campaign.party.find((p) => p.id === campaign.active_character_id);

  const loadMessages = useCallback(async () => setMessages(await api.messages(cid)), [cid]);
  useEffect(() => { setMessages(null); loadMessages(); }, [loadMessages]);
  useEffect(() => { try { localStorage.setItem(`draft:${cid}`, draft); } catch { /* storage unavailable */ } }, [draft, cid]);
  useLayoutEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [messages, pending?.status]);

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
    setMessages(fresh);
    setPending(null);
    onState();
    const gm = [...fresh].reverse().find((m) => m.role === "gm" && m.turn_id === t.turn_id);
    const big = (gm?.meta.cards ?? []).filter((c) => IMPORTANT_CARDS.has(c.type))
      .map((c) => ({ text: c.summary, style: /down|death|collapse/.test(c.type) ? "warning" : "achievement" }));
    if (t.result?.notices?.length || big.length) onNotices([...(t.result?.notices ?? []), ...big]);
  };

  const send = async (text: string, key = newKey()) => {
    if (!text.trim() || (pending && pending.status !== "failed")) return;
    setPending({ key, text, status: "sending" });
    try {
      const t = await api.submitTurn(cid, text.trim(), key);
      if (t.status === "completed") { setDraft(""); await finish(t); }
      else if (t.status === "failed") setPending({ key, text, turnId: t.turn_id, status: "failed", error: t.error ?? "failed" });
      else { setDraft(""); setPending({ key, text, turnId: t.turn_id, status: "processing", progress: t.progress }); }
    } catch (e) {
      setPending({ key, text, status: "failed", error: e instanceof ApiError ? e.message : String(e) });
    }
  };

  const busy = !!pending && pending.status !== "failed";
  return (
    <div className="story-layout">
      <aside className="side left">
        <RosterPanel campaign={campaign} openCharacter={openCharacter} />
        {campaign.encounter && <div className="combat-left"><CombatPanel enc={campaign.encounter} campaign={campaign} onArt={onState} /></div>}
      </aside>
      <div className="story-col">
        <div className="combat-top">{campaign.encounter && <CombatPanel enc={campaign.encounter} campaign={campaign} onArt={onState} />}</div>
        <div className="transcript" ref={scroller}>
          <SceneBanner campaign={campaign} onArt={onState} />
          <div className="transcript-inner">
            {messages === null ? <div className="muted">Loading transcript…</div> : messages.length === 0 && !pending ? (
              <Empty>
                <div className="h2" style={{ marginTop: 0 }}>No play recorded in this console yet</div>
                <p>Start with a recap of where the campaign left off, or just say what {pc?.name.split(" ")[0] ?? "you"} does.</p>
                <button className="btn primary" onClick={() => send(RECAP_PROMPT)}><Sparkles size={15} /> Previously on…</button>
              </Empty>
            ) : messages.map((m) => <MessageView key={m.id} m={m} pcName={pc?.name} roster={campaign.roster} />)}
            {pending && (
              <>
                {pending.text && <div className="msg-player"><div className="msg-label">{pc?.name ?? "You"}</div><div className="bubble">{pending.text}</div></div>}
                {pending.status === "failed" ? (
                  <div className="error-box">
                    <div className="row"><AlertTriangle size={16} color="var(--red)" /><b>The GM couldn't finish that turn.</b></div>
                    <div className="mono" style={{ margin: "6px 0 10px", whiteSpace: "pre-wrap", fontSize: 12 }}>{pending.error}</div>
                    <div className="sub" style={{ marginBottom: 8 }}>Nothing was applied to the game state. Dice already rolled stay in the audit log.</div>
                    <div className="row">
                      {pending.text && <button className="btn" onClick={() => send(pending.text, pending.key)}><RotateCcw size={14} /> Retry</button>}
                      <button className="btn ghost" onClick={() => { setDraft(pending.text); setPending(null); textarea.current?.focus(); }}>Edit &amp; resend</button>
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
      <aside className="side right"><CampaignSidebar campaign={campaign} onArt={onState} /></aside>
    </div>
  );
}

function MessageView({ m, pcName, roster }: { m: Message; pcName?: string; roster: CharacterCard[] }) {
  if (m.role === "player") {
    return <div className="msg-player"><div className="msg-label">{pcName ?? "Player"}</div><div className="bubble">{m.content}</div></div>;
  }
  if (m.role === "system") return <SystemNotice text={m.content} style={m.meta?.style} />;
  const meta = m.meta ?? {};
  return (
    <div className="msg-gm">
      <div className="msg-label">Game Master {meta.provider === "mock" && <Chip tone="amber">mock</Chip>}</div>
      <StoryText text={m.content} roster={roster} />
      <DiscoveryCards cards={meta.cards ?? []} />
      {(!!meta.rolls?.length || !!meta.changes?.length || !!meta.rules_cited?.length) && (
        <div className="mechanics">
          <div className="mechanics-title"><Cog size={11} /> Mechanics</div>
          {!!meta.rolls?.length && <div className="rolls">{meta.rolls.map((r) => <RollView key={r.roll_id} r={r} />)}</div>}
          {!!meta.changes?.length && (
            <details className="ledger">
              <summary>State changes ({meta.changes.length})</summary>
              <ul>{meta.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </details>
          )}
          {!!meta.rules_cited?.length && <div className="chips" style={{ marginTop: 6 }}>{meta.rules_cited.map((r) => <Chip key={r} tone="violet" title="Rulebook passage retrieved this turn"><BookOpen size={11} /> {r}</Chip>)}</div>}
        </div>
      )}
      <div className="msg-footer">
        {meta.floor_clock?.remaining_minutes != null && <span className="clock-stamp">Floor clock: <b>{meta.floor_clock.remaining_display}</b></span>}
      </div>
    </div>
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
  const nat = d.natural === 20 && (d.kind === "skill_check" || d.kind === "stat_check" || d.kind === "attack" || d.kind === "evade") ? "nat20"
    : d.natural === 1 && (d.kind === "skill_check" || d.kind === "stat_check" || d.kind === "attack" || d.kind === "evade") ? "nat1" : "";
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
