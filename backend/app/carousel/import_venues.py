"""Venues (sub-maps): each is a small walkable graph of zones behind one or more gazetteer places. Zone IDs are
`<venue>:<node id>`; the ENTRY portal's node is where a group arrives, the EXIT portal's node the second way out.
Only persistent and conditional venues with a member place on the travel graph can be entered (a conditional one is
gated by its place's own `requires` flags). Optional shortcuts (`off_by_default`) are not links. An Omen is placed in a
zone only when it is a fixed Omen of a member place with a design-recommended (not manual-review) node; every other
venue Omen stays the GM's to place. Must run after import_places and import_omens."""
from __future__ import annotations

import json
import sqlite3

from . import refdb
from .import_places import resolve_place
from .sources import SourceFile

ENTERABLE_LAYERS = {"persistent", "conditional"}
LINK_MODES = {"walk", "vertical_transition"}
PLACEABLE = "DESIGN_RECOMMENDATION_NOT_CANON_COORD"


def _portal_node(v: dict, suffix: str, key: str) -> str | None:
    return next((p.get(key) for p in v.get("portals") or [] if str(p.get("id", "")).endswith(suffix)), None)


def import_venues(ref: sqlite3.Connection, files: dict[str, SourceFile]) -> None:
    skipped = 0
    for v in files["venues"].data["maps"]:
        vid = v["id"]
        zid = lambda node: f"{vid}:{node}"  # noqa: E731
        nodes = v["graph"]["nodes"]
        members = {m: resolve_place(ref, m) for m in v.get("members") or []}
        on_graph = any(p and ref.execute("SELECT 1 FROM graph_nodes WHERE location_id = ?", (p,)).fetchone() for p in members.values())
        reason = (v["layer"] if v["layer"] not in ENTERABLE_LAYERS else None) or (None if on_graph else "no member place on the travel graph")
        if reason:
            refdb.report(ref, "venues", vid, f"not enterable: {reason}")
        entry = _portal_node(v, "-ENTRY", "arrive_at_node") or (nodes[0]["id"] if nodes else None)
        exit_ = _portal_node(v, "-EXIT", "depart_from_node") or entry
        ref.execute("INSERT INTO venues (id, title, layer, enterable, entry_zone, exit_zone, raw_json) VALUES (?,?,?,?,?,?,?)",
                    (vid, v.get("title"), v["layer"], int(reason is None), entry and zid(entry), exit_ and zid(exit_),
                     json.dumps(v, ensure_ascii=False)))
        for n in nodes:
            ref.execute("INSERT INTO venue_zones (id, venue_id, node_id, label, area_type, floor) VALUES (?,?,?,?,?,?)",
                        (zid(n["id"]), vid, n["id"], n.get("label"), n.get("area_type"), n.get("floor_hint")))
        for e in v["graph"]["edges"]:
            if e.get("mode") not in LINK_MODES:
                skipped += 1
                continue
            a, b = sorted((zid(e["from"]), zid(e["to"])))
            ref.execute("INSERT INTO venue_links (venue_id, a, b, mode) SELECT ?,?,?,? WHERE NOT EXISTS "
                        "(SELECT 1 FROM venue_links WHERE a = ? AND b = ?)", (vid, a, b, e["mode"], a, b))
        for m in members:
            ref.execute("INSERT INTO venue_places (place_id, venue_id) VALUES (?,?)", (m, vid))
        for o in v.get("omens") or []:
            om = ref.execute("SELECT spawn_mode, place_id, choice_trigger FROM omens WHERE id = ?", (o["omen_id"],)).fetchone()
            in_member = om is not None and om["place_id"] in members.values()
            if om is not None and not in_member:
                refdb.report(ref, "venues", o["omen_id"], f"listed in {vid}, but the Omen data places it at {om['place_id']}; the Omen data wins")
            if om is not None and o.get("choice_action") and o["choice_action"] != om["choice_trigger"]:
                refdb.report(ref, "venues", o["omen_id"], f"{vid} gives Choice {o['choice_action']!r}; the Omen data's "
                             f"choice_trigger {om['choice_trigger']!r} is used")
            placed = om is not None and om["spawn_mode"] == "fixed" and in_member and o.get("placement_status") == PLACEABLE
            ref.execute("INSERT INTO venue_omens (omen_id, venue_id, zone_id, placed, choice_action) VALUES (?,?,?,?,?)",
                        (o["omen_id"], vid, o.get("recommended_node") and zid(o["recommended_node"]), int(placed), o.get("choice_action")))
    refdb.report(ref, "venues", None, f"{skipped} proposed optional shortcut(s) not imported (off by default)")
