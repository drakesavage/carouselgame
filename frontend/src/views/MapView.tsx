import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Footprints, Image as ImageIcon, List, Loader2, Map as MapIcon, X } from "lucide-react";
import { api, ApiError, Campaign, MapPlace, newKey, PlayerMap, TravelResult, TurnStatus } from "../api";
import { Markdown } from "../ui";
import { reducedMotion } from "../prefs";
import { ArtLibrary } from "./ArtLibrary";
import { PlaceDossier } from "./PlaceDossier";
import "../map.css";

/**
 * The town on the illustrated panel map. Master pixels (12000²) are Leaflet CRS.Simple units with y negated; Leaflet
 * zoom 0 is the master, so tile level = zoom + the top level (zoomOffset) and only the manifest's levels are requested.
 */
const ll = (px: number, py: number) => L.latLng(-py, px);
const INSET = 220;
const LABEL_LEVEL = 2;   // pin labels show from this tile level up

type Shot = { travel: TravelResult; place: MapPlace | null; turn: TurnStatus };
type Layer = "all" | "visited" | "told" | "safe" | "omens";
const LAYERS: { id: Layer; label: string }[] = [
  { id: "all", label: "All known places" }, { id: "visited", label: "Visited" }, { id: "told", label: "Heard of" },
  { id: "safe", label: "Safe places" }, { id: "omens", label: "Omens seen" },
];

const TENT = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M12 3 2 20h20L12 3Zm0 6 5 9H7l5-9Z" fill="currentColor"/></svg>';

export function MapView({ cid, campaign, version, onChanged, openCharacter }: {
  cid: number; campaign: Campaign; version: number; onChanged: () => void; openCharacter: (id: number) => void;
}) {
  const [tab, setTab] = useState<"map" | "art">("map");
  const [data, setData] = useState<PlayerMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = () => api.map(cid).then((m) => { setData(m); setError(null); return m; }).catch((e) => { setError(String(e)); return null; });
  useEffect(() => { reload(); }, [cid, version]);
  return (
    <div className="view-inner mapview">
      <div className="map-head">
        <div><div className="kicker">The town map</div><div className="h1">A place full of unfinished stories.</div></div>
        <nav className="tabs" role="tablist" aria-label="Map sections">
          <button role="tab" aria-selected={tab === "map"} className={tab === "map" ? "active" : ""} onClick={() => setTab("map")}><MapIcon size={13} aria-hidden /> Town</button>
          <button role="tab" aria-selected={tab === "art"} className={tab === "art" ? "active" : ""} onClick={() => setTab("art")}><ImageIcon size={13} aria-hidden /> Art library</button>
        </nav>
      </div>
      {error && <div className="sub">{error}</div>}
      {tab === "map" && (data ? <TownMap cid={cid} data={data} version={version} reload={reload} onChanged={onChanged} /> : !error && <div className="muted">Loading…</div>)}
      {tab === "art" && <ArtLibrary cid={cid} campaign={campaign} places={data?.known ?? []} onChanged={onChanged} openCharacter={openCharacter} />}
    </div>
  );
}

/** Known places with a sighted Omen (map_api.py names an Omen's place only once the group knows it). */
function omenPlaces(data: PlayerMap): Set<string> {
  return new Set(data.omens.map((o) => o.place_id).filter((id): id is string => !!id));
}

