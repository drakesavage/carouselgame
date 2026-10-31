"""Carousel IDs that saved campaign state depends on. A re-import may only replace reference.db if every one of them
still resolves. Later phases add their columns here (party positions, Omen instances, owned tickets, ...)."""
from __future__ import annotations

import sqlite3

REFERENCE_COLUMNS: dict[str, str] = {
    "place": "SELECT DISTINCT ref_id FROM locations WHERE ref_id IS NOT NULL",
    "npc": "SELECT DISTINCT ref_id FROM npc_profiles WHERE ref_id IS NOT NULL",
}


def referenced_ids(campaign_conn: sqlite3.Connection) -> dict[str, set[str]]:
    return {kind: {r[0] for r in campaign_conn.execute(sql)} for kind, sql in REFERENCE_COLUMNS.items()}


def unresolved(ref: sqlite3.Connection, ids: dict[str, set[str]]) -> dict[str, list[str]]:
    """{kind: sorted IDs the reference DB cannot resolve}; kinds with none are left out. Place aliases count."""
    from .import_places import resolve_place
    checks = {
        "place": lambda i: resolve_place(ref, i) is not None,
        "npc": lambda i: ref.execute("SELECT 1 FROM npc_templates WHERE id = ?", (i,)).fetchone() is not None,
    }
    out = {kind: sorted(i for i in found if not checks[kind](i)) for kind, found in ids.items()}
    return {k: v for k, v in out.items() if v}
