import { useCallback, useEffect, useState } from "react";
import { Backpack, BookOpen, Clock3, FileText, Flag, Map as MapIcon, MessageSquareText, Search, Settings2, UserRound, Users } from "lucide-react";
import { api, Campaign, CampaignListItem, Manifest } from "./api";
import { ManifestContext } from "./views/Hud";
import { Chip } from "./ui";
import { StoryView, SystemNotice } from "./views/Story";
import { CharacterSheetDialog } from "./views/Sheet";
import { CharactersView } from "./views/Journals";
import { ChronicleView, QuestsView } from "./views/Journal";
import { SearchDialog } from "./views/Search";
import { WorldView } from "./views/World";
import { Announcement, AnnouncementStage, THEATRICAL } from "./views/Announce";
import { applyMotion, getPrefs, play } from "./prefs";
import { InventoryView } from "./views/Inventory";
import { PeopleView } from "./views/People";
import { RecordsView, RulesView } from "./views/Reference";
import { SettingsView } from "./views/Settings";

const TABS = [
  { id: "story", label: "Story", icon: MessageSquareText },
  { id: "characters", label: "Characters", icon: UserRound },
  { id: "inventory", label: "Inventory", icon: Backpack },
  { id: "quests", label: "Quests", icon: Flag },
  { id: "people", label: "People", icon: Users },
  { id: "timeline", label: "Chronicle", icon: Clock3 },
  { id: "world", label: "World", icon: MapIcon },
  { id: "rules", label: "Rules", icon: BookOpen },
  { id: "records", label: "Records", icon: FileText },
  { id: "settings", label: "Saves & Settings", icon: Settings2 },
] as const;
type Tab = (typeof TABS)[number]["id"];

type Toast = { id: number; text: string; style: string };

