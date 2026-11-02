import { useEffect, useState } from "react";
import { AlertTriangle, Clapperboard, Info, X } from "lucide-react";
import { Markdown } from "../ui";
import { play, reducedMotion } from "../prefs";
import "../announce.css";

export type Announcement = { id: number; text: string; style: string };

/** Which moments take the stage (the rest stay toasts): warnings, and the Plot Cycle moving on. */
export const THEATRICAL = new Set(["warning", "phase"]);
const KIND: Record<string, { label: string; icon: typeof Info; cue: "warning" | "notice" }> = {
  warning: { label: "Warning", icon: AlertTriangle, cue: "warning" },
  phase: { label: "The Plot Cycle", icon: Clapperboard, cue: "notice" },
  notice: { label: "Notice", icon: Info, cue: "notice" },
};

/** Long announcements stay up longer; nothing stays more than 14 seconds. */
const duration = (a: Announcement) => (reducedMotion() ? 4500 : 0) || Math.min(14000, Math.max(5000, a.text.length * 45));

/**
 * A title card over the story, one at a time. It never takes focus, so typing carries on underneath; click, Esc or Skip
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
  return (
    <div className={`announce ${a.style}`} role="status" aria-live="assertive" onClick={() => onDone(a.id)}>
      <div className="announce-band" key={a.id}>
        <div className="announce-strip">
          <k.icon size={15} aria-hidden /> <span className="announce-type">{k.label}</span>
          {queue.length > 1 && <span className="announce-more">+{queue.length - 1} more</span>}
          <button className="announce-skip" onClick={(e) => { e.stopPropagation(); onDone(a.id); }} aria-label="Skip announcement"><X size={13} /> Skip</button>
        </div>
        <div className="announce-main">
          <div className="announce-copy">
            {a.style === "phase" ? <div className="announce-title">{a.text}</div> : <div className="announce-text"><Markdown text={a.text} /></div>}
          </div>
        </div>
        <div className="announce-timer" style={{ animationDuration: `${duration(a)}ms` }} />
      </div>
    </div>
  );
}