function TownMap({ cid, data, version, reload, onChanged }: {
  cid: number; data: PlayerMap; version: number; reload: () => Promise<PlayerMap | null>; onChanged: () => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const pins = useRef<L.LayerGroup | null>(null);
  const party = useRef<L.Marker | null>(null);
  const route = useRef<L.Polyline | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [walkTo, setWalkTo] = useState<{ px: number; py: number } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shot, setShot] = useState<Shot | null>(null);
  const [listOpen, setListOpen] = useState(false);   // narrow screens: the layers and place list are a drawer over the map
  const [layer, setLayer] = useState<Layer>(() => {
    try { const v = sessionStorage.getItem(`map-filters:${cid}`); return LAYERS.some((l) => l.id === v) ? (v as Layer) : "all"; } catch { return "all"; }
  });
  useEffect(() => { try { sessionStorage.setItem(`map-filters:${cid}`, layer); } catch { /* storage unavailable */ } }, [cid, layer]);
  const top = data.tiles.levels.length ? data.tiles.levels[data.tiles.levels.length - 1].zoom : 5;
  const bottom = data.tiles.levels.length ? data.tiles.levels[0].zoom : 0;

  const withOmen = useMemo(() => omenPlaces(data), [data]);
  const counts: Record<Layer, number> = {
    all: data.known.length, visited: data.known.filter((p) => p.how === "visited").length, told: data.known.filter((p) => p.how === "told").length,
    safe: data.known.filter((p) => p.safe).length, omens: withOmen.size,
  };
  const shown = data.known.filter((p) => layer === "all" || (layer === "visited" ? p.how === "visited" : layer === "told" ? p.how === "told"
    : layer === "safe" ? p.safe : withOmen.has(p.id)));

  const select = (p: MapPlace, pan = false) => {
    setMsg(null); setWalkTo(null); setSelected(p.id); setListOpen(false);
    if (pan) map.current?.panTo(ll(p.px, p.py));
  };

  // the map itself, once: selection, zoom and centre survive every data refresh
  useEffect(() => {
    if (!el.current || map.current) return;
    const size = data.tiles.size;
    const bounds = L.latLngBounds(ll(0, size), ll(size, 0));
    const m = L.map(el.current, { crs: L.CRS.Simple, minZoom: bottom - top, maxZoom: 0, zoomSnap: 0.25, attributionControl: false,
      maxBounds: bounds.pad(0.1), maxBoundsViscosity: 0.8 });
    L.tileLayer(data.tiles.template, { tileSize: 512, zoomOffset: top, minZoom: bottom - top, maxZoom: 0, maxNativeZoom: 0, bounds, noWrap: true }).addTo(m);
    pins.current = L.layerGroup().addTo(m);
    const p = data.position;
    m.setView(p ? ll(p.px, p.py) : bounds.getCenter(), -2);
    const labels = () => el.current?.classList.toggle("labels-on", m.getZoom() + top >= LABEL_LEVEL);
    m.on("zoomend", labels);
    labels();
    m.on("click", (e: L.LeafletMouseEvent) => {
      if (!(e.originalEvent as MouseEvent).altKey) return;
      setMsg(null); setSelected(null);
      setWalkTo({ px: e.latlng.lng, py: -e.latlng.lat });
    });
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, []);

  // pins (shape and label as well as colour), the group's ring and the last walk
  useEffect(() => {
    const m = map.current, g = pins.current;
    if (!m || !g) return;
    g.clearLayers();
    for (const p of shown) {
      const omen = withOmen.has(p.id);
      const kind = omen ? "omen" : p.safe ? "safe" : "place";
      const html = omen ? "<span>!</span>" : p.safe ? TENT : "";
      const label = `${p.name}${omen ? " (Omen seen)" : p.safe ? " (safe place)" : ""}`;
      const mk = L.marker(ll(p.px, p.py), { icon: L.divIcon({ className: `pin-${kind}${p.id === selected ? " selected" : ""}`, html, iconSize: [22, 22] }),
        keyboard: true, title: label })
        .bindTooltip(p.name, { permanent: true, direction: "top", offset: [0, -12], className: "pin-label" })
        .on("click", () => select(p))
        .addTo(g);
      mk.getElement()?.setAttribute("aria-label", label);
    }
    // an Omen whose place the group doesn't know: a pin where it was seen, with no place name
    if (layer === "all" || layer === "omens") {
      for (const o of data.omens) {
        if (o.place_id) continue;
        const mk = L.marker(ll(o.px, o.py), { icon: L.divIcon({ className: "pin-omen", html: "<span>!</span>", iconSize: [22, 22] }), keyboard: false, title: o.description })
          .bindTooltip(o.description, { direction: "top" }).addTo(g);
        mk.getElement()?.setAttribute("aria-label", `Omen: ${o.description}`);
      }
    }
    if (data.position) {
      const at = ll(data.position.px, data.position.py);
      if (!party.current) party.current = L.marker(at, { icon: L.divIcon({ className: "party-pin", iconSize: [20, 20] }), zIndexOffset: 1000, keyboard: false }).addTo(m);
      else if (!shot) party.current.setLatLng(at);
      party.current.bindTooltip(`You: ${data.position.label ?? ""}`);
      party.current.getElement()?.setAttribute("aria-label", `The group: ${data.position.label ?? ""}`);
    }
    route.current?.remove();
    if (data.last_route?.geometry_px.length) {
      route.current = L.polyline(data.last_route.geometry_px.map(([x, y]) => ll(x, y)), { className: "walked", weight: 3, dashArray: "6 6", interactive: false }).addTo(m);
    }
  }, [data, layer, selected, withOmen]);  // eslint-disable-line react-hooks/exhaustive-deps

  const go = async (to: { to: string } | { north: number; west: number }) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.travel(cid, to, newKey());
      setWalkTo(null);
      await walk(r.travel);
      const fresh = await reload();
      const place = fresh?.known.find((p) => p.id === r.travel.arrived) ?? null;
      setShot({ travel: r.travel, place, turn: r.turn });
    } catch (e) {
      setMsg(e instanceof ApiError && e.status === 409 ? "The GM is still resolving a turn." : e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(false); }
  };

  /** Move the group's marker along the route (the narration is already being written meanwhile). */
  const walk = (t: TravelResult) => new Promise<void>((done) => {
    const pts = t.geometry_px, mk = party.current, m = map.current;
    if (!mk || !m || pts.length < 2) return done();
    if (reducedMotion()) { mk.setLatLng(ll(...pts[pts.length - 1])); return done(); }
    const seg = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]));
    const total = seg.reduce((a, b) => a + b, 0) || 1;
    const ms = Math.min(4000, t.minutes * 50);
    const t0 = performance.now();
    const step = (now: number) => {
      const f = Math.min(1, (now - t0) / ms);
      let d = (f < 0.5 ? 2 * f * f : 1 - (-2 * f + 2) ** 2 / 2) * total, i = 0;
      while (i < seg.length - 1 && d > seg[i]) { d -= seg[i]; i++; }
      const k = seg[i] ? Math.min(1, d / seg[i]) : 1;
      const at = ll(pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k);
      mk.setLatLng(at);
      if (!m.getBounds().pad(-0.15).contains(at)) m.panTo(at, { animate: false });
      if (f < 1) requestAnimationFrame(step); else done();
    };
    requestAnimationFrame(step);
  });

  const toWalk = (c: { px: number; py: number }) => ({ north: 1000 - c.py / 12, west: 1000 - c.px / 12 });
  const here = data.position?.place_id;
  const sel = selected ? data.known.find((p) => p.id === selected) : null;

  return (
    <div className={`atlas ${sel ? "has-dossier" : ""} ${listOpen ? "list-open" : ""}`}>
      <aside className="atlas-rail" aria-label="Map layers and places">
        <h3 className="rail-head">Map layers</h3>
        <div className="layers" role="radiogroup" aria-label="Show">
          {LAYERS.map((l) => (
            <button key={l.id} role="radio" aria-checked={layer === l.id} className={`layer ${layer === l.id ? "on" : ""}`} onClick={() => setLayer(l.id)}>
              <span>{l.label}</span><span className="count">{counts[l.id]}</span>
            </button>
          ))}
        </div>
        <h3 className="rail-head">Places</h3>
        <ul className="place-list">
          {[...shown].sort((a, b) => a.name.localeCompare(b.name)).map((p) => (
            <li key={p.id}>
              <button className={p.id === selected ? "on" : ""} aria-current={p.id === selected ? "true" : undefined} onClick={() => select(p, true)}>
                <i className={`lg ${withOmen.has(p.id) ? "omen" : p.safe ? "safe" : "place"}`} aria-hidden />{p.name}
                {p.id === here && <span className="here">you</span>}
              </button>
            </li>
          ))}
          {shown.length === 0 && <li className="muted small">No places on this layer.</li>}
        </ul>
        <p className="rail-note">Alt-click a road to walk to it.</p>
      </aside>
      <div className="town-frame">
        <div ref={el} className="town-map" aria-label="Town map" />
        <button className="btn small atlas-toggle" aria-expanded={listOpen} onClick={() => setListOpen((o) => !o)}>
          {listOpen ? <X size={13} aria-hidden /> : <List size={13} aria-hidden />} Places</button>
        <div className="legend" aria-hidden>
          <span><i className="lg you" /> the group</span><span><i className="lg place" /> known place</span>
          <span><i className="lg safe" /> safe place</span><span><i className="lg omen" /> Omen seen</span>
        </div>
        <Inset data={data} pan={(px, py) => map.current?.panTo(ll(px, py))} />
        {walkTo && (
          <div className="map-card" role="dialog" aria-label="Walk here">
            <button className="icon-btn close" onClick={() => setWalkTo(null)} aria-label="Close"><X size={14} /></button>
            <div className="card-name">Walk here?</div>
            <div className="row"><button className="btn" disabled={busy} onClick={() => go(toWalk(walkTo))}>{busy ? <Loader2 size={13} className="spin" /> : <Footprints size={13} />} Walk</button>
              <button className="btn ghost" onClick={() => setWalkTo(null)}>Cancel</button></div>
            {msg && <div className="card-msg">{msg}</div>}
          </div>
        )}
        {shot && <EstablishingShot shot={shot} onDone={() => { setShot(null); onChanged(); }} />}
      </div>
      {sel && <PlaceDossier cid={cid} placeId={sel.id} version={version} here={sel.id === here} busy={busy} message={msg}
        onTravel={() => go({ to: sel.id })} onClose={() => setSelected(null)} />}
    </div>
  );
}