export default function App() {
  const [campaigns, setCampaigns] = useState<CampaignListItem[] | null>(null);
  const [cid, setCid] = useState<number | null>(null);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [tab, setTab] = useState<Tab>(() => { try { return (localStorage.getItem("tab") as Tab) || "story"; } catch { return "story"; } });
  const [version, setVersion] = useState(0);
  const [sheet, setSheet] = useState<number | null>(null);
  const [focusQuest, setFocusQuest] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);
  const [announcing, setAnnouncing] = useState<Announcement[]>([]);
  useEffect(() => applyMotion(), []);
  useEffect(() => { // Ctrl+K anywhere, or "/" when not typing, opens search
    const onKey = (e: KeyboardEvent) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName ?? "") || (e.target as HTMLElement)?.isContentEditable;
      if ((e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); setSearching(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [provider, setProvider] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [manifest, setManifest] = useState<Manifest>({ locations: {}, characters: {} });
  useEffect(() => { api.manifest().then((m) => setManifest({ locations: m.locations ?? {}, characters: m.characters ?? {} })).catch(() => {}); }, []);

  useEffect(() => {
    Promise.all([api.campaigns(), api.settings()]).then(([list, prefs]) => {
      setCampaigns(list);
      setProvider(prefs.gm_provider);
      const preferred = list.find((c) => c.id === prefs.active_campaign_id) ?? list.find((c) => !c.is_demo) ?? list[0];
      setCid(preferred?.id ?? null);
    }).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => { try { localStorage.setItem("tab", tab); } catch { /* ignore */ } }, [tab]);

  const refresh = useCallback(async () => {
    if (cid == null) return;
    setCampaign(await api.campaign(cid));
    setVersion((v) => v + 1);
  }, [cid]);
  useEffect(() => { setCampaign(null); refresh().catch((e) => setError(String(e))); }, [refresh]);
  useEffect(() => { const iv = setInterval(() => api.health().then((h) => setProvider(h.provider)).catch(() => {}), 15000); return () => clearInterval(iv); }, []);

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
  const openQuest = (id?: number) => { setFocusQuest(id ?? null); setTab("quests"); };
  useEffect(() => { // any component can announce: window.dispatchEvent(new CustomEvent("crawler:announce", { detail: { text, style } }))
    const on = (e: Event) => { const d = (e as CustomEvent).detail; if (d?.text) pushNotices([{ text: String(d.text), style: String(d.style ?? "notice") }]); };
    window.addEventListener("crawler:announce", on);
    return () => window.removeEventListener("crawler:announce", on);
  });
  const dismiss = (id: number) => {
    setToasts((all) => all.filter((x) => x.id !== id));
  };

  // atmosphere: the whole HUD reads the situation (theme-dcc.css): warm in a saferoom, cold while exploring, red in a
  // fight, and more urgent as the floor runs out of time. Only CSS listens to these; nothing else changes.
  const mood = !campaign ? "explore" : campaign.encounter ? "combat" : campaign.safe_room ? "safe" : "explore";
  const left = campaign?.floor_clock?.remaining_minutes;
  const urgency = left == null ? "calm" : left < 6 * 60 ? "critical" : left < 24 * 60 ? "warn" : "calm";
  useEffect(() => {
    document.documentElement.dataset.mood = mood;
    document.documentElement.dataset.urgency = urgency;
  }, [mood, urgency]);

  if (error) return <div className="view"><div className="error-box">Could not reach the Crawler Console server: {error}</div></div>;
  return (
    <ManifestContext.Provider value={manifest}>
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-show">Dungeon Crawler World</span>
          <span className="brand-word">Crawler Console</span>
        </div>
        <nav className="nav" aria-label="Views">
          {/* icon-only at narrower widths: the label stays the accessible name and shows as a tooltip on hover or focus */}
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)} aria-current={tab === t.id ? "page" : undefined} data-tip={t.label}>
              <t.icon size={15} aria-hidden /><span className="nav-label">{t.label}</span>
            </button>
          ))}
        </nav>
        <div className="top-right">
          {campaign && <button className="btn small ghost top-search" onClick={() => setSearching(true)} title="Search what the party knows (Ctrl+K)"><Search size={14} /> Search</button>}
          {campaign && (
            <>
              {campaign.is_demo ? <span className="demo-ribbon">DEMO DATA</span> : null}
            </>
          )}
          <Chip tone={provider === "codex" ? "cyan" : "amber"} title="Runtime GM provider (change in Saves & Settings)">GM: {provider || "…"}</Chip>
          <span className="live-bug" title="The Syndicate is watching. Every second of it."><i aria-hidden /> Live</span>
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
      </header>
      <main className="main">
        {!campaigns ? <div className="view muted">Loading…</div> : campaigns.length === 0 || cid == null ? (
          <div className="view"><div className="view-inner"><SettingsView cid={0} version={version} onChanged={refresh} onCampaign={chooseCampaign} /></div></div>
        ) : !campaign ? <div className="view muted">Loading campaign…</div> : tab === "story" ? (
          <StoryView key={cid} campaign={campaign} onState={refresh} onNotices={pushNotices} openCharacter={setSheet} openQuest={openQuest} />
        ) : (
          <div className="view">
            {tab === "characters" && <CharactersView cid={cid} version={version} openCharacter={setSheet} />}
            {tab === "inventory" && <InventoryView cid={cid} version={version} openQuest={openQuest} />}
            {tab === "quests" && <QuestsView cid={cid} version={version} focus={focusQuest} openCharacter={setSheet} />}
            {tab === "people" && <PeopleView cid={cid} version={version} openCharacter={setSheet} openQuest={openQuest}
              pcAssignment={campaign.roster.find((c) => c.id === campaign.active_character_id)?.assignment}
              pcName={campaign.roster.find((c) => c.id === campaign.active_character_id)?.name} />}
            {tab === "timeline" && <ChronicleView cid={cid} version={version} openCharacter={setSheet} />}
            {tab === "world" && <WorldView cid={cid} campaign={campaign} version={version} onChanged={refresh} openCharacter={setSheet} />}
            {tab === "rules" && <RulesView cid={cid} />}
            {tab === "records" && <RecordsView cid={cid} />}
            {tab === "settings" && <SettingsView cid={cid} version={version} onChanged={refresh} onCampaign={chooseCampaign} />}
          </div>
        )}
      </main>
      {cid != null && (
        <SearchDialog cid={cid} open={searching} onClose={() => setSearching(false)} go={(h) => {
          if (h.type === "quest" && h.id != null) openQuest(h.id);
          else if (h.type === "person" && h.id != null) setSheet(h.id);
          else setTab(h.type === "item" ? "inventory" : h.type === "record" ? "records" : h.type === "story" || h.type === "group" ? "story" : "timeline");
        }} />
      )}
      {cid != null && <CharacterSheetDialog cid={cid} charId={sheet} onClose={() => setSheet(null)} onChanged={refresh} />}
      <AnnouncementStage queue={announcing} onDone={(id) => setAnnouncing((q) => q.filter((a) => a.id !== id))} />
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className="toast" onClick={() => dismiss(t.id)} title="Click to dismiss"><SystemNotice text={t.text} style={t.style} /></div>)}
      </div>
    </div>
    </ManifestContext.Provider>
  );
}
