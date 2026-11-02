import { CSSProperties, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ text }: { text: string }) {
  return (
    <div className="narration">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

export function Panel({ title, accent, right, children, className = "" }: { title: ReactNode; accent?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel hud ${className}`}>
      <div className="panel-head">
        <span>{title}{accent ? <> <span className="accent">{accent}</span></> : null}</span>
        {right}
      </div>
      <div className="panel-body">{children}</div>
    </section>
  );
}

/** The map key of a place name, matching memory/places.py ('The Molting Pigeon' -> 'molting pigeon'). */
export function placeKey(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9 ]+/g, "").replace(/^the\s+/, "").trim();
}

/** A local picture at the width it is shown (CSS pixels): the server sends a cached, scaled-down copy (app/media.py). */
export function sized(url: string | null | undefined, cssWidth: number): string | null {
  if (!url) return null;
  if (!/^\/?media\//.test(url)) return url;
  const px = Math.ceil(cssWidth * Math.min(window.devicePixelRatio || 1, 2));
  return `${url}${url.includes("?") ? "&" : "?"}w=${[64, 128, 256, 512, 1024].find((w) => w >= px) ?? 1024}`;
}

/** Local artwork from /media/portraits when present; otherwise a generated HUD placeholder (never a network image). */
export type PortraitCrop = { cx: number; cy: number; k: number; ar: number };

/** CSS background placement that shows `crop` (a square of the image) filling a square box. */
export function cropStyle(url: string, crop: PortraitCrop): CSSProperties {
  // Image height = 1, width = ar; the crop side is k x the shorter side. Background size is the image in box units.
  const side = crop.k * Math.min(crop.ar, 1);
  const wF = crop.ar / side, hF = 1 / side;
  const pos = (c: number, f: number) => (f <= 1 ? 50 : Math.min(100, Math.max(0, ((c * f - 0.5) / (f - 1)) * 100)));
  return { backgroundImage: `url("${url}")`, backgroundSize: `${wF * 100}% ${hF * 100}%`,
    backgroundPosition: `${pos(crop.cx, wF)}% ${pos(crop.cy, hF)}%`, backgroundRepeat: "no-repeat" };
}

export function Portrait({ name, path, src, size = 52, crop }: { name: string; path?: string | null; src?: string | null; size?: number; crop?: PortraitCrop | null }) {
  const style = { width: size, height: size };
  const url = src ?? (path ? `media/portraits/${encodeURIComponent(path)}` : null);
  // a crop shows part of the picture enlarged: ask for the width the whole picture is drawn at
  if (url && crop) return <div className="portrait" role="img" aria-label={name} style={{ ...style, ...cropStyle(sized(url, size * crop.ar / (crop.k * Math.min(crop.ar, 1)))!, crop) }} />;
  // Default framing: tall pictures show their top (where the face usually is), not the middle.
  if (url) return <div className="portrait" style={style}><img src={sized(url, size)!} alt={name} loading="lazy" style={{ objectPosition: "50% 12%" }} /></div>;
  // no picture: the first initial on a parchment square
  const initial = name.replace(/\(.*?\)/g, "").trim().charAt(0).toUpperCase();
  return (
    <div className="portrait portrait-initial" role="img" style={{ ...style, fontSize: Math.round(size * 0.46) }} aria-label={`${name} (no portrait)`}>
      <span aria-hidden>{initial || "?"}</span>
    </div>
  );
}

export function Bar({ kind, cur, max, label }: { kind: "hp" | "mana"; cur?: number | null; max?: number | null; label?: string }) {
  const known = cur != null && max != null && max > 0;
  const pct = known ? Math.max(0, Math.min(100, (cur! / max!) * 100)) : 0;
  return (
    <div className="barrow">
      <span>{label ?? (kind === "hp" ? "HP" : "MP")}</span>
      <div className={`bar ${kind} ${known ? "" : "unknown"} ${kind === "hp" && known && pct <= 30 ? "low" : ""}`}><i style={{ width: `${pct}%` }} /></div>
      <span>{cur ?? "?"}/{max ?? "?"}</span>
    </div>
  );
}

export function Chip({ children, tone, title }: { children: ReactNode; tone?: string; title?: string }) {
  return <span className={`chip ${tone ?? ""}`} title={title}>{children}</span>;
}

const STAT_KEYS = ["strength", "intelligence", "constitution", "dexterity", "charisma"];
export const STAT_ABBR: Record<string, string> = { strength: "STR", intelligence: "INT", constitution: "CON", dexterity: "DEX", charisma: "CHA" };
export { STAT_KEYS };

export const humanize = (k: string) => k.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/** Generic, read-only renderer for imported sheet sections (YAML-shaped JSON). */
export function Tree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value == null) return <span className="muted">—</span>;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">none</span>;
    if (value.every((v) => typeof v !== "object")) return <ul>{value.map((v, i) => <li key={i}>{String(v)}</li>)}</ul>;
    return <ul>{value.map((v, i) => <li key={i}><Tree value={v} depth={depth + 1} /></li>)}</ul>;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  return (
    <div className="tree">
      {entries.map(([k, v]) => typeof v === "object" && v !== null ? (
        <details key={k} open={depth < 1 && entries.length < 6}>
          <summary><span className="tk">{humanize(k)}</span></summary>
          <div style={{ paddingLeft: 14 }}><Tree value={v} depth={depth + 1} /></div>
        </details>
      ) : (
        <div key={k}><span className="tk">{humanize(k)}:</span> <Tree value={v} depth={depth + 1} /></div>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
