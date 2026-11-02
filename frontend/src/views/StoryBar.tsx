import { ActiveStory } from "../api";
import "../storybar.css";

/** The running story's Plot Cycle: eight phases, past ones dimmed, the current one lit (brass Off Screen, blood On Screen).
 * The phases and the current one come from story_api.py; nothing here advances or guesses a phase. */
export function StoryBar({ story }: { story: ActiveStory | null }) {
  if (!story) return null;
  const at = story.phases.indexOf(story.phase);
  return (
    <section className={`storybar plot ${story.camera}`} aria-label="The Plot Cycle">
      <span className="plot-label">The Plot Cycle</span>
      <ol className="plot-cycle">
        {story.phases.map((p, i) => (
          <li key={p} className={i < at ? "past" : i === at ? "now" : ""} aria-current={i === at ? "step" : undefined}>{p}</li>
        ))}
      </ol>
    </section>
  );
}
