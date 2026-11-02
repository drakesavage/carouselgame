import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Clapperboard, Map as MapIcon, ScrollText, Search, Settings2, Ticket, Users } from "lucide-react";
import { api, ActiveStory, Campaign, CampaignListItem, Manifest } from "./api";
import { ManifestContext } from "./views/Hud";
import { StoryView, SystemNotice } from "./views/Story";
import { CharacterSheetDialog } from "./views/Sheet";
import { ChronicleView } from "./views/Journal";
import { SearchDialog } from "./views/Search";
import { MapView } from "./views/MapView";
import { Announcement, AnnouncementStage, THEATRICAL } from "./views/Announce";
import { applyMotion, getPrefs, play } from "./prefs";
import { PeopleView } from "./views/People";
import { RecordsView, RulesView } from "./views/Reference";
import { SettingsView } from "./views/Settings";
import { Wallpaper } from "./views/Wallpaper";

const TABS = [
  { id: "scene", label: "Scene", icon: Clapperboard },
  { id: "map", label: "Town Map", icon: MapIcon },
  { id: "wallpaper", label: "Red Wallpaper", icon: ScrollText },
  { id: "tickets", label: "Tickets", icon: Ticket },
  { id: "chronicle", label: "Chronicle", icon: BookOpen },
  { id: "lodge", label: "Lodge", icon: Users },
  { id: "settings", label: "Saves & Settings", icon: Settings2 },
] as const;
type Tab = (typeof TABS)[number]["id"];
/** Tab ids an older build stored, mapped onto the seven screens. */
const OLD_TABS: Record<string, Tab> = { story: "scene", timeline: "chronicle", people: "lodge", characters: "lodge", world: "map" };
const storedTab = (t: string | null): Tab => (t && TABS.some((x) => x.id === t) ? (t as Tab) : (t && OLD_TABS[t]) || "scene");

type Toast = { id: number; text: string; style: string };

