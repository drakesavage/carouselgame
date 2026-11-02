import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { ActiveStory, api, Campaign, PlayerVenue } from "../api";
import { ArtQueue } from "./Art";

/** The scene's right rail: the feature card, production notes and the story's public revisions.
 * Only what the player API hands over: no poster art exists yet, so a running story gets a typographic title card. */
export function SceneRail({ campaign, story, onArt, openBehindTheScenes }: {
  campaign: Campaign; story: ActiveStory | null; onArt?: () => void; openBehindTheScenes?: () => void;
}) {
  const [venue, setVenue] = useState<PlayerVenue | null>(null);
  useEffect(() => { api.venue(campaign.id).then(setVenue).catch(() => setVenue(null)); }, [campaign]);
  const clock = campaign.clock;
  return (
    <>
      <section className="feature-card" aria-label="Feature">
        <div className="feature-kicker">Carousel presents</div>
        {story ? (
          <>
            <h2 className="feature-title">{story.title}</h2>
            {story.premise && <p className="feature-premise">{story.premise}</p>}
            {story.cast.length > 0 && (
              <ul className="feature-cast" aria-label="Cast">
                {story.cast.map((c) => <li key={c.id}><span>{c.name}</span> — <i>{c.casting}</i></li>)}
              </ul>
            )}
          </>
        ) : (
          <>
            <h2 className="feature-title unknown">Feature unknown</h2>
            <p className="feature-premise">No confirmed storyline</p>
          </>
        )}
      </section>
      <section className="rail-section" aria-label="Production notes">
        <h3 className="rail-head">Production notes</h3>
        <dl className="notes">
          <dt>Camera</dt>
          <dd className={story?.camera === "on" ? "rec" : ""}>{story ? (story.camera === "on" ? "On screen" : "Off screen") : "No story running"}</dd>
          <dt>Plot Cycle</dt><dd>{story ? story.phase : "—"}</dd>
          <dt>Storyline</dt><dd>{story ? story.title : "Unconfirmed"}</dd>
          <dt>Location</dt><dd>{campaign.current_location ?? "Unknown"}{venue ? ` · ${venue.zone.label}` : ""}</dd>
          <dt>Time</dt><dd>{clock ? `Day ${clock.day} · ${clock.time} · ${clock.slot}` : "—"}</dd>
        </dl>
      </section>
      {story && story.revisions.length > 0 && (
        <section className="rail-section" aria-label="Revisions">
          <h3 className="rail-head">Revisions</h3>
          <ul className="revisions">{[...story.revisions].reverse().map((r, i) => <li key={i}>{r}</li>)}</ul>
        </section>
      )}
      <ArtQueue cid={campaign.id} onChanged={onArt} />
      {openBehindTheScenes && (
        <button className="link-btn rail-link" onClick={openBehindTheScenes}>Behind the Scenes <ChevronRight size={12} aria-hidden /></button>
      )}
    </>
  );
}
