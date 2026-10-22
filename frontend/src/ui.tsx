import { ReactNode } from "react";
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

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Local artwork from /media/portraits when present; otherwise a generated HUD placeholder (never a network image). */
export function Portrait({ name, path, size = 52 }: { name: string; path?: string | null; size?: number }) {
  const style = { width: size, height: size };
  if (path) return <div className="portrait" style={style}><img src={`/media/portraits/${encodeURIComponent(path)}`} alt={name} /></div>;
  const h = hash(name);
  const hue = h % 360;
  const initials = name.replace(/\(.*?\)/g, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
  const cells = Array.from({ length: 16 }, (_, i) => (h >> i) & 1);
  return (
    <div className="portrait" style={style} aria-label={`${name} (no portrait)`}>
      <svg viewBox="0 0 64 64">
        <defs>
          <linearGradient id={`g${h}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={`hsl(${hue} 60% 22%)`} />
            <stop offset="1" stopColor={`hsl(${(hue + 60) % 360} 70% 9%)`} />
          </linearGradient>
        </defs>
        <rect width="64" height="64" fill={`url(#g${h})`} />
        {cells.map((on, i) => on ? <rect key={i} x={(i % 4) * 16 + 6} y={Math.floor(i / 4) * 16 + 6} width="4" height="4" fill={`hsl(${hue} 90% 70% / 0.18)`} /> : null)}
        <path d="M32 6 54 19v26L32 58 10 45V19z" fill="none" stroke={`hsl(${hue} 90% 65% / 0.45)`} strokeWidth="1.2" />
        <text x="32" y="38" textAnchor="middle" fontFamily="Chakra Petch, sans-serif" fontWeight="700" fontSize="18" fill={`hsl(${hue} 90% 82%)`}>{initials || "?"}</text>
      </svg>
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
