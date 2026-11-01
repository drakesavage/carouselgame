import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Footprints, Image as ImageIcon, Loader2, Map as MapIcon, ShieldCheck, X } from "lucide-react";
import { api, ApiError, Campaign, MapPlace, newKey, PlayerMap, TravelResult, TurnStatus } from "../api";
import { Markdown } from "../ui";
import { ArtLibrary } from "./ArtLibrary";
import "../map.css";

/**
 * The town on the illustrated panel map. Master pixels (12000²) are Leaflet CRS.Simple units with y negated; Leaflet
 * zoom 0 is the master, so tile level = zoom + the top level (zoomOffset) and only the manifest's levels are requested.
 */
const ll = (px: number, py: number) => L.latLng(-py, px);
const INSET = 220;

type Card = { kind: "place"; place: MapPlace } | { kind: "walk"; px: number; py: number };
type Shot = { travel: TravelResult; place: MapPlace | null; turn: TurnStatus };

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
      <div className="h1">Map</div>
      <nav className="tabs" role="tablist" aria-label="Map sections">
        <button role="tab" aria-selected={tab === "map"} className={tab === "map" ? "active" : ""} onClick={() => setTab("map")}><MapIcon size={13} /> Town</button>
        <button role="tab" aria-selected={tab === "art"} className={tab === "art" ? "active" : ""} onClick={() => setTab("art")}><ImageIcon size={13} /> Art library</button>
      </nav>
      {error && <div className="sub">{error}</div>}
      {tab === "map" && (data ? <TownMap cid={cid} data={data} reload={reload} onChanged={onChanged} /> : !error && <div className="muted">Loading…</div>)}
      {tab === "art" && <ArtLibrary cid={cid} campaign={campaign} places={data?.known ?? []} onChanged={onChanged} openCharacter={openCharacter} />}
    </div>
  );
}

