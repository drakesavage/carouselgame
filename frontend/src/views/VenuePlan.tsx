import { useEffect, useRef, useState } from "react";
import { PlayerVenue } from "../api";

/** Only server-projected discovered areas reach this component. */
export function VenuePlan({ plan, busy, onMove }: {
  plan: NonNullable<PlayerVenue["plan"]>; busy: boolean; onMove: (id: string) => void;
}) {
  const [level, setLevel] = useState(plan.current_level);
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); }, [open]);
  useEffect(() => setLevel(plan.current_level), [plan.current_level]);
  const levels = [...new Set(plan.zones.map(z => z.level))].sort((a, b) => a - b);
  const active = levels.includes(level) ? level : plan.current_level;
  const areas = plan.zones.filter(z => z.level === active);
  const x = Math.min(...areas.map(z => z.bounds[0])) - 14;
  const y = Math.min(...areas.map(z => z.bounds[1])) - 14;
  const w = Math.max(...areas.map(z => z.bounds[2])) - x + 14;
  const h = Math.max(...areas.map(z => z.bounds[3])) - y + 14;
  const levelName = (n: number) => plan.zones.some(z => z.level === n && z.label === "Roof") ? "Roof" : n === 0 ? "Ground floor" : n === -1 ? "Basement" : `Floor ${n}`;
  return <>
    <button className="btn small" aria-haspopup="dialog" onClick={() => setOpen(true)} style={{ margin: "8px 0" }}>Interior plan</button>
    <dialog ref={dialog} aria-label="Interior plan" onClose={() => setOpen(false)}
      style={{ width: "min(680px, 92vw)", maxHeight: "85vh", overflow: "auto", margin: "auto", padding: 20,
        background: "#202023", color: "#eee2ce", border: "1px solid #a9926c", borderRadius: 6 }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 20 }}>
      <strong>Interior plan · discovered areas</strong>
      <button className="btn small" onClick={() => dialog.current?.close()}>Close plan</button>
    </div>
    <label style={{ display: "block", margin: "8px 0" }}>Level {" "}
      <select aria-label="Interior floor" value={active} onChange={e => setLevel(Number(e.target.value))}>
        {levels.map(n => <option key={n} value={n}>{levelName(n)}</option>)}
      </select>
    </label>
    <svg viewBox={`${x} ${y} ${w} ${h}`} aria-label={`Known interior plan: ${levelName(active)}`}
      style={{ display: "block", width: "100%", height: Math.min(400, h * 1.1), background: "#171719" }}>
      {areas.map(z => {
        const [left, top, right, bottom] = z.bounds;
        const canMove = z.can_move && !busy;
        const words = z.label.split(" ");
        const lines: string[] = [];
        for (const word of words) {
          const last = lines.length - 1;
          if (last >= 0 && (lines[last] + " " + word).length <= 12) lines[last] += " " + word;
          else lines.push(word);
        }
        return <g key={z.id} role={z.can_move ? "button" : "img"} tabIndex={canMove ? 0 : -1}
          aria-label={z.can_move ? `Move to ${z.label}` : `${z.label}${z.current ? ", you are here" : ""}${z.blocked ? `, blocked: ${z.blocked}` : ""}`}
          aria-disabled={z.can_move ? !canMove : undefined} style={{ cursor: canMove ? "pointer" : "default" }}
          onClick={() => { if (canMove) onMove(z.id); }}
          onKeyDown={e => { if (canMove && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onMove(z.id); } }}>
          <title>{z.label}{z.current ? " · You are here" : ""}{z.blocked ? ` · Blocked: ${z.blocked}` : ""}</title>
          <rect x={left} y={top} width={right - left} height={bottom - top} rx={2}
            fill={z.current ? "#4c2228" : z.visited ? "#302a22" : "#242427"} stroke={z.current ? "#d79593" : "#a9926c"} strokeWidth={1.5}
            strokeDasharray={z.blocked ? "4 3" : undefined} />
          <text x={(left + right) / 2} y={(top + bottom) / 2 - (lines.length - 1) * 6}
            fill="#eee2ce" fontSize={11} textAnchor="middle" pointerEvents="none">
            {lines.map((line, i) => <tspan key={i} x={(left + right) / 2} dy={i ? 12 : 0}>{line}</tspan>)}
          </text>
        </g>;
      })}
    </svg>
    <small>Highlighted: your location. Dashed outline: blocked. Select an available adjoining area to move. Diagram is not to scale.</small>
    </dialog>
  </>;
}
