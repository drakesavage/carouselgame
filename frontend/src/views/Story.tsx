import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowDown, BookOpen, ChevronRight, Clapperboard, Eye, Film, Info, MessageSquare, RotateCcw, Send, Sparkles, Tent, Users } from "lucide-react";
import { ActiveStory, api, ApiError, Campaign, CharacterCard, Message, newKey, TurnStatus } from "../api";
import { Chip, Empty, Markdown, Portrait } from "../ui";
import { ReportButton } from "./Report";
import { StoryBar } from "./StoryBar";
import { VenuePanel } from "./VenuePanel";
import { AroundPanel } from "./AroundPanel";
import { OpeningBar } from "./OpeningBar";
import { SPECTATOR_QUICK, SpectatorFrame, WipedScreen } from "./Spectator";
import { startOpening } from "./Campaigns";
import { SceneRail } from "./SceneRail";
import { Decision, DiscoveryCards, IMPORTANT_CARDS, portraitSrc, RosterPanel, SceneBanner, splitDecision, StoryText, useManifest } from "./Hud";
import "../scene.css";

const STAGES = ["loading", "gm_thinking", "resolving", "saving"];
const STAGE_LABEL: Record<string, string> = { loading: "load state", gm_thinking: "GM thinking", resolving: "rules", saving: "persist" };

const RECAP_PROMPT = "Give me a \"Previously on Carousel\" recap from the campaign records to re-baseline us, then set the current scene with where everyone is and what decision is in front of me. (Recap/orientation only — not a substantive decision.)";
const QUICK = [
  { icon: Eye, label: "Look around", text: "I take a careful look around. " },
  { icon: MessageSquare, label: "Talk to…", text: "I talk to " },
  { icon: Users, label: "Check in with the group", text: "I check in with the group — how is everyone doing? " },
  { icon: Tent, label: "Rest at camp", text: "We rest here at camp for ", safeOnly: true },
  { icon: BookOpen, label: "Ask about the rules", text: "Rules question (no in-world action): " },
];

type Pending = { key: string; text: string; turnId?: number; status: "sending" | "processing" | "failed"; progress?: TurnStatus["progress"]; error?: string; unreachable?: boolean };
// the request never reached the game: a proxy with nothing behind it (502/503/504) or no network at all
const unreachable = (e: unknown) => (e instanceof ApiError && [502, 503, 504].includes(e.status)) || e instanceof TypeError;

const PAGE = 80;  // the server's default page of messages

export function StoryView({ campaign, story, onState, onNotices, openCharacter, openSaves, openBehindTheScenes, openChronicle }: {
  campaign: Campaign; story: ActiveStory | null; onState: () => void; onNotices: (n: { text: string; style: string }[]) => void;
  openCharacter: (id: number) => void; openSaves?: () => void; openBehindTheScenes?: () => void; openChronicle?: () => void;
}) {
  const cid = campaign.id;
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [draft, setDraft] = useState(() => { try { return localStorage.getItem(`draft:${cid}`) ?? ""; } catch { return ""; } });
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

  // A new game whose opening turn never started (the create call was cut off): start it, with the same per-tab key.
  const opening = campaign.opening?.stage === "car" && messages?.length === 0 && !pending;
  useEffect(() => {
    if (!opening) return;
    startOpening(cid).then(() => api.activeTurn(cid)).then((t) => {
      if (t) setPending({ key: "", text: "", turnId: t.turn_id, status: "processing", progress: t.progress });
      else loadMessages();
    }).catch(() => {});
  }, [opening, cid]);  // eslint-disable-line react-hooks/exhaustive-deps

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
    const moments = turnMoments(t.result?.notices ?? [], gm?.meta.cards ?? []);
    if (moments.length) onNotices(moments);
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

  /** A turn the server started from a panel click (a venue move, talking to a resident). */
  const serverTurn = (t: TurnStatus) => {
    anchor.current = { kind: "bottom" };
    if (t.status === "completed") finish(t);
    else setPending({ key: "", text: "", turnId: t.turn_id, status: t.status === "failed" ? "failed" : "processing", progress: t.progress,
                      error: t.error ?? undefined });
  };
  const busy = !!pending && pending.status !== "failed";
  const lastGm = messages ? [...messages].reverse().find((x) => x.role === "gm")?.id : undefined;
  const first = pc?.name.split(" ")[0];
  const spectating = !!campaign.fate?.deathwatch;
  if (campaign.fate?.wiped) return <WipedScreen campaign={campaign} onChanged={onState} openSaves={openSaves} />;
  const quick = spectating ? SPECTATOR_QUICK : QUICK.filter((q) => !q.safeOnly || campaign.safe_room);
  return (
    <div className="story-layout">
      <aside className="side left" aria-label="Cast">
        <RosterPanel campaign={campaign} openCharacter={openCharacter} />
      </aside>
      <div className="story-col">
        <div className="viewfinder" aria-hidden><i /><i /><i /><i /></div>
        {spectating ? <SpectatorFrame campaign={campaign} story={story} onChanged={onState} openChronicle={openChronicle} /> : <>
          {story && <CameraLine camera={story.camera} />}
          <StoryBar story={story} />
        </>}
        <SceneBanner campaign={campaign} onArt={onState} openCharacter={openCharacter} />
        <OpeningBar campaign={campaign} />
        <VenuePanel cid={cid} version={campaign} onTurn={serverTurn} />
        <AroundPanel cid={cid} version={campaign} onTurn={serverTurn} />
        <div className="transcript" ref={scroller} onScroll={onScroll} role="log" aria-label="Story transcript">
          <div className="transcript-inner">
            {more && <button className="btn small ghost earlier" onClick={earlier}>Load earlier messages</button>}
            {messages === null ? <div className="muted">Loading transcript…</div> : messages.length === 0 && !pending ? (
              <Empty>
                <div className="h2" style={{ marginTop: 0 }}>Nothing has happened yet</div>
                <p>Start with a recap of where the campaign left off, or just say what {first ? `${first} does` : "you do"}.</p>
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
                      : "Nothing was applied to the game state."}</div>
                    <div className="row">
                      {pending.text && <button className="btn" onClick={() => send(pending.text, pending.key)}><RotateCcw size={14} /> Retry</button>}
                      <button className="btn ghost" onClick={() => { setDraft(pending.text); setPending(null); textarea.current?.focus(); }}>Edit &amp; resend</button>
                      <ReportButton cid={cid} turnId={pending.turnId ?? null} compact={false} />
                    </div>
                  </div>
                ) : (
                  <div className="working">
                    <div className="row"><Film size={15} color="var(--brass)" aria-hidden /><span className="quip">The projector warms up…</span><span className="spacer" />
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
          <textarea id="composer" ref={textarea} value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy} aria-label="Your next move"
            placeholder={campaign.fate?.deathwatch ? "Ask for a cut, a pace, or a summary"
              : `What does ${first ?? "your character"} do? Say anything.`}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(draft); } }} />
          <div className="row wrap">
            <div className="quick">
              {!spectating && <button className="btn small" disabled={busy} onClick={() => send(RECAP_PROMPT)} title="Ask the GM for a recap and scene-setting"><Sparkles size={13} aria-hidden /> Previously on…</button>}
              {quick.map((q) => (
                <button key={q.label} className="btn small ghost" disabled={busy} onClick={() => { setDraft((d) => (d ? d + " " : "") + q.text); textarea.current?.focus(); }}>
                  <q.icon size={13} aria-hidden /> {q.label}
                </button>
              ))}
            </div>
            <span className="spacer" />
            <span className="kbd">Ctrl+Enter</span>
            <button className="btn primary" disabled={busy || !draft.trim()} onClick={() => send(draft)}><Send size={14} /> Act</button>
          </div>
        </div>
      </div>
      <aside className="side right" aria-label="Production"><SceneRail campaign={campaign} story={story} onArt={onState} openBehindTheScenes={openBehindTheScenes} /></aside>
    </div>
  );
}

