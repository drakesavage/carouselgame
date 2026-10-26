import { useEffect, useMemo, useState } from "react";
import {
  Backpack, Bomb, BookOpen, Boxes, Car, Check, ChevronDown, ChevronRight, Coins, Crosshair, FlaskConical, Gem, Hammer, LayoutGrid, List,
  Loader2, Package, Search, Shield, Sparkles, Sword, Wrench, X,
} from "lucide-react";
import { api, fmtMinutes, Inventory, InvItem, Stage } from "../api";
import { Chip, Empty, sized } from "../ui";
import { ArtButton } from "./Art";
import "../inventory.css";

const CAT: Record<string, { label: string; icon: typeof Package }> = {
  currency: { label: "Currency", icon: Coins }, weapon: { label: "Weapons", icon: Sword }, ammunition: { label: "Ammunition", icon: Crosshair },
  armor: { label: "Armor & wear", icon: Shield }, consumable: { label: "Consumables", icon: FlaskConical }, material: { label: "Materials", icon: Boxes },
  tool: { label: "Tools", icon: Wrench }, trap: { label: "Traps", icon: Bomb }, vehicle: { label: "Vehicles", icon: Car },
  keepsake: { label: "Keepsakes", icon: Gem }, book: { label: "Books", icon: BookOpen }, other: { label: "Other", icon: Package },
};
const STAGES: { id: Stage; label: string }[] = [
  { id: "planned", label: "Planned" }, { id: "designed", label: "Designed" }, { id: "in_progress", label: "Building" },
  { id: "finished", label: "Finished" }, { id: "installed", label: "Installed" },
];
const STATES = [
  { id: "shared", label: "Shared", test: (i: InvItem) => i.owner_character_id == null && !i.deployed },
  { id: "personal", label: "Personal", test: (i: InvItem) => i.owner_character_id != null },
  { id: "equipped", label: "Equipped", test: (i: InvItem) => !!i.equipped },
  { id: "deployed", label: "Deployed", test: (i: InvItem) => !!i.deployed },
  { id: "unknown", label: "Count unknown", test: (i: InvItem) => i.quantity_state === "unknown" },
] as const;

function load<T>(key: string, fallback: T): T { try { return (localStorage.getItem(key) as T) ?? fallback; } catch { return fallback; } }
function save(key: string, v: string) { try { localStorage.setItem(key, v); } catch { /* storage unavailable */ } }

/**
 * The party's inventory: dense grid or list, search and filters, honest counts ("count unknown" is never a number),
 * inventions by stage, Gold with what is committed to reservations, and the materials projects need. Read-only: every
 * change goes through the GM's validated rules operations.
 */
