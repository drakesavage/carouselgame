import { useEffect, useState } from "react";
import { api, MissingPoster } from "../api";
import "../people.css";

/** The missing-poster board outside the Diner. Empty until the group has been there; the server sends only the poster's
 * face (name, Plot Armor, Archetype, where they were last seen), never how they died. */
export function PosterBoard({ cid, version }: { cid: number; version: unknown }) {
  const [posters, setPosters] = useState<MissingPoster[]>([]);
  useEffect(() => { api.posters(cid).then(setPosters).catch(() => setPosters([])); }, [cid, version]);
  if (!posters.length) return null;
  return (
    <section aria-label="Missing-poster board">
      <h2 className="h2">The board outside the Diner <span className="muted">{posters.length}</span></h2>
      <div className="poster-grid">
        {posters.map((p) => (
          <article key={p.id} className={`poster ${p.found ? "found" : ""}`}>
            <div className="poster-head">MISSING</div>
            <div className="poster-name">{p.name}</div>
            <dl>
              <dt>Plot Armor</dt><dd>{p.plot_armor ?? "?"}</dd>
              <dt>Archetype</dt><dd>{p.archetype ?? "unknown"}</dd>
              <dt>Last seen</dt><dd>{p.last_seen ?? "unknown"}</dd>
            </dl>
            {p.found ? <div className="poster-note">Found</div> : p.held_by ? <div className="poster-note">Taken by {p.held_by}</div> : null}
          </article>
        ))}
      </div>
    </section>
  );
}
