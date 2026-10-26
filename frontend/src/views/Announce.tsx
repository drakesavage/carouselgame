import { useEffect, useState } from "react";
import { ShieldCheck, Skull, Sparkles, X } from "lucide-react";
import { Markdown } from "../ui";
import { play, reducedMotion } from "../prefs";
import "../announce.css";

export type Announcement = { id: number; text: string; style: string };
const HEAD: Record<string, string> = { achievement: "NEW ACHIEVEMENT", warning: "SYSTEM WARNING", notice: "SYSTEM ANNOUNCEMENT" };

/**
 * The System AI's theatrical announcements (achievements, level-ups, deaths, floor collapse): one at a time, over the
 * story, auto-dismissed after a few seconds; click, Esc or Skip ends it at once. It never blocks input or the turn.
 */
export function AnnouncementStage({ queue, onDone }: { queue: Announcement[]; onDone: (id: number) => void }) {
  const a = queue[0];
  const [shown, setShown] = useState<number | null>(null);
  useEffect(() => {
    if (!a) return;
    if (shown !== a.id) {
      setShown(a.id);
      play(a.style === "warning" ? "warning" : "achievement");
    }
    const t = setTimeout(() => onDone(a.id), reducedMotion() ? 4500 : 6500);
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onDone(a.id); };
    window.addEventListener("keydown", key);
    return () => { clearTimeout(t); window.removeEventListener("keydown", key); };
  }, [a?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!a) return null;
  const Icon = a.style === "warning" ? Skull : a.style === "achievement" ? Sparkles : ShieldCheck;
  return (
    <div className={`announce ${a.style}`} role="status" aria-live="assertive" onClick={() => onDone(a.id)}>
      <div className="announce-band">
        <div className="announce-head"><Icon size={18} /> <span className="announce-type">{HEAD[a.style] ?? HEAD.notice}</span>
          {queue.length > 1 && <span className="announce-more">+{queue.length - 1}</span>}
          <button className="announce-skip" onClick={(e) => { e.stopPropagation(); onDone(a.id); }} aria-label="Skip announcement"><X size={14} /> Skip</button>
        </div>
        <div className="announce-text"><Markdown text={a.text} /></div>
        <div className="announce-timer" style={{ animationDuration: reducedMotion() ? "4.5s" : "6.5s" }} />
      </div>
    </div>
  );
}
