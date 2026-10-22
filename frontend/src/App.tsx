import { useCallback, useEffect, useState } from "react";
import { Backpack, BookOpen, Clock3, FileText, Flag, MessageSquareText, Settings2, UserRound, Users } from "lucide-react";
import { api, Campaign, CampaignListItem, fmtMinutes } from "./api";
import { Chip } from "./ui";
import { StoryView, SystemNotice } from "./views/Story";
import { CharacterSheetDialog } from "./views/Sheet";
import { CharactersView, InventoryView, NpcView, QuestsView, TimelineView } from "./views/Journals";
import { RecordsView, RulesView } from "./views/Reference";
import { SettingsView } from "./views/Settings";

const TABS = [
  { id: "story", label: "Story", icon: MessageSquareText },
  { id: "characters", label: "Characters", icon: UserRound },
  { id: "inventory", label: "Inventory", icon: Backpack },
  { id: "quests", label: "Quests", icon: Flag },
  { id: "people", label: "People", icon: Users },
  { id: "timeline", label: "Timeline", icon: Clock3 },
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
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [provider, setProvider] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

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

  const chooseCampaign = async (id: number) => {
    setCid(id);
    api.saveSettings({ active_campaign_id: id }).catch(() => {});
    setCampaigns(await api.campaigns());
  };
  const pushNotices = (n: { text: string; style: string }[]) => {
    const add = n.map((x, i) => ({ ...x, id: Date.now() + i }));
    setToasts((t) => [...t, ...add].slice(-4));
    add.forEach((t) => setTimeout(() => setToasts((all) => all.filter((x) => x.id !== t.id)), 9000));
  };

  if (error) return <div className="view"><div className="error-box">Could not reach the Crawler Console server: {error}</div></div>;
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg className="brand-mark" viewBox="0 0 32 32"><path d="M16 3 28 10v12L16 29 4 22V10z" fill="none" stroke="#3ef0c8" strokeWidth="1.8" /><path d="M16 9 22 12.5v7L16 23l-6-3.5v-7z" fill="rgba(62,240,200,.15)" stroke="#3ef0c8" strokeWidth="1" /><circle cx="16" cy="16" r="2.6" fill="#ff3d7f" /></svg>
          <div>CRAWLER CONSOLE<small>DUNGEON HUD // v0.1</small></div>
        </div>
        <nav className="nav">
          {TABS.map((t) => <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)} title={t.label} aria-label={t.label}><t.icon size={15} /><span className="nav-label">{t.label}</span></button>)}
        </nav>
        <div className="top-right">
          {campaign && (
            <>
              {campaign.is_demo ? <span className="demo-ribbon">DEMO DATA</span> : null}
              <Chip tone="amber" title="Floor collapse (game time)">F{campaign.current_floor ?? "?"} · {fmtMinutes(campaign.floor_clock.remaining_minutes)}</Chip>
            </>
          )}
          <Chip tone={provider === "codex" ? "cyan" : "amber"} title="Runtime GM provider (change in Saves & Settings)">GM: {provider || "…"}</Chip>
          {campaigns && campaigns.length > 0 && (
            <select className="select" value={cid ?? ""} onChange={(e) => chooseCampaign(Number(e.target.value))} aria-label="Campaign">
              {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
        </div>
      </header>
      <main className="main">
        {!campaigns ? <div className="view muted">Loading…</div> : campaigns.length === 0 || cid == null ? (
          <div className="view"><div className="view-inner"><SettingsView cid={0} version={version} onChanged={refresh} onCampaign={chooseCampaign} /></div></div>
        ) : !campaign ? <div className="view muted">Loading campaign…</div> : tab === "story" ? (
          <StoryView key={cid} campaign={campaign} onState={refresh} onNotices={pushNotices} openCharacter={setSheet} />
        ) : (
          <div className="view">
            {tab === "characters" && <CharactersView cid={cid} version={version} openCharacter={setSheet} />}
            {tab === "inventory" && <InventoryView cid={cid} version={version} />}
            {tab === "quests" && <QuestsView cid={cid} version={version} />}
            {tab === "people" && <NpcView cid={cid} version={version} openCharacter={setSheet} />}
            {tab === "timeline" && <TimelineView cid={cid} version={version} />}
            {tab === "rules" && <RulesView cid={cid} />}
            {tab === "records" && <RecordsView cid={cid} />}
            {tab === "settings" && <SettingsView cid={cid} version={version} onChanged={refresh} onCampaign={chooseCampaign} />}
          </div>
        )}
      </main>
      {cid != null && <CharacterSheetDialog cid={cid} charId={sheet} onClose={() => setSheet(null)} onChanged={refresh} />}
      <div className="toasts" aria-live="polite">{toasts.map((t) => <div key={t.id} className="toast"><SystemNotice text={t.text} style={t.style} /></div>)}</div>
    </div>
  );
}