/** The whole town at a glance from the zoom-1 tiles (2×2). Clicking pans the panel map there; you never travel from it. */
function Inset({ data, pan }: { data: PlayerMap; pan: (px: number, py: number) => void }) {
  const lvl = data.tiles.levels.find((l) => l.zoom === 1);
  if (!lvl) return null;
  const k = INSET / lvl.image_width, s = INSET / data.tiles.size;
  const tiles = [];
  for (let y = 0; y < lvl.rows; y++) for (let x = 0; x < lvl.columns; x++)
    tiles.push(<img key={`${x}-${y}`} src={data.tiles.template.replace("{z}", "1").replace("{x}", String(x)).replace("{y}", String(y))} alt=""
      style={{ left: x * 512 * k, top: y * 512 * k, width: 512 * k, height: 512 * k }} draggable={false} />);
  const click = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    pan((e.clientX - r.left) / s, (e.clientY - r.top) / s);
  };
  return (
    <div className="inset" style={{ width: INSET, height: INSET }} onClick={click} title="The whole town: click to look there">
      {tiles}
      {data.known.map((p) => <i key={p.id} className={`dot ${p.safe ? "safe" : ""}`} style={{ left: p.px * s, top: p.py * s }} />)}
      {data.position && <i className="dot you" style={{ left: data.position.px * s, top: data.position.py * s }} />}
    </div>
  );
}

