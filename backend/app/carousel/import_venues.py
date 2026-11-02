"""Venues (sub-maps): each is a small walkable graph of zones behind one or more gazetteer places. Zone IDs are
`<venue>:<node id>`; the ENTRY portal's node is where a group arrives, the EXIT portal's node the second way out.
Only persistent and conditional venues with a member place on the travel graph can be entered (a conditional one is
gated by its place's own `requires` flags). Optional shortcuts (`off_by_default`) are not links. An Omen is placed in a
zone only when it is a fixed Omen of a member place with a design-recommended (not manual-review) node; every other
venue Omen stays the GM's to place. Must run after import_places and import_omens."""
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy

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
    reconciled = files.get("reconciliation")
    corrections = reconciled.data if reconciled else {}
    patches = corrections.get("venue_patches", {})
    associations = {(a["venue_id"], a["omen_id"]): a for a in corrections.get("associations", [])}
    for original in files["venues"].data["maps"]:
        v = deepcopy(original)
        vid = v["id"]
        patch = patches.get(vid, {})
        if patch.get("graph_override"):
            v["graph"] = patch["graph_override"]
            v["portals"] = patch["portals_override"]
        for omen in v.get("omens", []):
            replacement = patch.get("omen_node_overrides", {}).get(omen["omen_id"])
            if replacement:
                omen.update(recommended_node=replacement, placement_status=PLACEABLE)
        if "members" in patch:
            v["members"] = patch["members"]
        v["members"] = list(dict.fromkeys([*v.get("members", []), *patch.get("additional_members", [])]))
        v["reconciliation"] = patch
        v["alias_of_venue"] = patch.get("alias_of_venue")
        v["shared_geometry_map"] = patch.get("shared_geometry_map", vid)
        zid = lambda node: f"{vid}:{node}"  # noqa: E731
        nodes = v["graph"]["nodes"]
        members = {m: resolve_place(ref, m) for m in v.get("members") or []}
        on_graph = any(p and ref.execute("SELECT 1 FROM graph_nodes WHERE location_id = ?", (p,)).fetchone() for p in members.values())
        reason = (patch.get("reason") if patch.get("excluded") or patch.get("runtime_blocked") else None)
        reason = reason or (v["layer"] if v["layer"] not in ENTERABLE_LAYERS else None) or (None if on_graph else "no member place on the travel graph")
        if reason:
            refdb.report(ref, "venues", vid, f"not enterable: {reason}")
        entry = _portal_node(v, "-ENTRY", "arrive_at_node") or (nodes[0]["id"] if nodes else None)
        exit_ = _portal_node(v, "-EXIT", "depart_from_node") or entry
        v["navigation_mode"] = "text_graph"
        v["illustrated_geometry_installed"] = False
        v["logical_portals"] = [
            {"from_map_id": n["map_id"], "from_node_id": n["id"],
             "to_map_id": vid, "to_node_id": zid(entry), "return_from_node_id": zid(exit_),
             "state": "blocked" if reason else ("conditional" if json.loads(n["requires_json"]) else "open"),
             "requires": json.loads(n["requires_json"]), "source_version": files["travel_graph"].sha256,
             "scope": "logical site arrival only; physical doors and illustrated collision unverified"}
            for pid in sorted({p for p in members.values() if p})
            for n in ref.execute("SELECT id,map_id,requires_json FROM graph_nodes WHERE location_id=?", (pid,))
        ] if entry and exit_ else []
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
            ref.execute("INSERT INTO venue_links (venue_id, a, b, mode, source_lock) SELECT ?,?,?,?,? WHERE NOT EXISTS "
                        "(SELECT 1 FROM venue_links WHERE a = ? AND b = ?)", (vid, a, b, e["mode"], e.get("lock"), a, b))
            if e.get("lock"):
                refdb.report(ref, "venue_route_holds", vid, f"{a} -> {b}: unresolved source constraint {e['lock']}")
        for m in members:
            if not v["alias_of_venue"]:
                ref.execute("INSERT INTO venue_places (place_id, venue_id) VALUES (?,?)", (m, vid))
        for o in v.get("omens") or []:
            association = associations.get((vid, o["omen_id"]), {})
            relation = association.get("relationship", "venue_context")
            ref.execute("INSERT INTO asset_relationships (venue_id, omen_id, relationship, source_location, basis) VALUES (?,?,?,?,?)",
                        (vid, o["omen_id"], relation, association.get("source_location"), association.get("basis")))
            om = ref.execute("SELECT spawn_mode, place_id, choice_trigger FROM omens WHERE id = ?", (o["omen_id"],)).fetchone()
            in_member = om is not None and om["place_id"] in members.values()
            if om is not None and not in_member and relation == "venue_context":
                refdb.report(ref, "venues", o["omen_id"], f"listed in {vid}, but the Omen data places it at {om['place_id']}; the Omen data wins")
            if om is not None and o.get("choice_action") and o["choice_action"] != om["choice_trigger"]:
                refdb.report(ref, "venues", o["omen_id"], f"{vid} gives Choice {o['choice_action']!r}; the Omen data's "
                             f"choice_trigger {om['choice_trigger']!r} is used")
            placed = (not reason and relation == "venue_context" and om is not None and om["spawn_mode"] == "fixed"
                      and in_member and o.get("placement_status") == PLACEABLE)
            ref.execute("INSERT INTO venue_omens (omen_id, venue_id, zone_id, placed, choice_action) VALUES (?,?,?,?,?)",
                        (o["omen_id"], vid, o.get("recommended_node") and zid(o["recommended_node"]), int(placed),
                         om["choice_trigger"] if om is not None else None))
    refdb.report(ref, "venues", None, f"{skipped} proposed optional shortcut(s) not imported (off by default)")