/** The camera line over the story: the camera is away, or rolling. Only shown while a story runs (story_api camera). */
function CameraLine({ camera }: { camera: "on" | "off" }) {
  return camera === "on"
    ? <div className="camera-line on"><span className="rec"><i aria-hidden /> REC</span><b>ON SCREEN</b> — The camera is rolling.</div>
    : <div className="camera-line off"><Clapperboard size={13} aria-hidden /><b>OFF SCREEN</b> — The camera is away.</div>;
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
      <BehindTheScenes meta={meta} />
      <div className="msg-tools"><ReportButton cid={cid} turnId={m.turn_id} /></div>
      {ask && <Decision text={ask} />}
    </div>
  );
}

/** Behind the Scenes: the state changes and rules consulted for this turn, closed by default and never part of the story. */
function BehindTheScenes({ meta }: { meta: Message["meta"] }) {
  const changes = meta.changes ?? [], rules = meta.rules_cited ?? [];
  if (!changes.length && !rules.length) return null;
  const count = [changes.length && `${changes.length} change${changes.length > 1 ? "s" : ""}`, rules.length && `${rules.length} rule${rules.length > 1 ? "s" : ""}`].filter(Boolean).join(" · ");
  return (
    <details className="mechanics">
      <summary>
        <ChevronRight size={12} className="caret" aria-hidden /> <span className="mechanics-title">Behind the Scenes</span>
        {count && <span className="mech-count">{count}</span>}
      </summary>
      {!!changes.length && (
        <div className="ledger">
          <div className="mechanics-sub">State changes</div>
          <ul>{changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      {!!rules.length && (
        <div className="ledger">
          <div className="mechanics-sub">Rules consulted</div>
          <div className="chips">{rules.map((r) => <span key={r} className="chip cite-btn"><BookOpen size={11} aria-hidden /> {r}</span>)}</div>
        </div>
      )}
    </details>
  );
}

/** What a finished turn puts up as a toast or a title card: the turn's own notices, then deaths and resolved leads from the records. */
function turnMoments(notices: { text: string; style: string }[], cards: { type: string; summary: string }[]) {
  return [
    ...notices.map((n) => ({ text: n.text, style: n.style === "warning" ? "warning" : "notice" })),
    ...cards.filter((c) => IMPORTANT_CARDS.has(c.type)).map((c) => ({ text: c.summary, style: /down|death/.test(c.type) ? "warning" : "notice" })),
  ];
}

/** A notice in the transcript or a toast: a small title card labelled NOTICE or WARNING. */
export function SystemNotice({ text, style }: { text: string; style?: string }) {
  const warning = style === "warning";
  return (
    <div className={`sys ${warning ? "warning" : "notice"}`}>
      <div className="sys-head">{warning ? <AlertTriangle size={13} aria-hidden /> : <Info size={13} aria-hidden />}{warning ? "WARNING" : "NOTICE"}</div>
      <Markdown text={text} />
    </div>
  );
}
