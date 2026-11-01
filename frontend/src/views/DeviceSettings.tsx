import { Volume2, VolumeX } from "lucide-react";
import "../device.css";
import { Panel } from "../ui";
import { play, usePrefs } from "../prefs";

/** This browser's display and sound preferences (prefs.ts); other devices keep their own. */
export function DeviceSettings() {
  const [p, set] = usePrefs();
  return (
    <Panel title="This device: display & sound">
      <div className="device-prefs">
        <label className="dp-row">
          <span className="dp-k">Sound</span>
          <button className={`btn small ${p.sound ? "" : "ghost"}`} aria-pressed={p.sound} onClick={() => { set({ sound: !p.sound }); if (!p.sound) setTimeout(() => play("notice"), 50); }}>
            {p.sound ? <Volume2 size={13} /> : <VolumeX size={13} />} {p.sound ? "On" : "Muted"}
          </button>
          <input type="range" min={0} max={1} step={0.05} value={p.volume} disabled={!p.sound} aria-label="Volume"
            onChange={(e) => set({ volume: Number(e.target.value) })} onMouseUp={() => play("dice")} />
        </label>
        <label className="dp-row">
          <span className="dp-k">Motion</span>
          <select className="select" value={p.motion} onChange={(e) => set({ motion: e.target.value as typeof p.motion })}>
            <option value="system">Follow the system setting</option>
            <option value="reduce">Reduce (no animations)</option>
            <option value="full">Full animations</option>
          </select>
        </label>
        <label className="dp-row">
          <span className="dp-k">Announcements</span>
          <select className="select" value={p.announcements} onChange={(e) => set({ announcements: e.target.value as typeof p.announcements })}>
            <option value="theatrical">Theatrical (achievements and warnings take the stage; skippable)</option>
            <option value="simple">Simple toasts</option>
            <option value="off">Off (they stay in the story)</option>
          </select>
        </label>
        <div className="sub">Kept in this browser only. Sounds are made on the fly; nothing is downloaded.</div>
      </div>
    </Panel>
  );
}
