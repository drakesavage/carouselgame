import { useEffect, useState } from "react";
import { Clapperboard } from "lucide-react";
import { ActiveStory, api } from "../api";
import "../storybar.css";

/** The running story's Plot Cycle (eight steps, the current one lit) and whether the camera is on the group.
 * Only what story_api.py hands the player: title, premise, phase, camera, cast and public revision summaries. */
export function StoryBar({ cid, version }: { cid: number; version: unknown }) {
  const [story, setStory] = useState<ActiveStory | null>(null);
  useEffect(() => { api.story(cid).then((s) => setStory(s.active)).catch(() => setStory(null)); }, [cid, version]);
  if (!story) return null;
  const at = story.phases.indexOf(story.phase);
  return (
    <section className="storybar" aria-label={`Story: ${story.title}`}>
      <div className="storybar-head">
        <span className="storybar-title" title={story.premise ?? undefined}>{story.title}</span>
        <span className={`storybar-camera ${story.camera}`} aria-label={story.camera === "on" ? "On screen" : "Off screen"}>
          <Clapperboard size={12} /> {story.camera === "on" ? "ON SCREEN" : "OFF SCREEN"}
        </span>
      </div>
      <ol className="plot-cycle" aria-label="Plot Cycle">
        {story.phases.map((p, i) => (
          <li key={p} className={i < at ? "past" : i === at ? "now" : ""} aria-current={i === at ? "step" : undefined}>{p}</li>
        ))}
      </ol>
      {story.cast.length > 0 && <div className="storybar-cast">{story.cast.map((c) => `${c.name.split(" ")[0]} (${c.casting})`).join(" · ")}</div>}
    </section>
  );
}
