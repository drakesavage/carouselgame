import { useState } from "react";
import { Loader2, MapPin, Sparkles } from "lucide-react";
import { api, Campaign } from "../api";
import { Portrait, sized } from "../ui";
import { ArtButton } from "./Art";
import { portraitSrc, useManifest } from "./Hud";

/** Location art is keyed like memory/places.py key(): lower case, punctuation and a leading "the" dropped. */
const artKey = (name: string) => name.toLowerCase().replace(/[^a-z0-9 ]+/g, "").replace(/^the\s+/, "").trim();

/** Every picture in the campaign in one place, with the same paint and upload controls as where they're used. */
export function ArtLibrary({ cid, campaign, places, onChanged, openCharacter }: {
  cid: number; campaign: Campaign; places: { id: string; name: string }[]; onChanged: () => void; openCharacter: (id: number) => void;
}) {
  const m = useManifest();
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
          <div className="lib-grid wide">{places.map((p) => { const k = artKey(p.name); return (
            <div key={p.id} className="lib-tile">
              <div className="lib-pic wide">{art.location?.[k] ? <img src={sized(art.location[k], 256)!} alt="" loading="lazy" /> : <MapPin size={22} />}</div>
              <div className="lib-name">{p.name}</div>
              <ArtButton cid={cid} kind="location" subjectName={p.name} label="Paint" onChanged={onChanged} />
            </div>); })}
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
      <div className="sub">Item pictures ({items}) live with their items; scene pictures appear in the establishing shot. Nothing is painted without your click.</div>
    </div>
  );
}
