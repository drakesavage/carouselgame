import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Clapperboard, Map as MapIcon, MoreHorizontal, NotebookText, ScrollText, Search, Settings2, Ticket, Users, UsersRound } from "lucide-react";
import { api, ActiveStory, Campaign, CampaignListItem, Manifest } from "./api";
import { ManifestContext } from "./views/Hud";
import { StoryView, SystemNotice } from "./views/Story";
import { CharacterSheetDialog } from "./views/Sheet";
import { ChronicleView } from "./views/Journal";
import { SearchDialog } from "./views/Search";
import { MapView } from "./views/MapView";
import { Announcement, AnnouncementStage, THEATRICAL } from "./views/Announce";
import { applyMotion, getPrefs, play } from "./prefs";
import { ago } from "./ui";
import { PeopleView } from "./views/People";
import { SettingsView } from "./views/Settings";
import { Wallpaper } from "./views/Wallpaper";
import { TicketsView } from "./views/Tickets";

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

/** Phone bottom navigation: four screens and More (the rest). */
const BOTTOM: Tab[] = ["scene", "map", "wallpaper", "lodge"];

type Toast = { id: number; text: string; style: string };

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
  useEffect(() => { // any component can announce: window.dispatchEvent(new CustomEvent("carousel:announce", { detail: { text, style } }))
    const on = (e: Event) => { const d = (e as CustomEvent).detail; if (d?.text) pushRef.current([{ text: String(d.text), style: String(d.style ?? "notice") }]); };
    window.addEventListener("carousel:announce", on);
    return () => window.removeEventListener("carousel:announce", on);
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
  // narrower screens: the cast and the production notes open as drawers from the status strip (CSS decides when they're drawers)
  const [drawer, setDrawer] = useState<"cast" | "notes" | null>(null);
  const [more, setMore] = useState(false);
  useEffect(() => { document.documentElement.dataset.drawer = drawer ?? ""; }, [drawer]);
  useEffect(() => { setDrawer(null); setMore(false); }, [tab]);
  useEffect(() => {
    if (!drawer && !more) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setDrawer(null); setMore(false); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, more]);
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
          {tab === "scene" && <>
            <button className="btn small ghost drawer-btn cast-btn" aria-expanded={drawer === "cast"} onClick={() => setDrawer(drawer === "cast" ? null : "cast")}>
              <UsersRound size={13} aria-hidden /> Cast</button>
            <button className="btn small ghost drawer-btn notes-btn" aria-expanded={drawer === "notes"} onClick={() => setDrawer(drawer === "notes" ? null : "notes")}>
              <NotebookText size={13} aria-hidden /> Production notes</button>
          </>}
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
                {tab === "tickets" && <TicketsView campaign={campaign} version={version} />}
                {tab === "chronicle" && <ChronicleView campaign={campaign} story={story} version={version} openCharacter={setSheet} openSaves={() => setTab("settings")} />}
                {tab === "lodge" && <PeopleView cid={cid} version={version} openCharacter={setSheet} openQuest={() => setTab("chronicle")}
                  pcAssignment={campaign.roster.find((c) => c.id === campaign.active_character_id)?.assignment}
                  pcName={campaign.roster.find((c) => c.id === campaign.active_character_id)?.name} />}
                {tab === "settings" && <SettingsView cid={cid} version={version} onChanged={refresh} onCampaign={chooseCampaign} />}
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
      {drawer && <div className="drawer-scrim" onClick={() => setDrawer(null)} aria-hidden />}
      {campaign && (
        <nav className="bottom-nav" aria-label="Screens">
          {BOTTOM.map((id) => { const t = TABS.find((x) => x.id === id)!; return (
            <button key={id} className={tab === id ? "active" : ""} aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>
              <t.icon size={18} aria-hidden /><span>{id === "wallpaper" ? "Wallpaper" : t.label}</span>
            </button>); })}
          <button className={!BOTTOM.includes(tab) ? "active" : ""} aria-expanded={more} aria-haspopup="menu" onClick={() => setMore((m) => !m)}>
            <MoreHorizontal size={18} aria-hidden /><span>More</span>
          </button>
          {more && (
            <div className="more-menu" role="menu">
              {TABS.filter((t) => !BOTTOM.includes(t.id)).map((t) => (
                <button key={t.id} role="menuitem" className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}><t.icon size={15} aria-hidden /> {t.label}</button>
              ))}
            </div>
          )}
        </nav>
      )}
      <div className="sr-only" role="status" aria-live="polite">{status}</div>
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className="toast" onClick={() => dismiss(t.id)} title="Click to dismiss"><SystemNotice text={t.text} style={t.style} /></div>)}
      </div>
    </div>
    </ManifestContext.Provider>
  );
}
