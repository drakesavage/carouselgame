import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, BookOpen, Dices, Eye, Footprints, Hand, Hourglass, MessageSquare, RotateCcw, Send, Shield, ShieldCheck, Sparkles, Swords, Tent, Users } from "lucide-react";
import { api, ApiError, Campaign, Encounter, fmtMinutes, Message, newKey, RollCard, TurnStatus } from "../api";
import { Bar, Chip, Empty, Markdown, Panel, Portrait } from "../ui";

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
  const pc = campaign.party.find((p) => p.id === campaign.active_character_id);

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
    await loadMessages();
    setPending(null);
    onState();
    if (t.result?.notices?.length) onNotices(t.result.notices);
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
        <PartyPanel campaign={campaign} openCharacter={openCharacter} />
        {campaign.encounter && <div className="combat-left"><CombatPanel enc={campaign.encounter} /></div>}
      </aside>
      <div className="story-col">
        <div className="combat-top">{campaign.encounter && <CombatPanel enc={campaign.encounter} />}</div>
        <div className="transcript" ref={scroller}>
          <div className="transcript-inner">
            {messages === null ? <div className="muted">Loading transcript…</div> : messages.length === 0 && !pending ? (
              <Empty>
                <div className="h2" style={{ marginTop: 0 }}>No play recorded in this console yet</div>
                <p>Start with a recap of where the campaign left off, or just say what {pc?.name.split(" ")[0] ?? "you"} does.</p>
                <button className="btn primary" onClick={() => send(RECAP_PROMPT)}><Sparkles size={15} /> Previously on…</button>
              </Empty>
            ) : messages.map((m) => <MessageView key={m.id} m={m} pcName={pc?.name} />)}
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
      <aside className="side right"><CampaignPanel campaign={campaign} /></aside>
    </div>
  );
}

