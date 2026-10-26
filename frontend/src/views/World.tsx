import { useEffect, useState } from "react";
import { CloudFog, EyeOff, Footprints, Image as ImageIcon, Loader2, Map as MapIcon, MapPin, Route, ShieldCheck, Sparkles, Users } from "lucide-react";
import { api, Campaign, KnownWorld, Place } from "../api";
import { Chip, Empty, Portrait, sized } from "../ui";
import { ArtButton } from "./Art";
import { portraitSrc, useManifest } from "./Hud";
import "../world.css";

export function WorldView({ cid, campaign, version, onChanged, openCharacter }: {
  cid: number; campaign: Campaign; version: number; onChanged: () => void; openCharacter: (id: number) => void;
}) {
  const [tab, setTab] = useState<"map" | "art">("map");
  const [world, setWorld] = useState<KnownWorld | null>(null);
  useEffect(() => { api.places(cid).then(setWorld).catch(() => setWorld(null)); }, [cid, version]);
  return (
    <div className="view-inner world">
      <div className="h1">World</div>
      <nav className="tabs" role="tablist" aria-label="World sections">
        <button role="tab" aria-selected={tab === "map"} className={tab === "map" ? "active" : ""} onClick={() => setTab("map")}><MapIcon size={13} /> Known world</button>
        <button role="tab" aria-selected={tab === "art"} className={tab === "art" ? "active" : ""} onClick={() => setTab("art")}><ImageIcon size={13} /> Art library</button>
      </nav>
      {tab === "map" && (!world ? <div className="muted">Loading…</div> : <KnownWorldMap cid={cid} campaign={campaign} world={world} onChanged={onChanged} />)}
      {tab === "art" && <ArtLibrary cid={cid} campaign={campaign} world={world} onChanged={onChanged} openCharacter={openCharacter} />}
    </div>
  );
}

/**
 * The places the party knows, nested the way their names nest, with saferooms, who is where, routes actually travelled
 * and fog for the rest of each district. A schematic, not a tactical map: it draws no distances it doesn't know.
 */
function KnownWorldMap({ cid, campaign, world, onChanged }: { cid: number; campaign: Campaign; world: KnownWorld; onChanged: () => void }) {
  const here = Object.values(world.nodes).find((n) => n.current);
  const current = world.roots.filter((k) => (world.nodes[k].floor ?? world.floor) === world.floor);
  const earlier = world.roots.filter((k) => !current.includes(k));
  return (
    <>
      <div className="sub" style={{ margin: "10px 0" }}>
        Floor {world.floor ?? "?"}: only places the party has been to or knows of. {here ? <>You are at <b>{here.name}</b>.</> : null}
      </div>
      <div className="legend">
        <span><span className="dot here" /> You are here</span><span><ShieldCheck size={12} /> Saferoom</span>
        <span><EyeOff size={12} /> Known, not entered</span><span><CloudFog size={12} /> Unexplored</span>
      </div>
      {current.length === 0 && <Empty>No places recorded on this floor yet.</Empty>}
      <div className="regions">{current.map((k) => <PlaceNode key={k} k={k} world={world} cid={cid} campaign={campaign} onChanged={onChanged} depth={0} />)}</div>
      {(world.routes.length > 0 || world.unplaced_groups.length > 0) && (
        <div className="map-notes">
          {world.routes.length > 0 && (
            <section><div className="h2"><Route size={14} /> Routes travelled</div>
              <ul>{world.routes.map((r) => <li key={r.a + r.b}>{r.a} ↔ {r.b}{r.times > 1 ? <span className="muted"> · {r.times} times</span> : null}</li>)}</ul>
            </section>
          )}
          {world.unplaced_groups.length > 0 && (
            <section><div className="h2"><Users size={14} /> Position not recorded</div>
              <ul>{world.unplaced_groups.map((g) => <li key={g.key}>{g.label}</li>)}</ul>
            </section>
          )}
        </div>
      )}
      {earlier.length > 0 && <div className="sub">Earlier floors (no longer reachable): {earlier.map((k) => world.nodes[k].name).join(", ")}</div>}
    </>
  );
}

