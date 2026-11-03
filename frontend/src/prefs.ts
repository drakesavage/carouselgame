/**
 * Per-device display and sound preferences (roadmap M8): kept in this browser only, never on the server.
 * Sound is off until turned on; motion follows the system setting unless overridden; announcements can be theatrical,
 * simple toasts, or off. Sounds are synthesized (WebAudio): no audio files are downloaded.
 */
import { useEffect, useState } from "react";

export type Prefs = { sound: boolean; volume: number; motion: "system" | "reduce" | "full"; announcements: "theatrical" | "simple" | "off" };
const DEFAULTS: Prefs = { sound: false, volume: 0.5, motion: "system", announcements: "theatrical" };
const KEY = "device-prefs";
const listeners = new Set<(p: Prefs) => void>();

export function getPrefs(): Prefs {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") }; } catch { return DEFAULTS; }
}

export function setPrefs(patch: Partial<Prefs>): void {
  const next = { ...getPrefs(), ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
  applyMotion(next);
  listeners.forEach((l) => l(next));
}

export function usePrefs(): [Prefs, (patch: Partial<Prefs>) => void] {
  const [p, setP] = useState(getPrefs);
  useEffect(() => { listeners.add(setP); return () => { listeners.delete(setP); }; }, []);
  return [p, setPrefs];
}

/** True when animations should be skipped: the device preference, else the system's reduced-motion setting. */
export function reducedMotion(p: Prefs = getPrefs()): boolean {
  if (p.motion !== "system") return p.motion === "reduce";
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export function applyMotion(p: Prefs = getPrefs()): void {
  document.documentElement.dataset.motion = p.motion === "system" ? "" : p.motion;
}

// ------------------------------------------------------------------ sound
type Cue = "dice" | "warning" | "notice";
const CUES: Record<Cue, { notes: number[]; step: number; type: OscillatorType; len: number }> = {
  dice: { notes: [520, 390, 610], step: 0.045, type: "triangle", len: 0.07 },
  warning: { notes: [220, 196], step: 0.16, type: "sawtooth", len: 0.22 },
  notice: { notes: [880, 1175], step: 0.07, type: "sine", len: 0.12 },
};
let ctx: AudioContext | null = null;

export function play(cue: Cue): void {
  const p = getPrefs();
  if (!p.sound || p.volume <= 0) return;
  try {
    ctx ??= new AudioContext();
    const c = CUES[cue];
    const t0 = ctx.currentTime + 0.01;
    c.notes.forEach((f, i) => {
      const osc = ctx!.createOscillator();
      const gain = ctx!.createGain();
      osc.type = c.type;
      osc.frequency.value = f;
      const at = t0 + i * c.step;
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.12 * p.volume, at + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + c.len);
      osc.connect(gain).connect(ctx!.destination);
      osc.start(at);
      osc.stop(at + c.len + 0.02);
    });
  } catch { /* audio unavailable */ }
}
