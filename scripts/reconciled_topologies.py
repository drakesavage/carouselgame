"""Text-only topology repairs derived from registered ADAPTATION guides."""
import re


def story_entry(patch: dict, lock: str, omen_id: str, *, gate_label=False) -> dict:
    """Directional logical entry: a Choice opens the story; departure stays possible."""
    exterior = {n["id"] for n in patch["graph_override"]["nodes"] if n["area_type"] == "exterior"}
    for edge in patch["graph_override"]["edges"]:
        if edge["from"] in exterior:
            edge["lock"] = "entry_story:" + edge["from"] + ":" + lock
    entry = patch["portals_override"][0]["arrive_at_node"]
    if gate_label:
        for area in [*patch["graph_override"]["nodes"], *patch["player_plan"]["zones"]]:
            if area.get("id", area.get("node_id")) == entry:
                area["label"] = "Gate"
    patch["omen_node_overrides"] = {omen_id: entry}
    patch["story_entry_policy"] = {"classification": "ADAPTATION guard implementing the existing registry Choice",
        "required_story_lock": lock, "observation_starts_story": False, "egress_requires_active_story": False}
    return patch


def stacked(vid: str, guide: dict, original: dict, guide_path: str, *, activate=False, reason="") -> dict:
    """Replace superseded chains with the approved floors; retain unresolved entry/access holds."""
    plates = guide.get("plates") or [{**f, "level": i, "stair_core": guide["core"]}
                                      for i, f in enumerate(guide["floors"])]
    nodes, edges, areas, outside = [], [], [], []
    key = lambda text: "guide_" + re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")

    def node(name, label, level, bounds=None, area="interior"):
        ident = key(name)
        nodes.append({"id": ident, "label": label, "floor_hint": level, "area_type": area,
                      "geometry_status": "approved ADAPTATION study; physical registration unverified"})
        if bounds is not None:
            areas.append({"node_id": ident, "level": level, "label": label, "bounds": bounds})
        return ident

    def link(a, b, mode="walk", lock=None):
        edges.append({"from": a, "to": b, "mode": mode, "two_way": True, "lock": lock})

    stairs = []
    for plate in sorted(plates, key=lambda p: p["level"]):
        level = plate["level"]
        hall_bounds = ([120, 72, 200, 200] if vid == "SM-068" else
                       [110, 112, 140, 128] if vid == "SM-057" else [112, 114, 138, 128])
        hall = node(f"L{level}_hall", "Hall", level, hall_bounds)
        stair = node(f"L{level}_stairs", "Stairs", level, plate["stair_core"])
        link(hall, stair)
        stairs.append(stair)
        for label, bounds in plate["rooms"]:
            room = node(f"L{level}_{label}", label, level, bounds)
            link(hall, room, lock="requires_authored_records_access" if vid == "SM-068" and label == "Locked records" else None)
        for index, point in enumerate(plate.get("outside", plate.get("exterior_doors", []))):
            x, y = point
            bounds = ([x - 20, -32, x + 20, -8] if y == 0 else
                      [-48, y - 15, -8, y + 15] if x == 0 else [x - 20, y + 8, x + 20, y + 32])
            threshold = node(f"L{level}_outside_{index}", "Entrance" if not outside else "Rear exit", level, bounds, "exterior")
            link(threshold, hall)
            outside.append(threshold)
    for a, b in zip(stairs, stairs[1:]):
        link(a, b, "vertical_transition")
    assert outside and len({n["id"] for n in nodes}) == len(nodes), vid
    nodes += [{**n, "area_type": "retired_reference", "walkable": False} for n in original["graph"]["nodes"]]
    return {"graph_override": {"nodes": nodes, "edges": edges},
            "portals_override": [{"id": vid + "-ENTRY", "arrive_at_node": outside[0]},
                                 {"id": vid + "-EXIT", "depart_from_node": outside[-1]}],
            "runtime_blocked": not activate, "physical_runtime_blocked": True, "reason": reason,
            "topology_basis": {"classification": "ADAPTATION", "guide": guide_path, "layout_human_approved": True,
                               "physical_geometry_approved": False, "safety_scope_automatically_applied": False},
            "player_plan": {"layout_human_approved": True, "mode": "discovery_filtered_schematic", "zones": areas} if activate else None,
            "retired_source_graph": original["graph"]}


def allcraft(guide: dict, original: dict, guide_path: str, layout_approved: bool = False) -> dict:
    routes = guide["route_edges"]
    keys = sorted({k for pair in routes for k in pair})
    ids = {k: "route_" + re.sub(r"[^A-Za-z0-9]+", "_", k).strip("_") for k in keys}
    if len(set(ids.values())) != len(ids):
        raise ValueError("Allcraft route IDs collide")

    def floor(key):
        return 5 if key == "ROOF" else int(key[1]) if key.startswith("F") else 0

    nodes = []
    for key in keys:
        label = key.replace(":", " · ").replace("_", " ")
        nodes.append({"id": ids[key], "label": label, "floor_hint": floor(key),
                      "area_type": "exterior" if key in {"street", "rear_alley", "ROOF"} else "interior",
                      "geometry_status": "ADAPTATION text topology; physical registration unverified"})
    # Keep old IDs resolvable for saves without guessing which expanded room a
    # former aggregate zone meant. These have no links or arrival portals.
    nodes += [{**n, "area_type": "retired_reference", "walkable": False} for n in original["graph"]["nodes"]]
    edges = [{"from": ids[a], "to": ids[b], "mode": "walk" if floor(a) == floor(b) else "vertical_transition",
              "two_way": True, "lock": None} for a, b in routes]
    diagram = []
    if layout_approved:
        for plate in guide["plates"]:
            level = plate["level"]
            if level == 5:
                diagram.append({"node_id": ids["ROOF"], "level": 5, "label": "Roof", "bounds": plate["outer_bounds"]})
                continue
            areas = [("hall", "Hall", plate["central_hall"]),
                     ("back_landing", "Rear stairs", plate["back_stair_core"]),
                     ("front_landing", "Front stairs", plate["front_main_stair_core"])]
            areas += [(r["label"], r["label"], r["bounds"]) for r in plate["rooms"]]
            for key, label, bounds in areas:
                diagram.append({"node_id": ids[f"F{level}:{key}"], "level": level, "label": label, "bounds": bounds})
        diagram += [{"node_id": ids["street"], "level": 1, "label": "Street", "bounds": [0, 376, 240, 412]},
                    {"node_id": ids["rear_alley"], "level": 1, "label": "Rear alley", "bounds": [-52, 12, -12, 108]}]
    return {"graph_override": {"nodes": nodes, "edges": edges},
            "player_plan": {"layout_human_approved": True, "mode": "discovery_filtered_schematic", "zones": diagram} if layout_approved else None,
            "portals_override": [{"id": "SM-087-ENTRY", "arrive_at_node": ids["street"]},
                                 {"id": "SM-087-EXIT", "depart_from_node": ids["rear_alley"]}],
            "runtime_blocked": False, "physical_runtime_blocked": True,
            "reason": "Corrected registered guide installed as text topology only",
            "topology_basis": {"classification": "ADAPTATION", "guide": guide_path,
                               "layout_human_approved": layout_approved,
                               "physical_geometry_approved": False, "source_writ_scope": guide["source_writ_scope"],
                               "safety_scope_automatically_applied": False},
            "retired_source_graph": original["graph"]}