/** "14 min ago" from an ISO timestamp. */
export function ago(iso: string, now = Date.now()) {
  const m = Math.max(0, Math.round((now - Date.parse(iso)) / 60000));
  if (Number.isNaN(m)) return "";
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

export default function App() {
  const [campaigns, setCampaigns] = useState<CampaignListItem[] | null>(null);
  const [cid, setCid] = useState<number | null>(null);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [tab, setTab] = useState<Tab>(() => { try { return storedTab(localStorage.getItem("tab")); } catch { return "scene"; } });
  const [version, setVersion] = useState(0);
  const [story, setStory] = useState<ActiveStory | null>(null);
  const [sheet, setSheet] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);
  const [announcing, setAnnouncing] = useState<Announcement[]>([]);
  const [, setTick] = useState(0);  // re-renders the "saved … ago" line
  useEffect(() => { const iv = setInterval(() => setTick((t) => t + 1), 60000); return () => clearInterval(iv); }, []);
  useEffect(() => applyMotion(), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName ?? "") || (e.target as HTMLElement)?.isContentEditable;
      // Ctrl+K anywhere, or "/" when not typing, opens search
      if ((e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey)) { e.preventDefault(); setSearching(true); return; }
      // Alt+1…Alt+7 switch screens
      if (e.altKey && !e.ctrlKey && /^[1-7]$/.test(e.key)) { e.preventDefault(); setTab(TABS[Number(e.key) - 1].id); return; }
      // Ctrl+/ goes to the composer
      if (e.key === "/" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault(); setTab("scene");
        setTimeout(() => document.getElementById("composer")?.focus(), 0);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [manifest, setManifest] = useState<Manifest>({ locations: {}, characters: {} });
  useEffect(() => { api.manifest().then((m) => setManifest({ locations: m.locations ?? {}, characters: m.characters ?? {} })).catch(() => {}); }, []);

  useEffect(() => {
    Promise.all([api.campaigns(), api.settings()]).then(([list, prefs]) => {
      setCampaigns(list);
      const preferred = list.find((c) => c.id === prefs.active_campaign_id) ?? list.find((c) => !c.is_demo) ?? list[0];
      setCid(preferred?.id ?? null);
    }).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => { try { localStorage.setItem("tab", tab); } catch { /* ignore */ } }, [tab]);
  // the Red Wallpaper is a layer over the game: Esc goes back to the screen it was opened from
  const prevTab = useRef<Tab>("scene");
  const lastTab = useRef<Tab>(tab);
  useEffect(() => { if (lastTab.current !== tab) { prevTab.current = lastTab.current; lastTab.current = tab; } }, [tab]);
  useEffect(() => {
    if (tab !== "wallpaper") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || document.querySelector("[role=dialog]")) return;
      setTab(prevTab.current === "wallpaper" ? "scene" : prevTab.current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab]);

  const refresh = useCallback(async () => {
    if (cid == null) return;
    setCampaign(await api.campaign(cid));
    setVersion((v) => v + 1);
  }, [cid]);
  useEffect(() => { setCampaign(null); setStory(null); refresh().catch((e) => setError(String(e))); }, [refresh]);
  // the running story (camera, Plot Cycle) comes from the engine on every state change
  useEffect(() => {
    if (cid == null) return;
    let live = true;
    api.story(cid).then((s) => { if (live) setStory(s.active); }).catch(() => { if (live) setStory(null); });
    return () => { live = false; };
  }, [cid, version]);

  const chooseCampaign = async (id: number | null) => {  // null: the open campaign was deleted and none is left
    if (id == null) { setCid(null); setCampaign(null); setCampaigns(await api.campaigns()); return; }
    if (id !== cid) { setCid(id); api.saveSettings({ active_campaign_id: id }).catch(() => {}); }
    setCampaigns(await api.campaigns());
    if (id === cid) refresh().catch(() => {});
  };
  const pushNotices = (n: { text: string; style: string }[]) => {
    // device preference: theatrical announcements for the big moments, toasts for the rest; or toasts only; or none
    const mode = getPrefs().announcements;
    if (mode === "off") return;
    const big = mode === "theatrical" ? n.filter((x) => THEATRICAL.has(x.style)) : [];
    if (big.length) setAnnouncing((q) => [...q, ...big.map((x, i) => ({ ...x, id: Date.now() + 100 + i }))]);
    n = n.filter((x) => !big.includes(x));
    if (n.length) play("notice");
    const add = n.map((x, i) => ({ ...x, id: Date.now() + i }));
    setToasts((t) => [...t, ...add].slice(-4));
    add.forEach((t) => setTimeout(() => setToasts((all) => all.filter((x) => x.id !== t.id)), 9000));
  };
  const pushRef = useRef(pushNotices);
  pushRef.current = pushNotices;
  useEffect(() => { // any component can announce: window.dispatchEvent(new CustomEvent("crawler:announce", { detail: { text, style } }))
    const on = (e: Event) => { const d = (e as CustomEvent).detail; if (d?.text) pushRef.current([{ text: String(d.text), style: String(d.style ?? "notice") }]); };
    window.addEventListener("crawler:announce", on);
    return () => window.removeEventListener("crawler:announce", on);
  }, []);
  const dismiss = (id: number) => setToasts((all) => all.filter((x) => x.id !== id));

  // the mood comes from engine state only: CSS listens to these, no component invents camera or fate
  const camera = story ? story.camera : "none";
  const fate = campaign?.fate?.wiped ? "wiped" : campaign?.fate?.deathwatch ? "deathwatch" : "alive";
  const safe = campaign?.safe_room ? "1" : "0";
  useEffect(() => {
    const d = document.documentElement.dataset;
    d.camera = camera; d.fate = fate; d.safe = safe;
  }, [camera, fate, safe]);

  // Camera and Plot Cycle changes are said out loud (live region) and a new phase gets a title card. Both come only from
  // the story payload changing between two fetches of the same story, never from narration text.
  const [status, setStatus] = useState("");
  const seen = useRef<{ id: number; camera: string; phase: string } | null>(null);
  useEffect(() => {
    const was = seen.current;
    seen.current = story ? { id: story.id, camera: story.camera, phase: story.phase } : null;
    if (!story || !was || was.id !== story.id) return;
    const said: string[] = [];
    if (was.camera !== story.camera) said.push(story.camera === "on" ? "On screen. The camera is rolling." : "Off screen.");
    if (was.phase !== story.phase) {
      said.push(`Plot Cycle: ${story.phase}.`);
      pushRef.current([{ text: story.phase, style: "phase" }]);
    }
    if (said.length) setStatus(said.join(" "));
  }, [story]);
  const lastFate = useRef(fate);
  useEffect(() => {
    if (lastFate.current !== fate && fate === "deathwatch") setStatus("Your character has left the scene. Spectator view.");
    lastFate.current = fate;
  }, [fate]);
  const openBehindTheScenes = () => { setTab("settings"); setTimeout(() => document.getElementById("behind-the-scenes")?.scrollIntoView({ block: "start" }), 50); };

  if (error) return <div className="view"><div className="error-box">Could not reach the Carousel server: {error}</div></div>;
  const clock = campaign?.clock;
  return (
    <ManifestContext.Provider value={manifest}>
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-word">CAROUSEL</span>
          <span className="brand-line">A game of horror movies</span>
        </div>
        <nav className="nav" aria-label="Screens">
          {/* icon-only at narrower widths: the label stays the accessible name and shows as a tooltip on hover or focus */}
          {TABS.map((t, i) => (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)} aria-current={tab === t.id ? "page" : undefined}
              data-tip={t.label} title={`${t.label} (Alt+${i + 1})`}>
              <t.icon size={15} aria-hidden /><span className="nav-label">{t.label}</span>
            </button>
          ))}
        </nav>
        <div className="top-right">
          {campaign && <button className="btn small ghost top-search" onClick={() => setSearching(true)} title="Search what the group knows (Ctrl+K)"><Search size={14} aria-hidden /> Search</button>}
          {campaign?.is_demo ? <span className="demo-ribbon">DEMO DATA</span> : null}
        </div>
      </header>
      {campaign && (
        <div className="status-strip" aria-label="Where things stand">
          <span>
            <span className="chapter-kicker">{story ? "Now playing" : "Location"}</span>
            <span className="chapter">{story ? story.title : campaign.current_location ?? "Somewhere in Carousel"}</span>
          </span>
          {clock && <span className="clock">Day {clock.day} · {clock.time}</span>}
          <span className={`save-note${campaign.last_save ? "" : " unsaved"}`}>
            {campaign.last_save ? `Saved at camp, ${ago(campaign.last_save.created_at)}` : "Not saved since leaving camp"}
          </span>
          <span className="spacer" />
          {campaigns && campaigns.length > 0 && (
            <select className="select" value={cid ?? ""} aria-label="Campaign" onChange={(e) => {
              if (e.target.value === "manage") { setTab("settings"); setTimeout(() => document.getElementById("campaigns")?.scrollIntoView({ block: "start" }), 50); return; }
              chooseCampaign(Number(e.target.value));
            }}>
              {campaigns.filter((c) => !c.archived || c.id === cid).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              <option value="manage">Manage campaigns…</option>
            </select>
          )}
        </div>
      )}
      <main className="main">
        {!campaigns ? <div className="view muted">Loading…</div> : campaigns.length === 0 || cid == null ? (
          <div className="view"><div className="view-inner"><SettingsView cid={0} version={version} onChanged={refresh} onCampaign={chooseCampaign} /></div></div>
        ) : !campaign ? <div className="view muted">Loading campaign…</div> : (
          <>
            {/* the Scene stays mounted while another screen is open: the draft, scroll position and a turn in flight survive */}
            <div className="scene-host" hidden={tab !== "scene"}>
              <StoryView key={cid} campaign={campaign} story={story} onState={refresh} onNotices={pushNotices} openCharacter={setSheet}
                openSaves={() => setTab("settings")} openBehindTheScenes={openBehindTheScenes} openChronicle={() => setTab("chronicle")} />
            </div>
            {tab !== "scene" && (
              <div className={`view ${tab === "wallpaper" ? "wp-view" : ""}`}>
                {tab === "map" && <MapView cid={cid} campaign={campaign} version={version} onChanged={refresh} openCharacter={setSheet} />}
                {tab === "wallpaper" && <Wallpaper campaign={campaign} story={story} version={version} onChanged={refresh} openTickets={() => setTab("tickets")} />}
                {tab === "tickets" && <div className="view-inner muted">No tickets to show yet.</div>}
                {tab === "chronicle" && <ChronicleView cid={cid} version={version} openCharacter={setSheet} />}
                {tab === "lodge" && <PeopleView cid={cid} version={version} openCharacter={setSheet} openQuest={() => setTab("chronicle")}
                  pcAssignment={campaign.roster.find((c) => c.id === campaign.active_character_id)?.assignment}
                  pcName={campaign.roster.find((c) => c.id === campaign.active_character_id)?.name} />}
                {tab === "settings" && <>
                  <SettingsView cid={cid} version={version} onChanged={refresh} onCampaign={chooseCampaign} />
                  <section className="view-inner" id="behind-the-scenes" aria-label="Behind the Scenes">
                    <h2 className="h2">Behind the Scenes</h2>
                    <RulesView cid={cid} />
                    <RecordsView cid={cid} />
                  </section>
                </>}
              </div>
            )}
          </>
        )}
      </main>
      {cid != null && (
        <SearchDialog cid={cid} open={searching} onClose={() => setSearching(false)} go={(h) => {
          if (h.type === "person" && h.id != null) setSheet(h.id);
          else setTab(h.type === "record" ? "settings" : h.type === "story" || h.type === "group" ? "scene" : "chronicle");
        }} />
      )}
      {cid != null && <CharacterSheetDialog cid={cid} charId={sheet} onClose={() => setSheet(null)} onChanged={refresh} />}
      <AnnouncementStage queue={announcing} onDone={(id) => setAnnouncing((q) => q.filter((a) => a.id !== id))} />
      <div className="sr-only" role="status" aria-live="polite">{status}</div>
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className="toast" onClick={() => dismiss(t.id)} title="Click to dismiss"><SystemNotice text={t.text} style={t.style} /></div>)}
      </div>
    </div>
    </ManifestContext.Provider>
  );
}