export function InventoryView({ cid, version, openQuest }: { cid: number; version: number; openQuest: (id?: number) => void }) {
  const [data, setData] = useState<Inventory | null>(null);
  const [artTick, setArtTick] = useState(0);
  const [art, setArt] = useState<Record<number, string>>({});
  const [looks, setLooks] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [state, setState] = useState<string | null>(null);
  const [cat, setCat] = useState<string | null>(null);
  const [view, setView] = useState<"grid" | "list">(() => load("inv-view", "grid"));
  const [drafting, setDrafting] = useState(false);
  const [draftMsg, setDraftMsg] = useState<string | null>(null);
  useEffect(() => { api.inventory(cid).then(setData).catch(() => setData(null)); }, [cid, version]);
  useEffect(() => {
    api.artList(cid, { kind: "item" }).then((jobs) => setArt(Object.fromEntries(jobs.filter((j) => j.is_current && j.url && j.subject_id != null).map((j) => [j.subject_id!, j.url!])))).catch(() => {});
    api.artAppearances(cid, "item").then(setLooks).catch(() => {});
  }, [cid, version, artTick]);
  const all = useMemo(() => (data ? [...data.shared, ...data.by_character] : []), [data]);
  const shown = useMemo(() => all.filter((i) => {
    if (i.category === "currency") return false;
    if (state && !STATES.find((s) => s.id === state)!.test(i)) return false;
    if (cat && i.category !== cat) return false;
    return !q || `${i.name} ${i.owner_name ?? ""} ${i.description ?? ""} ${i.status ?? ""}`.toLowerCase().includes(q.toLowerCase());
  }), [all, state, cat, q]);
  if (!data) return <div className="muted">Loading…</div>;
  const counts = (test: (i: InvItem) => boolean) => all.filter((i) => i.category !== "currency" && test(i)).length;
  const groups: [string, InvItem[]][] = [
    ["Shared stash", shown.filter((i) => i.owner_character_id == null && !i.deployed)],
    ["Deployed assets", shown.filter((i) => i.owner_character_id == null && i.deployed)],
    ...Object.entries(shown.filter((i) => i.owner_character_id != null).reduce<Record<string, InvItem[]>>((m, i) => { (m[i.owner_name ?? "?"] ??= []).push(i); return m; }, {})),
  ];
  const inventions = all.filter((i) => i.invention);
  const draftLooks = async () => {
    setDrafting(true); setDraftMsg(null);
    try {
      const r = await api.artDraftItemLooks(cid);
      setDraftMsg(r.drafted.length ? `Drafted ${r.drafted.length} looks${r.remaining ? `; ${r.remaining} left (run again)` : ""}.` : "Every item already has a look.");
      setArtTick((t) => t + 1);
    } catch (e) { setDraftMsg(String(e)); } finally { setDrafting(false); }
  };
  return (
    <div className="view-inner inventory">
      <div className="row"><div className="h1 grow">Inventory</div>
        <button className="btn small ghost" disabled={drafting} onClick={draftLooks} title="Write a physical description for every item without one, for item pictures (art only)">
          {drafting ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />} Draft item looks</button></div>
      {draftMsg && <div className="sub">{draftMsg}</div>}

      <div className="inv-summary">
        <GoldPanel gold={data.gold} />
        <section className="inv-panel">
          <h3>Reserved, not owned</h3>
          {data.reservations.length === 0 ? <div className="muted">Nothing reserved.</div> : data.reservations.map((r) => (
            <button key={r.quest_id} className="inv-res" onClick={() => openQuest(r.quest_id)}>
              <b>{r.item}</b>
              <span className="chips">
                {r.payment === "paid" ? <Chip tone="green">paid{r.paid_gold != null ? ` ${r.paid_gold} Gold` : ""}</Chip> : <Chip tone="amber">{r.payment ?? "unpaid"}</Chip>}
                {!r.owned && <Chip tone="amber">not owned yet</Chip>}
              </span>
              {r.held_by && <span className="sub">Held by {r.held_by}{r.claim ? ` · ${r.claim}` : ""}</span>}
            </button>
          ))}
        </section>
        <section className="inv-panel">
          <h3>Materials</h3>
          {data.materials.length === 0 ? <div className="muted">No materials recorded.</div> : (
            <ul className="inv-mats">{data.materials.map((m) => (
              <li key={m.id}><span className="grow">{m.name}{m.owner ? <span className="muted"> · {m.owner}</span> : null}</span>
                {m.quantity == null ? <span className="qty unknown" title="The record doesn't establish how many">count ?</span> : <span className="qty">×{m.quantity}</span>}</li>))}
            </ul>
          )}
        </section>
      </div>

      {(data.projects.length > 0 || inventions.length > 0) && (
        <section>
          <div className="h2"><Hammer size={14} /> Inventions &amp; projects</div>
          <div className="inv-projects">
            {data.projects.map((p) => (
              <button key={`q${p.quest_id}`} className="inv-project" onClick={() => openQuest(p.quest_id)}>
                <div className="ip-title">{p.title}</div>
                <StageBar stage={p.stage} />
                <div className="ip-meta">
                  {[p.lead && `Lead: ${p.lead}`, p.assistant && `with ${p.assistant}`, p.build_time && `${p.build_time} build`, p.difficulty && `Difficulty ${p.difficulty}`].filter(Boolean).join(" · ")}
                </div>
                {p.materials.length > 0 && <div className="chips">{p.materials.map((m) => (
                  <Chip key={m.material} tone={m.enough ? "green" : "red"}>{m.enough ? <Check size={11} /> : <X size={11} />} {m.material}: need {m.need}, have {m.have ?? "?"}</Chip>))}</div>}
                {p.linked_items.length > 0 && <div className="sub">Uses: {p.linked_items.map((i) => i.name).join(", ")}</div>}
                {p.stage !== "finished" && p.stage !== "installed" && <div className="sub">Not built: it isn't anyone's gear yet.</div>}
              </button>
            ))}
            {inventions.map((i) => (
              <div key={`i${i.id}`} className="inv-project item">
                <div className="ip-title">{i.name}</div>
                {i.stage && <StageBar stage={i.stage} />}
                <div className="ip-meta">{[i.owner_name ?? "Shared", i.deployed ? "deployed" : null, i.status].filter(Boolean).join(" · ")}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="inv-tools">
        <label className="people-search"><Search size={14} aria-hidden /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search items, owners, notes" aria-label="Search items" /></label>
        <div className="seg" role="group" aria-label="View">
          <button className={view === "grid" ? "active" : ""} aria-pressed={view === "grid"} onClick={() => { setView("grid"); save("inv-view", "grid"); }}><LayoutGrid size={13} /> Grid</button>
          <button className={view === "list" ? "active" : ""} aria-pressed={view === "list"} onClick={() => { setView("list"); save("inv-view", "list"); }}><List size={13} /> List</button>
        </div>
      </div>
      <div className="chips inv-filters" role="group" aria-label="Filter by state">
        {STATES.map((s) => <button key={s.id} className={`chip ${state === s.id ? "cyan" : ""}`} aria-pressed={state === s.id} onClick={() => setState(state === s.id ? null : s.id)}>{s.label} <span className="mono">{counts(s.test)}</span></button>)}
      </div>
      <div className="chips inv-filters" role="group" aria-label="Filter by kind">
        {Object.entries(CAT).filter(([k]) => k !== "currency" && counts((i) => i.category === k)).map(([k, c]) => (
          <button key={k} className={`chip ${cat === k ? "cyan" : ""}`} aria-pressed={cat === k} onClick={() => setCat(cat === k ? null : k)}><c.icon size={12} /> {c.label} <span className="mono">{counts((i) => i.category === k)}</span></button>
        ))}
      </div>
      {shown.length === 0 && <Empty>No items match.</Empty>}
      {groups.filter(([, list]) => list.length).map(([title, list]) => (
        <section key={title}>
          <div className="h2">{title === "Shared stash" ? <Backpack size={14} /> : null} {title} <span className="muted">{list.length}</span></div>
          <div className={view === "grid" ? "inv-grid" : "inv-list"}>
            {list.map((i) => <ItemEntry key={i.id} i={i} cid={cid} art={art[i.id]} look={looks[String(i.id)]} onArt={() => setArtTick((t) => t + 1)} list={view === "list"} />)}
          </div>
        </section>
      ))}
    </div>
  );
}

function StageBar({ stage }: { stage: Stage }) {
  const at = STAGES.findIndex((s) => s.id === stage);
  return (
    <ol className="stagebar" aria-label={`Stage: ${STAGES[at]?.label ?? stage}`}>
      {STAGES.map((s, n) => <li key={s.id} className={n < at ? "done" : n === at ? "now" : ""}>{s.label}</li>)}
    </ol>
  );
}

function GoldPanel({ gold }: { gold: Inventory["gold"] }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="inv-panel gold">
      <h3><Coins size={13} /> Gold</h3>
      <div className="gold-amount">{gold.amount ?? "?"}</div>
      {gold.committed > 0 && <div className="sub">{gold.committed} already paid toward reserved items (not in this total).</div>}
      {gold.history.length > 0 && (
        <>
          <button className="link-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />} History ({gold.history.length})</button>
          {open && <ul className="gold-history">{gold.history.map((h) => (
            <li key={h.id}><span className={`mono ${h.delta != null && h.delta < 0 ? "neg" : "pos"}`}>{h.delta == null ? "±?" : h.delta > 0 ? `+${h.delta}` : h.delta}</span>
              <span className="grow">{h.summary}</span>{h.game_time_minutes != null && <span className="muted mono">T+{fmtMinutes(h.game_time_minutes)}</span>}</li>))}
          </ul>}
        </>
      )}
    </section>
  );
}

function ItemEntry({ i, cid, art, look, onArt, list }: { i: InvItem; cid: number; art?: string; look?: string; onArt: () => void; list: boolean }) {
  const [open, setOpen] = useState(false);
  const C = CAT[i.category] ?? CAT.other;
  const effects = Array.isArray(i.properties?.effects) ? (i.properties.effects as unknown[]).map(String) : [];
  const notes = Array.isArray(i.properties?.notes) ? (i.properties.notes as unknown[]).map(String) : [];
  const trap = i.properties?.trap as Record<string, any> | undefined;
  const prov = i.properties?.provenance as string | undefined;
  return (
    <div className={`inv-item ${open ? "open" : ""} ${i.equipped ? "equipped" : ""} ${i.quantity_state === "unknown" ? "unknown" : ""}`}>
      <button className="inv-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="inv-icon">{art ? <img src={sized(art, 32)!} alt="" loading="lazy" /> : <C.icon size={18} aria-hidden />}</span>
        <span className="inv-name">{i.name}</span>
        {list && <span className="inv-cat">{C.label}</span>}
        {list && <span className="inv-owner">{i.owner_name ?? (i.deployed ? "deployed" : "shared")}</span>}
        <span className="inv-flags">
          {i.equipped ? <Chip tone="cyan">{i.slot ?? "equipped"}</Chip> : null}
          {i.deployed ? <Chip tone="amber">deployed</Chip> : null}
          {i.stage && <Chip tone="violet">{STAGES.find((s) => s.id === i.stage)?.label}</Chip>}
        </span>
        {i.quantity_state === "unknown" ? <span className="qty unknown" title={i.quantity_note ?? "The record doesn't establish how many"}>count ?</span> : <span className="qty">×{i.quantity}</span>}
      </button>
      {open && (
        <div className="inv-body">
          {!list && <div className="sub">{C.label}{i.owner_name ? ` · ${i.owner_name}` : ""}</div>}
          {i.description && <p>{i.description}</p>}
          {effects.length > 0 && <ul>{effects.map((e, n) => <li key={n}>{e}</li>)}</ul>}
          {i.status && <p><b>Status:</b> {i.status}</p>}
          {i.quantity_state === "unknown" && <p className="warn">Count not established{i.quantity_note ? `: ${i.quantity_note}` : ""}. It can't be spent as an exact quantity until the GM sets the count.</p>}
          {i.storage && <p><b>Stored:</b> {i.storage}</p>}
          {i.attached_to && <p><b>Installed on:</b> {i.attached_to}</p>}
          {trap && <p><b>Trap:</b> {[trap.trigger, trap.area, trap.check?.kind === "attack" ? `attack +${trap.check.attack_bonus}` : null, trap.damage, trap.effects].filter(Boolean).join(" · ")}</p>}
          {notes.map((n, k) => <p key={k} className="sub">{n}</p>)}
          {look && <p className="sub" title="Appearance notes for pictures (art only, not canon)"><Sparkles size={10} /> <i>{look}</i></p>}
          {prov && <p className="sub"><b>From:</b> {prov}</p>}
          <div className="row"><ArtButton cid={cid} kind="item" subjectId={i.id} subjectName={i.name} label="Picture" onChanged={onArt} /><span className="spacer" /><span className="mono muted">#{i.id}</span></div>
        </div>
      )}
    </div>
  );
}

