"""Carousel IDs that saved campaign state depends on. A re-import may only replace reference.db if every one of them
still resolves. Later phases add their columns here (party positions, Omen instances, ...)."""
from __future__ import annotations

import json
import sqlite3

REFERENCE_COLUMNS: dict[str, tuple[str, str]] = {"place": ("locations", "ref_id"), "npc": ("npc_profiles", "ref_id"),
                                                  "ticket": ("character_tickets", "ticket_ref")}


def referenced_ids(campaign_conn: sqlite3.Connection) -> dict[str, set[str]]:
    """IDs in live state and in every save point (restoring a save must not bring back an ID the import dropped)."""
    out = {kind: {r[0] for r in campaign_conn.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL")}
           for kind, (table, col) in REFERENCE_COLUMNS.items()}
    for (snap,) in campaign_conn.execute("SELECT snapshot_json FROM save_points"):
        tables = json.loads(snap)
        for kind, (table, col) in REFERENCE_COLUMNS.items():
            out[kind].update(r[col] for r in tables.get(table, []) if r.get(col))
    return out


def unresolved(ref: sqlite3.Connection, ids: dict[str, set[str]]) -> dict[str, list[str]]:
    """{kind: sorted IDs the reference DB cannot resolve}; kinds with none are left out. Place aliases count."""
    from .import_places import resolve_place
    checks = {
        "place": lambda i: resolve_place(ref, i) is not None,
        "npc": lambda i: ref.execute("SELECT 1 FROM npc_templates WHERE id = ?", (i,)).fetchone() is not None,
        "ticket": lambda i: ref.execute("SELECT 1 FROM tropes WHERE id = ?", (i[len("trope:"):],)).fetchone() is not None if i.startswith("trope:")
        else ref.execute("SELECT 1 FROM tickets WHERE id = ?", (i,)).fetchone() is not None,
    }
    out = {kind: sorted(i for i in found if not checks[kind](i)) for kind, found in ids.items()}
    return {k: v for k, v in out.items() if v}
