import { Check } from "lucide-react";
import { Campaign, OpeningStage } from "../api";
import "../storybar.css";

const STEPS: { stage: OpeningStage; label: string }[] = [
  { stage: "car", label: "The drive in" }, { stage: "silas", label: "Silas the Showman" }, { stage: "opening_omen", label: "The first Omen" },
  { stage: "escort", label: "The escort" }, { stage: "done", label: "Dyer's Lodge" },
];

/** The new game's five steps (the current one lit, finished ones ticked) and the lure as the group believes it.
 * Only what campaign_summary hands the player: the stage, the belief and the payoff, never the lure's truth. */
export function OpeningBar({ campaign }: { campaign: Campaign }) {
  const o = campaign.opening;
  if (!o || o.stage === "done") return null;
  const at = STEPS.findIndex((s) => s.stage === o.stage);
  return (
    <section className="storybar openingbar" aria-label="The opening">
      <ol className="plot-cycle opening-steps" aria-label="Opening steps">
        {STEPS.map((s, i) => (
          <li key={s.stage} className={i < at ? "past" : i === at ? "now" : ""} aria-current={i === at ? "step" : undefined}>
            {i < at && <Check size={10} aria-label="done" />} {s.label}
          </li>
        ))}
      </ol>
      {o.belief && <div className="storybar-cast">Why we came: {o.belief}</div>}
    </section>
  );
}