function MessageView({ m, pcName }: { m: Message; pcName?: string }) {
  if (m.role === "player") {
    return <div className="msg-player"><div className="msg-label">{pcName ?? "Player"}</div><div className="bubble">{m.content}</div></div>;
  }
  if (m.role === "system") return <SystemNotice text={m.content} style={m.meta?.style} />;
  const meta = m.meta ?? {};
  return (
    <div className="msg-gm">
      <div className="msg-label">Game Master {meta.provider === "mock" && <Chip tone="amber">mock</Chip>}</div>
      <Markdown text={m.content} />
      {!!meta.rolls?.length && <div className="rolls">{meta.rolls.map((r) => <RollView key={r.roll_id} r={r} />)}</div>}
      {!!meta.changes?.length && (
        <details className="ledger">
          <summary>Ledger updates ({meta.changes.length})</summary>
          <ul>{meta.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </details>
      )}
      <div className="msg-footer">
        {meta.floor_clock?.remaining_minutes != null && <span className="clock-stamp">Floor clock: <b>{meta.floor_clock.remaining_display}</b></span>}
        {meta.rules_cited?.map((r) => <Chip key={r} tone="violet" title="Rulebook passage consulted"><BookOpen size={11} /> {r}</Chip>)}
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
  const crit = d.natural === 20 || d.natural === 1;
  const cls = crit ? "crit" : r.outcome ?? "";
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

function PartyPanel({ campaign, openCharacter }: { campaign: Campaign; openCharacter: (id: number) => void }) {
  return (
    <Panel title="Party" accent={campaign.party.length || ""}>
      {campaign.party.length === 0 ? <div className="muted">No active party recorded.</div> : (
        <div style={{ display: "grid", gap: 2, margin: -6 }}>
          {campaign.party.map((p) => (
            <button key={p.id} className={`party-card ${p.id === campaign.active_character_id ? "pc" : ""}`} onClick={() => openCharacter(p.id)}>
              <Portrait name={p.name} path={p.portrait_path} />
              <div style={{ minWidth: 0 }}>
                <div className="row"><span className="pname grow">{p.name}</span>{p.level != null && <span className="mono muted" style={{ fontSize: 11 }}>L{p.level}</span>}</div>
                <div className="pclass">{[p.race ?? p.species, p.class_name].filter(Boolean).join(" · ") || p.kind}</div>
                <Bar kind="hp" cur={p.hp_current} max={p.hp_max} />
                <Bar kind="mana" cur={p.mana_current} max={p.mana_max} />
                {p.statuses.length > 0 && <div className="chips" style={{ marginTop: 5 }}>{p.statuses.map((s) => <Chip key={s.id} tone={s.polarity === "buff" ? "green" : s.polarity === "debuff" ? "red" : ""} title={s.description ?? ""}>{s.name}</Chip>)}</div>}
              </div>
            </button>
          ))}
        </div>
      )}
    </Panel>
  );
}

const PHASES: Record<string, string> = { surprise: "Surprise", mob_declaration: "Mobs declaring", crawler_reaction: "Your reaction — Interrupts?",
  crawler_action: "Crawler actions", ended: "Ended" };

function CombatPanel({ enc }: { enc: Encounter }) {
  const party = enc.combatants.filter((c) => c.side === "party");
  const foes = enc.combatants.filter((c) => c.side === "foe");
  return (
    <Panel title={<><Swords size={13} /> Combat</>} accent={`Round ${enc.round}`} className="combat">
      <div style={{ fontWeight: 600 }}>{enc.name}</div>
      {enc.phase && <div className="row" style={{ margin: "4px 0 8px" }}><Chip tone={enc.phase === "crawler_reaction" ? "magenta" : "cyan"}>{PHASES[enc.phase] ?? enc.phase}</Chip></div>}
      {foes.map((f) => (
        <div key={f.id} className="combatant">
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

function CampaignPanel({ campaign }: { campaign: Campaign }) {
  const fc = campaign.floor_clock;
  const hours = fc.remaining_minutes != null ? fc.remaining_minutes / 60 : null;
  const tone = hours == null ? "" : hours < 12 ? "danger" : hours > 72 ? "calm" : "";
  const adv = campaign.clocks.find((c) => c.key === "advancement_cycles");
  const quests = campaign.quests.filter((q) => q.kind === "quest");
  const projects = campaign.quests.filter((q) => q.kind !== "quest");
  return (
    <>
      <Panel title="Floor" accent={campaign.current_floor ?? "?"}>
        <div className="sub">Floor collapse in</div>
        <div className={`countdown ${tone}`}>{fc.remaining_minutes != null ? fmtMinutes(fc.remaining_minutes) : "unknown"}</div>
        <div className="sub" style={{ marginBottom: 8 }}>game time · {fmtMinutes(fc.elapsed_minutes)} elapsed on this floor</div>
        <div className="stat-line"><span className="k">Location</span><span style={{ textAlign: "right" }}>{campaign.current_location ?? "unknown"}</span></div>
        <div className="stat-line"><span className="k">Saferoom</span><span>{campaign.safe_room ? <Chip tone="green">protected</Chip> : <Chip tone="red">exposed</Chip>}</span></div>
        {adv && (
          <div style={{ marginTop: 8 }}>
            <div className="row"><span className="k sub grow">Advancement cycles</span><span className="mono">{adv.current}/{adv.maximum}</span></div>
            <div className="segments" style={{ gridTemplateColumns: `repeat(${adv.maximum ?? 12}, 1fr)` }}>
              {Array.from({ length: adv.maximum ?? 12 }, (_, i) => <i key={i} className={i < adv.current ? "on" : ""} />)}
            </div>
          </div>
        )}
      </Panel>
      {campaign.encounter && <CombatPanel enc={campaign.encounter} />}
      {campaign.active_objective && <Panel title="Objective"><div style={{ fontSize: 13.5 }}>{campaign.active_objective}</div></Panel>}
      <Panel title="Quests" accent={quests.length || ""}>
        {quests.length ? quests.map((q) => <div key={q.id} className="quest"><div>{q.priority === 1 && <Chip tone="magenta">priority</Chip>} {q.title}</div>{q.progress && <div className="sub">{q.progress}</div>}</div>) : <div className="muted">None active.</div>}
      </Panel>
      {projects.length > 0 && (
        <Panel title="Projects" accent={projects.length}>
          {projects.map((q) => <div key={q.id} className="quest"><div>{q.title}</div>{q.progress && <div className="sub">{q.progress}</div>}</div>)}
        </Panel>
      )}
      {campaign.last_save && <div className="sub" style={{ padding: "0 4px" }}>Last save: {campaign.last_save.label} · {new Date(campaign.last_save.created_at).toLocaleString()}</div>}
    </>
  );
}
