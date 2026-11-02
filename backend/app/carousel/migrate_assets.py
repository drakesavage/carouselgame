"""Backfill pre-map campaigns from their existing, unambiguous location names."""
import json

from .import_places import place_by_name
from .refdata import has_reference
from . import venues


def backfill_map_positions(conn) -> list[int]:
    if not has_reference(conn):
        return []
    changed = []
    with conn:
        for camp in conn.execute("SELECT id,current_location FROM campaigns WHERE node_id IS NULL AND current_location IS NOT NULL").fetchall():
            pid = place_by_name(conn, camp["current_location"])
            node = conn.execute("SELECT id,requires_json FROM ref.graph_nodes WHERE id=? AND location_id=?", (pid, pid)).fetchone() if pid else None
            if not node:
                continue
            flags = {r[0] for r in conn.execute("SELECT flag FROM campaign_flags WHERE campaign_id=?", (camp["id"],))}
            if not set(json.loads(node["requires_json"])).issubset(flags):
                continue
            conn.execute("UPDATE campaigns SET node_id=? WHERE id=? AND node_id IS NULL", (node["id"], camp["id"]))
            conn.execute("INSERT OR IGNORE INTO known_places (campaign_id,place_id,how,known_at_minutes) SELECT id,?,'visited',game_time_minutes FROM campaigns WHERE id=?", (pid, camp["id"]))
            if not conn.execute("SELECT 1 FROM stories WHERE campaign_id=? AND status='active'", (camp["id"],)).fetchone():
                venues.enter(conn, camp["id"], node["id"])
            changed.append(camp["id"])
    return changed
