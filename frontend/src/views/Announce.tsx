import { useEffect, useState } from "react";
import { ArrowBigUpDash, Gift, Megaphone, ShieldCheck, Skull, Trophy, X } from "lucide-react";
import { Markdown } from "../ui";
import { play, reducedMotion } from "../prefs";
import "../announce.css";

export type Announcement = { id: number; text: string; style: string };

/** Which broadcast moments take the stage (the rest stay toasts) and how each one looks and sounds. */
export const THEATRICAL = new Set(["achievement", "warning", "broadcast", "loot", "levelup", "critfail"]);
const KIND: Record<string, { label: string; icon: typeof Trophy; cue: "achievement" | "warning" | "notice" }> = {
  achievement: { label: "New achievement!", icon: Trophy, cue: "achievement" },
  broadcast: { label: "Borant broadcast", icon: Megaphone, cue: "notice" },
  loot: { label: "Loot!", icon: Gift, cue: "achievement" },
  levelup: { label: "Level up!", icon: ArrowBigUpDash, cue: "achievement" },
  critfail: { label: "Critical failure", icon: Skull, cue: "warning" },
  warning: { label: "System warning", icon: Skull, cue: "warning" },
  notice: { label: "System announcement", icon: ShieldCheck, cue: "notice" },
};
const TIER = /\b(Bronze|Silver|Gold|Platinum|Legendary|Celestial)\b/i;

/** The headline of a moment: an achievement's title, a box's name, a level change; the rest is the body. */
function split(a: Announcement): { title: string | null; body: string } {
  const lines = a.text.trim().split("\n");
  const first = lines[0].replace(/\*\*/g, "").trim();
  if (a.style === "achievement") {
    const m = first.match(/new achievement!?\s*(.+)$/i);
    if (m) return { title: m[1].replace(/\s*\([^)]*\)\s*(—.*)?$/, "").trim(), body: lines.slice(1).join("\n").trim() };
  }
  if (a.style === "loot") {
    const m = first.match(/opens the (.+)$/i);
    if (m) return { title: m[1], body: first.replace(/ opens the .+$/i, " cracks it open.") };
  }
  if (a.style === "levelup") {
    const m = first.match(/^(.+?) Level (\d+) → (\d+)/);
    if (m) return { title: `Level ${m[3]}`, body: `${m[1]} goes from Level ${m[2]} to Level ${m[3]}.` };
  }
  return { title: null, body: a.text };
}

/** Long announcements stay up longer; nothing stays more than 14 seconds. */
const duration = (a: Announcement) => (reducedMotion() ? 4500 : 0) || Math.min(14000, Math.max(a.style === "critfail" ? 4500 : 6500, a.text.length * 45));

/**
 * The broadcast moments: achievements, Borant's announcements, loot reveals, level-ups, critical failures and System
 * warnings, one at a time over the story. They never take focus, so typing carries on underneath; click, Esc or Skip
 * ends one at once, and each leaves by itself after a few seconds.
 */
export function AnnouncementStage({ queue, onDone }: { queue: Announcement[]; onDone: (id: number) => void }) {
  const a = queue[0];
  const [shown, setShown] = useState<number | null>(null);
  useEffect(() => {
    if (!a) return;
    if (shown !== a.id) {
      setShown(a.id);
      play((KIND[a.style] ?? KIND.notice).cue);
    }
    const t = setTimeout(() => onDone(a.id), duration(a));
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onDone(a.id); };
    window.addEventListener("keydown", key);
    return () => { clearTimeout(t); window.removeEventListener("keydown", key); };
  }, [a?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!a) return null;
  const k = KIND[a.style] ?? KIND.notice;
  const { title, body } = split(a);
  const tier = a.style === "loot" ? (a.text.match(TIER)?.[1] ?? "").toLowerCase() : "";
  return (
    <div className={`announce ${a.style} ${tier ? `tier-${tier}` : ""}`} role="status" aria-live="assertive" onClick={() => onDone(a.id)}>
      <div className="announce-band" key={a.id}>
        <div className="announce-strip">
          <k.icon size={16} aria-hidden /> <span className="announce-type">{k.label}</span>
          {queue.length > 1 && <span className="announce-more">+{queue.length - 1} more</span>}
          <button className="announce-skip" onClick={(e) => { e.stopPropagation(); onDone(a.id); }} aria-label="Skip announcement"><X size={13} /> Skip</button>
        </div>
        <div className="announce-main">
          {(a.style === "loot" || a.style === "levelup") && <div className="announce-emblem" aria-hidden><k.icon size={40} /></div>}
          <div className="announce-copy">
            {title && <div className="announce-title">{title}</div>}
            {body && <div className="announce-text"><Markdown text={body} /></div>}
          </div>
        </div>
        <div className="announce-timer" style={{ animationDuration: `${duration(a)}ms` }} />
      </div>
    </div>
  );
}
