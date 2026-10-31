"""A character's Carousel sheet (System Bible §2.3, §4.2): five base stats, current stats after additive modifiers,
and Plot Armor in three layers. base = sum of base stats; current = sum of current stats; effective = current with
Plot Armor multipliers applied in the order they were recorded, floored. Drawbacks never touch base points."""
from __future__ import annotations

import math
import sqlite3

STATS = ("Mettle", "Moxie", "Hustle", "Savvy", "Grit")


def sheet(conn: sqlite3.Connection, character_id: int, *, show_hidden: bool = True) -> dict:
    """show_hidden=False (player views): modifiers with a secret source still count but are not listed."""
    row = conn.execute(f"SELECT {', '.join(s.lower() for s in STATS)} FROM characters WHERE id = ?", (character_id,)).fetchone()
    base = {s: row[i] for i, s in enumerate(STATS)}
    mods = [dict(m) for m in conn.execute("SELECT id, target, op, value, source, scope, hidden FROM stat_modifiers WHERE character_id = ? ORDER BY id",
                                          (character_id,))]
    current = {s: None if base[s] is None else base[s] + sum(m["value"] for m in mods if m["target"] == s.lower() and m["op"] == "add")
               for s in STATS}
    if any(v is None for v in base.values()):
        pa = {"base": None, "current": None, "effective": None}
    else:
        cur = sum(current.values())
        eff = cur
        for m in mods:
            if m["target"] == "plot_armor":
                eff = eff * m["value"] if m["op"] == "multiply" else eff + m["value"]
        pa = {"base": sum(base.values()), "current": math.floor(cur), "effective": math.floor(eff)}
        current = {s: math.floor(v) for s, v in current.items()}
    return {"stats": base, "current": current, "plot_armor": pa, "modifiers": [m for m in mods if show_hidden or not m["hidden"]]}
