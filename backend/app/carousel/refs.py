"""Carousel IDs that saved campaign state depends on. A re-import may only replace reference.db if every one of them
still resolves. Later phases add their columns here."""
from __future__ import annotations

import json
import sqlite3

REFERENCE_COLUMNS: dict[str, tuple[str, str]] = {"place": ("locations", "ref_id"), "npc": ("npc_profiles", "ref_id"),
                                                  "ticket": ("character_tickets", "ticket_ref"),
                                                  "node": ("campaigns", "node_id"), "group_node": ("party_groups", "node_id"),
                                                  "known_place": ("known_places", "place_id"), "omen": ("omen_states", "omen_id"),
                                                  "story_omen": ("stories", "omen_id"),
                                                  "lure_resident": ("campaigns", "lure_resident"),
                                                  "venue": ("campaigns", "venue_id"), "group_venue": ("party_groups", "venue_id"),
                                                  "zone": ("campaigns", "zone_id"), "group_zone": ("party_groups", "zone_id"),
                                                  "visited_zone": ("zone_visits", "zone_id")}


def referenced_ids(campaign_conn: sqlite3.Connection) -> dict[str, set[str]]:
    """IDs in live state and in every save point (restoring a save must not bring back an ID the import dropped)."""
    def has(table: str, col: str) -> bool:   # a campaign.db from before a phase lacks that phase's tables (it gains them on next server start)
        return any(r[1] == col for r in campaign_conn.execute(f"PRAGMA table_info({table})"))
    out = {kind: {r[0] for r in campaign_conn.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL")} if has(table, col) else set()
           for kind, (table, col) in REFERENCE_COLUMNS.items()}
    for (snap,) in campaign_conn.execute("SELECT snapshot_json FROM save_points"):
        tables = json.loads(snap)
        for kind, (table, col) in REFERENCE_COLUMNS.items():
            out[kind].update(r[col] for r in tables.get(table, []) if r.get(col))
    return out


def unresolved(ref: sqlite3.Connection, ids: dict[str, set[str]]) -> dict[str, list[str]]:
    """{kind: sorted IDs the reference DB cannot resolve}; kinds with none are left out. Place aliases count."""
    from .import_places import resolve_place

    def place(i: str) -> bool:
        return resolve_place(ref, i) is not None

    def node(i: str) -> bool:
        return ref.execute("SELECT 1 FROM graph_nodes WHERE id = ?", (i,)).fetchone() is not None

    def npc(i: str) -> bool:
        return ref.execute("SELECT 1 FROM npc_templates WHERE id = ?", (i,)).fetchone() is not None

    def venue(i: str) -> bool:
        return ref.execute("SELECT 1 FROM venues WHERE id = ?", (i,)).fetchone() is not None

    def zone(i: str) -> bool:   # a story-added room lives in campaign state, not in the reference data
        return ":story-" in i or ref.execute("SELECT 1 FROM venue_zones WHERE id = ?", (i,)).fetchone() is not None

    checks = {
        "venue": venue, "group_venue": venue, "zone": zone, "group_zone": zone, "visited_zone": zone,
        "place": place,
        "npc": npc, "lure_resident": npc,
        "node": node, "group_node": node, "known_place": place,
        "omen": lambda i: ref.execute("SELECT 1 FROM omens WHERE id = ?", (i,)).fetchone() is not None,
        "story_omen": lambda i: ref.execute("SELECT 1 FROM omens WHERE id = ?", (i,)).fetchone() is not None,
        "ticket": lambda i: ref.execute("SELECT 1 FROM tropes WHERE id = ?", (i[len("trope:"):],)).fetchone() is not None if i.startswith("trope:")
        else ref.execute("SELECT 1 FROM tickets WHERE id = ?", (i,)).fetchone() is not None,
    }
    out = {kind: sorted(i for i in found if not checks[kind](i)) for kind, found in ids.items()}
    return {k: v for k, v in out.items() if v}
