"""Places and movement: every gazetteer record (aliases and rejected ones included; no CAR ID is ever dropped),
the town-map handoff fields merged onto the same IDs, the travel graph with its flags untouched, and name lookup.
Movement authority is the graph: adjacency never implies permission, and painted paths add nothing."""
from __future__ import annotations

import json
import sqlite3

from .coords import to_pixel
from .sources import SourceFile
from .text import norm_name


def import_places(ref: sqlite3.Connection, files: dict[str, SourceFile]) -> None:
    town = {r["id"]: r for r in files["locations"].data}
    additions = files.get("reconciliation")
    extra = additions.data.get("places", []) if additions else []
    patches = additions.data.get("place_patches", {}) if additions else {}
    node_patches = additions.data.get("graph_node_patches", {}) if additions else {}
    edge_patches = additions.data.get("graph_edge_patches", {}) if additions else {}
    original_ids = {g["id"] for g in files["gazetteer"].data}
    for g in [*files["gazetteer"].data, *(g for g in extra if g["id"] not in original_ids)]:
        rec = {**g, **town.get(g["id"], {})}
        rec.update(patches.get(g["id"], {}))
        coords = rec.get("coordinates") or [None, None]
        north, west = coords[0], coords[1]
        px, py = to_pixel(north, west) if north is not None and west is not None else (None, None)
        ref.execute("""INSERT INTO places (id, name, category, region, map_id, north, west, px, py, requires_json, spoiler, status,
                                           in_town_map, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rec["id"], rec.get("name"), rec.get("category"), rec.get("region"), rec.get("map_id"), north, west, px, py,
                     json.dumps(rec.get("requires") or []), json.dumps(rec.get("spoiler")), rec.get("status"),
                     int(rec["id"] in town and north is not None and west is not None), json.dumps(rec, ensure_ascii=False)))
        if rec.get("name"):
            ref.execute("INSERT OR IGNORE INTO place_names (name, place_id) VALUES (?,?)", (norm_name(rec["name"]), rec["id"]))
    graph = files["travel_graph"].data
    if set(node_patches) - {n["id"] for n in graph["nodes"]} or set(edge_patches) - {e["id"] for e in graph["edges"]}:
        raise ValueError("reconciled navigation override names an unknown saved graph ID")
    for alias, target in (graph.get("aliases") or {}).items():
        ref.execute("INSERT INTO place_aliases (alias_id, target_id) VALUES (?,?)", (alias, target))
    for n in graph["nodes"]:
        n = {**n, **{k: v for k, v in node_patches.get(n["id"], {}).items() if k in {"coordinates", "map_id"}}}
        c = n.get("coordinates") or [None, None]
        requirements = list(n.get("requires") or [])
        if patches.get(n.get("location_id"), {}).get("navigable") is False:
            # Preserve saved IDs, but retire a superseded normal-town arrival.
            requirements.append("source_topology_pending:" + n["location_id"])
        ref.execute("INSERT INTO graph_nodes (id, location_id, map_id, north, west, requires_json) VALUES (?,?,?,?,?,?)",
                    (n["id"], n.get("location_id"), n.get("map_id"), c[0], c[1], json.dumps(requirements)))
    for e in graph["edges"]:
        patch = edge_patches.get(e["id"], {})
        if patch and e.get("kind") != "approach":
            raise ValueError("reconciled edge overrides are limited to private approach spurs")
        e = {**e, **{k: v for k, v in patch.items() if k in {"geometry", "cost", "length_units", "reconciliation_basis"}}}
        ref.execute("""INSERT INTO graph_edges (id, from_id, to_id, kind, directed, requires_json, cost, length_units, geometry_json, raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (e["id"], e["from"], e["to"], e.get("kind"), int(bool(e.get("directed"))), json.dumps(e.get("requires") or []),
                     e.get("cost"), e.get("length_units"), json.dumps(e.get("geometry") or []), json.dumps(e, ensure_ascii=False)))


def resolve_place(ref: sqlite3.Connection, id_or_alias: str) -> str | None:
    """The canonical place ID for an ID or an alias ID; None if neither exists."""
    row = ref.execute("SELECT target_id FROM place_aliases WHERE alias_id = ?", (id_or_alias,)).fetchone()
    pid = row[0] if row else id_or_alias
    return pid if ref.execute("SELECT 1 FROM places WHERE id = ?", (pid,)).fetchone() else None


def place_by_name(ref: sqlite3.Connection, name: str | None) -> str | None:
    """The one canonical place a name (normalised) refers to, preferring active records; None if unknown or ambiguous."""
    ids = [r[0] for r in ref.execute("SELECT place_id FROM place_names WHERE name = ?", (norm_name(name),))]
    resolved = {resolve_place(ref, i) for i in ids} - {None}
    if len(resolved) > 1:
        active = {i for i in resolved if ref.execute("SELECT 1 FROM places WHERE id = ? AND status = 'active'", (i,)).fetchone()}
        resolved = active if len(active) == 1 else set()
    return resolved.pop() if len(resolved) == 1 else None