function PlaceNode({ k, world, cid, campaign, onChanged, depth }: { k: string; world: KnownWorld; cid: number; campaign: Campaign; onChanged: () => void; depth: number }) {
  const n = world.nodes[k];
  const art = campaign.art?.location?.[k];
  const kids = n.children.map((c) => world.nodes[c]);
  const cls = `place ${kids.length ? "region" : "spot"} ${n.current ? "current" : ""} ${n.visited ? "" : "unvisited"} ${n.saferoom ? "safe" : ""} depth-${Math.min(depth, 3)}`;
  return (
    <article className={cls} aria-label={n.name}>
      <header className="place-head">
        {art ? <img className="place-art" src={sized(art, kids.length ? 256 : 128)!} alt="" loading="lazy" /> : null}
        <div className="place-title">
          <div className="place-name">{n.current && <span className="dot here" title="You are here" />}{n.name}</div>
          <div className="chips">
            {n.saferoom && <Chip tone="green"><ShieldCheck size={11} /> saferoom</Chip>}
            {!n.visited && <Chip tone="amber"><EyeOff size={11} /> known, not entered</Chip>}
            {n.groups.map((g) => <Chip key={g.key} tone="cyan"><Footprints size={11} /> {g.label} ({g.members})</Chip>)}
          </div>
          {n.description && <div className="place-desc">{n.description}</div>}
        </div>
        <span className="place-paint"><ArtButton cid={cid} kind="location" subjectName={n.name} label="" onChanged={onChanged} /></span>
      </header>
      {kids.length > 0 && (
        <div className="place-kids">
          {kids.map((c) => <PlaceNode key={c.key} k={c.key} world={world} cid={cid} campaign={campaign} onChanged={onChanged} depth={depth + 1} />)}
          <div className="fog" title="Parts of this district the party hasn't explored"><CloudFog size={16} /> Unexplored</div>
        </div>
      )}
    </article>
  );
}

/** Every picture in the campaign in one place, with the same paint and upload controls as where they're used. */
function ArtLibrary({ cid, campaign, world, onChanged, openCharacter }: { cid: number; campaign: Campaign; world: KnownWorld | null; onChanged: () => void; openCharacter: (id: number) => void }) {
  const m = useManifest();
  const places: Place[] = world ? Object.values(world.nodes) : [];
  const art = campaign.art ?? { character: {}, item: {}, mob: {}, scene: {}, location: {} };
  const mobs = Object.entries(art.mob ?? {});
  const [drafting, setDrafting] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const draft = async () => {
    setDrafting(true); setMsg(null);
    try {
      const r = await api.artDraftPlaceLooks(cid);
      setMsg(r.drafted.length ? `Drafted looks for ${r.drafted.length} place${r.drafted.length > 1 ? "s" : ""}. Review or edit them in each place's Paint dialog.` : "Every place already has a look.");
    } catch (e) { setMsg(String(e)); } finally { setDrafting(false); }
  };
  const items = Object.keys(art.item ?? {}).length;
  return (
    <div className="art-library">
      <section>
        <div className="h2">Characters <span className="muted">{campaign.roster.length}</span></div>
        <div className="lib-grid">{campaign.roster.map((c) => (
          <div key={c.id} className="lib-tile">
            <button className="lib-pic" onClick={() => openCharacter(c.id)} aria-label={`${c.name}: open dossier`}><Portrait name={c.name} src={portraitSrc(c, m)} crop={c.portrait_crop} size={96} /></button>
            <div className="lib-name">{c.name}</div>
            <ArtButton cid={cid} kind="character" subjectId={c.id} subjectName={c.name} label="Paint" onChanged={onChanged} />
          </div>))}
        </div>
      </section>
      <section>
        <div className="row"><div className="h2 grow"><MapPin size={14} /> Places <span className="muted">{places.length}</span></div>
          <button className="btn small ghost" disabled={drafting} onClick={draft} title="Write what each known place looks like (and what not to draw), for place pictures; art only">
            {drafting ? <Loader2 size={12} className="spin" /> : <Sparkles size={12} />} Draft place looks</button></div>
        {msg && <div className="sub">{msg}</div>}
        {places.length === 0 ? <div className="muted">No known places yet.</div> : (
          <div className="lib-grid wide">{places.map((p) => (
            <div key={p.key} className="lib-tile">
              <div className="lib-pic wide">{art.location?.[p.key] ? <img src={sized(art.location[p.key], 256)!} alt="" loading="lazy" /> : <MapPin size={22} />}</div>
              <div className="lib-name">{p.name}{!p.visited && <span className="muted"> · not entered</span>}</div>
              <ArtButton cid={cid} kind="location" subjectName={p.name} label="Paint" onChanged={onChanged} />
            </div>))}
          </div>
        )}
      </section>
      {mobs.length > 0 && (
        <section>
          <div className="h2">Mobs <span className="muted">{mobs.length}</span></div>
          <div className="lib-grid">{mobs.map(([name, url]) => (
            <div key={name} className="lib-tile"><div className="lib-pic"><img src={sized(url, 128)!} alt="" loading="lazy" /></div><div className="lib-name">{name}</div>
              <ArtButton cid={cid} kind="mob" subjectName={name} label="Paint" onChanged={onChanged} /></div>))}
          </div>
        </section>
      )}
      <div className="sub">Item pictures ({items}) live with their items in Inventory; scene pictures appear in the story's scene header. Nothing is painted without your click.</div>
    </div>
  );
}