/** The arrival: the place's identity image holds the screen until the GM's narration lands. */
function EstablishingShot({ shot, onDone }: { shot: Shot; onDone: () => void }) {
  const [turn, setTurn] = useState(shot.turn);
  useEffect(() => {
    if (turn.status !== "processing") return;
    let alive = true;
    const iv = setInterval(async () => {
      try { const t = await api.turn(turn.turn_id); if (alive && t.status !== "processing") setTurn(t); } catch { /* transient */ }
    }, 1200);
    return () => { alive = false; clearInterval(iv); };
  }, [turn.turn_id, turn.status]);
  const title = useMemo(() => shot.place?.name ?? "On the road", [shot.place]);
  return (
    <div className="shot" role="dialog" aria-label={`Arriving at ${title}`}>
      {shot.place?.image ? <img className="shot-art" src={shot.place.image} alt="" /> : <div className="shot-art blank" />}
      <div className="shot-veil" />
      <div className="shot-body">
        <div className="shot-title">{title}</div>
        <div className="shot-sub">{shot.travel.minutes} minutes on foot{shot.travel.interrupted ? <> · <b>{shot.travel.interrupted.description}</b></> : null}</div>
        {turn.status === "completed" && turn.result ? <div className="shot-text narration"><Markdown text={turn.result.narration} /></div>
          : turn.status === "failed" ? <div className="shot-text">The GM couldn't narrate the arrival: {turn.error}. Retry from the Scene.</div>
          : <div className="shot-text muted"><Loader2 size={14} className="spin" /> {turn.progress?.stage ?? "The GM is setting the scene"}…</div>}
        <button className="btn" onClick={onDone} disabled={turn.status === "processing"}>Continue</button>
      </div>
    </div>
  );
}