function TownMap({ cid, data, reload, onChanged }: { cid: number; data: PlayerMap; reload: () => Promise<PlayerMap | null>; onChanged: () => void }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const pins = useRef<L.LayerGroup | null>(null);
  const party = useRef<L.Marker | null>(null);
  const route = useRef<L.Polyline | null>(null);
  const [card, setCard] = useState<Card | null>(null);
  const [cardMsg, setCardMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shot, setShot] = useState<Shot | null>(null);
  const top = data.tiles.levels.length ? data.tiles.levels[data.tiles.levels.length - 1].zoom : 5;
  const bottom = data.tiles.levels.length ? data.tiles.levels[0].zoom : 0;

  // the map itself, once
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
    m.on("click", (e: L.LeafletMouseEvent) => {
      if (!(e.originalEvent as MouseEvent).altKey) return;
      setCardMsg(null);
      setCard({ kind: "walk", px: e.latlng.lng, py: -e.latlng.lat });
    });
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, []);

  // pins, party marker and the last walk
  useEffect(() => {
    const m = map.current, g = pins.current;
    if (!m || !g) return;
    g.clearLayers();
    for (const p of data.known) {
      L.circleMarker(ll(p.px, p.py), { radius: p.safe ? 8 : 6, className: `pin ${p.safe ? "safe" : ""}`, weight: 2 })
        .bindTooltip(p.name, { direction: "top", offset: [0, -6] })
        .on("click", () => { setCardMsg(null); setCard({ kind: "place", place: p }); })
        .addTo(g);
    }
    for (const o of data.omens) {
      L.marker(ll(o.px, o.py), { icon: L.divIcon({ className: "omen-pin", html: "<span>!</span>", iconSize: [22, 22] }), keyboard: false })
        .bindTooltip(o.description, { direction: "top" }).addTo(g);
    }
    if (data.position) {
      const at = ll(data.position.px, data.position.py);
      if (!party.current) party.current = L.marker(at, { icon: L.divIcon({ className: "party-pin", iconSize: [18, 18] }), zIndexOffset: 1000 }).addTo(m);
      else if (!shot) party.current.setLatLng(at);
      party.current.bindTooltip(`You: ${data.position.label ?? ""}`);
    }
    route.current?.remove();
    if (data.last_route?.geometry_px.length) {
      route.current = L.polyline(data.last_route.geometry_px.map(([x, y]) => ll(x, y)), { className: "walked", weight: 3, dashArray: "6 6", interactive: false }).addTo(m);
    }
  }, [data]);

  const go = async (to: { to: string } | { north: number; west: number }) => {
    setBusy(true); setCardMsg(null);
    try {
      const r = await api.travel(cid, to, newKey());
      setCard(null);
      await walk(r.travel);
      const fresh = await reload();
      const place = fresh?.known.find((p) => p.id === r.travel.arrived) ?? null;
      setShot({ travel: r.travel, place, turn: r.turn });
    } catch (e) {
      setCardMsg(e instanceof ApiError && e.status === 409 ? "The GM is still resolving a turn." : e instanceof ApiError ? e.message : String(e));
    } finally { setBusy(false); }
  };

  /** Move the party marker along the route (the narration is already being written meanwhile). */
  const walk = (t: TravelResult) => new Promise<void>((done) => {
    const pts = t.geometry_px, mk = party.current, m = map.current;
    if (!mk || !m || pts.length < 2) return done();
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

  const toWalk = (c: Extract<Card, { kind: "walk" }>) => ({ north: 1000 - c.py / 12, west: 1000 - c.px / 12 });
  const here = data.position?.place_id;

  return (
    <div className="town">
      <div className="legend">
        <span><i className="lg party" /> you</span><span><i className="lg pin" /> known place</span><span><i className="lg pin safe" /> safe place</span>
        <span><i className="lg omen" /> something wrong</span><span className="muted">Click a place to go there; Alt-click a road to walk to it.</span>
      </div>
      <div className="town-frame">
        <div ref={el} className="town-map" aria-label="Town map" />
        <Inset data={data} pan={(px, py) => map.current?.panTo(ll(px, py))} />
        {card && (
          <div className="map-card" role="dialog" aria-label={card.kind === "place" ? card.place.name : "Walk here"}>
            <button className="icon-btn close" onClick={() => setCard(null)} aria-label="Close"><X size={14} /></button>
            {card.kind === "place" ? (<>
              {card.place.image && <img className="card-art" src={card.place.image} alt="" loading="lazy" />}
              <div className="card-name">{card.place.name}{card.place.safe && <span className="safe-tag"><ShieldCheck size={12} /> safe</span>}</div>
              {card.place.category && <div className="muted small">{card.place.category}</div>}
              {card.place.id === here ? <div className="muted small">You are here.</div>
                : <button className="btn" disabled={busy} onClick={() => go({ to: card.place.id })}>{busy ? <Loader2 size={13} className="spin" /> : <Footprints size={13} />} Go here</button>}
            </>) : (<>
              <div className="card-name">Walk here?</div>
              <div className="row"><button className="btn" disabled={busy} onClick={() => go(toWalk(card))}>{busy ? <Loader2 size={13} className="spin" /> : <Footprints size={13} />} Walk</button>
                <button className="btn ghost" onClick={() => setCard(null)}>Cancel</button></div>
            </>)}
            {cardMsg && <div className="card-msg">{cardMsg}</div>}
          </div>
        )}
        {shot && <EstablishingShot shot={shot} onDone={() => { setShot(null); onChanged(); }} />}
      </div>
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
          : turn.status === "failed" ? <div className="shot-text">The GM couldn't narrate the arrival: {turn.error}. Retry from the Story tab.</div>
          : <div className="shot-text muted"><Loader2 size={14} className="spin" /> {turn.progress?.stage ?? "The GM is setting the scene"}…</div>}
        <button className="btn" onClick={onDone} disabled={turn.status === "processing"}>Continue</button>
      </div>
    </div>
  );
}
