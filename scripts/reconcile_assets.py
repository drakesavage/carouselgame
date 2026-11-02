"""Export source-aware game references without changing geography or review decisions.

Run with the game's Python. SQLite is opened read-only; output is a rebuildable
handoff, never a replacement for the studio or original submap specification.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import json
import sqlite3
from pathlib import Path
from reconciled_topologies import allcraft, stacked, story_entry


def build(root: Path) -> dict:
    inputs = {}

    def read(rel):
        raw = (root / rel).read_bytes()
        inputs[rel] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw.decode("utf-8-sig"))

    gaz = {p["id"]: p for p in read("map/data/gazetteer.json")}
    registry = read("Illustrated_Map_Production/audits/20261031-reconciliation/location_registry.json")
    additions = []
    for p in registry:
        if p["id"] in gaz:
            continue
        additions.append({
            "id": p["id"], "name": p["name"], "category": p.get("category") or "Location",
            "coordinates": None, "map_id": None, "status": "source_only",
            "requires": [], "spoiler": True, "navigable": False,
            "source_citations": p.get("source_citations", []),
            "canon_facts": p.get("canon_facts", []),
            "existence_confidence": p.get("canonical_certainty"),
            "persistence": p.get("persistence"),
            "placement_confidence": "unplaced; not a map pin",
            "description": "Source-indexed identity; exact placement and runtime entry remain unverified.",
        })

    db_path = root / "Location_Art_Production/data/studio.sqlite3"
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    try:
        images = []
        gaps = []
        for p in db.execute("SELECT * FROM locations ORDER BY id"):
            if p["image_required"] and not p["current_asset_id"]:
                gaps.append({"id": p["id"], "name": p["name"], "reason": "no_selected_image"})
            if not p["current_asset_id"]:
                continue
            a = db.execute("SELECT * FROM assets WHERE id = ?", (p["current_asset_id"],)).fetchone()
            if not a:
                raise ValueError(f"Missing selected asset: {p['id']}")
            path = Path(a["path"])
            path = path if path.is_absolute() else root / "Location_Art_Production" / path
            rel = path.resolve().relative_to((root / "Location_Art_Production/assets").resolve()).as_posix()
            if hashlib.sha256(path.read_bytes()).hexdigest() != a["sha256"]:
                raise ValueError(f"Image hash mismatch: {p['id']}")
            images.append({"location_id": p["id"], "relative_image_path": "assets/" + rel,
                           "sha256": a["sha256"], "review_status": a["review_status"],
                           "human_approved": p["approved_asset_id"] == a["id"],
                           "asset_id": a["id"]})
        studio_state = {
            "locations": [tuple(x) for x in db.execute("SELECT id,current_asset_id,approved_asset_id,record_json,identity_json,release_eligible FROM locations ORDER BY id")],
            "assets": [tuple(x) for x in db.execute("SELECT id,sha256,review_status FROM assets ORDER BY id")],
        }
        studio_sha = hashlib.sha256(json.dumps(studio_state, sort_keys=True).encode()).hexdigest()
    finally:
        db.close()

    # These memberships follow explicit source identities, not nearest pixels.
    members = {
        "SM-086": ["AUD-LOC-BOWLING-ALLEY"], "SM-087": ["CAR-202"],
        "SM-088": ["AUD-LOC-BETTER-BEA-WARES"],
        "SM-091": ["AUD-LOC-WESTERN-COMMUNITY-CENTER"],
        "SM-092": ["AUD-LOC-JUST-DESSERTS"], "SM-093": ["AUD-LOC-COMMUNITY-POOL"],
        "SM-095": ["AUD-LOC-BLACK-SNOW-CHEMICAL-FACTORY"],
    }
    # Narrow only where the registry's own Original Location identifies the site.
    omen_places = {
        "OM-005": "CAR-027", "OM-008": "AUD-LOC-JUST-DESSERTS",
        "OM-017": "AUD-LOC-COMMUNITY-POOL", "OM-053": "CAR-014",
        "OM-068": "AUD-LOC-HEM-AND-HAUNT", "OM-069": "CAR-034", "OM-070": "CAR-034",
        "OM-058": "AUD-LOC-ALLCRAFT-OPPOSITE-BAR",
    }
    for oid in ["OM-055", "OM-056", "OM-057", "OM-059", "OM-066", "OM-067", "OM-076", "OM-091", "OM-092"]:
        omen_places[oid] = "CAR-202"
    for i in range(77, 84):
        omen_places[f"OM-{i:03}"] = "AUD-LOC-BETTER-BEA-WARES"
    for i in [60, 61, 62]:
        omen_places[f"OM-{i:03}"] = "AUD-LOC-WESTERN-COMMUNITY-CENTER"
    omens = read("omen/Carousel_Omen_Engine_Data_v0.2.json")["registry"]
    omen_index = {o["ID"]: o for o in omens}
    # Named sites previously flattened onto Eastern Carousel/Downtown/Geist
    # Estate. Their existence is sourced, but no parcel or travel edge is added.
    new_sites = [
        ("AUD-LOC-KRSL-SECURED-FACILITY", "KRSL secured facility", "SM-089", ["OM-048"]),
        ("AUD-LOC-KRSL-POWERWORKS", "KRSL Powerworks Pavilion", "SM-090", ["OM-063", "OM-064", "OM-065", "OM-108"]),
        ("AUD-LOC-LOOK-BACK-ALLEY", "The Look Back alley", "SM-094", ["OM-007"]),
        ("AUD-LOC-CENTENNIAL-HILL-HOTEL", "Centennial storyline hotel on a hill", "SM-096", ["OM-052", "OM-054"]),
        ("AUD-LOC-NORTHERN-OPEN-HOUSE", "Northern open-house residence", "SM-097", ["OM-049"]),
        ("AUD-LOC-TURNPIKE-FIELD", "Carousel Turnpike private field", "SM-108", ["OM-044"]),
        ("AUD-LOC-RIVER-TREK-HOUSE", "House on the river trek", None, ["OM-100"]),
    ]
    for pid, name, vid, oids in new_sites:
        if pid not in gaz and not any(p["id"] == pid for p in additions):
            additions.append({"id": pid, "name": name, "category": "Location", "status": "source_only",
                              "coordinates": None, "map_id": None, "spoiler": True, "navigable": False,
                              "description": omen_index[oids[0]]["Original Location"],
                              "source_citations": [{"omen_id": oid, "source": omen_index[oid].get("Source / Chapter")}
                                                   for oid in oids],
                              "placement_confidence": "unplaced; not a map pin"})
        if vid:
            members[vid] = [pid]
        for oid in oids:
            omen_places[oid] = pid
    omen_places.update({"OM-006": "CAR-047", "OM-089": "CAR-048", "OM-031": "CAR-147"})
    venue_patches = {}
    graph_node_patches, graph_edge_patches = {}, {}
    for vid, ids in members.items():
        venue_patches[vid] = {"members": ids, "membership_basis": "explicit source venue identity; no parcel inferred"}
    venue_patches["SM-017"] = {"additional_members": ["AUD-LOC-HEM-AND-HAUNT"]}
    # Invalid source identities remain in the archive, never as playable venues.
    for vid in ["SM-071", "SM-078"]:
        venue_patches[vid] = {"excluded": True, "reason": "Source adjudication: not an independent venue"}

    adjudications = read("sublocations/production/source_adjudications.json")
    place_patches = {}
    story_parents = {"SM-052": "CAR-086", "SM-070": "CAR-046", "SM-105": "CAR-151", "SM-106": "CAR-151"}
    for a in adjudications:
        venue_patches.setdefault(a["map_id"], {}).update(
            {"source_adjudication": a, "runtime_blocked": True,
             "reason": a["runtime_graph_status"]})
        if a["map_id"] != "SM-084":
            original = next(v for v in read("sublocations/Carousel_Codex_Submap_Package_v1.1/Carousel_Codex_Submap_Spec_v1.json")["maps"]
                            if v["id"] == a["map_id"])
            for pid in original["members"]:
                place_patches[pid] = {"coordinates": None, "map_id": None, "status": "source_only",
                                      "navigable": False, "description": a["correction"],
                                      "source_adjudication": a,
                                      "runtime_entry_status": "requires_story_state_entry",
                                      "parent_location_id": story_parents.get(a["map_id"])}
        else:
            place_patches["CAR-173"] = {"description": a["correction"], "source_adjudication": a}
    # Preserve additional saved source overrides (e.g. mine and fraternity) as
    # explicit holds when production says the seed graph is no longer effective.
    spec = read("sublocations/Carousel_Codex_Submap_Package_v1.1/Carousel_Codex_Submap_Spec_v1.json")
    for v in spec["maps"]:
        rel = f"sublocations/production/maps/{v['id']}/source_blueprint.json"
        if not (root / rel).exists():
            continue
        bp = read(rel)
        if bp.get("original_graph_effective") is False or bp.get("runtime_installation_blocked"):
            patch = venue_patches.setdefault(v["id"], {})
            if patch.get("excluded"):
                continue
            patch.update(runtime_blocked=True, reason="Production source/topology correction requires reviewed runtime graph")
            patch.setdefault("source_adjudication", bp.get("source_adjudication_override") or bp.get("source_adjudication"))

    guide_rel = "sublocations/production/maps/SM-087/geometry_study/registered_floor_guide.json"
    approvals = {}
    approval_rel = "Asset_Reconciliation/floorplan-approval.json"
    if (root / approval_rel).exists():
        for approval in read(approval_rel)["studies"]:
            if not approval.get("human_approved") or approval.get("actor") != "human":
                raise ValueError(f"Layout lacks explicit human approval: {approval['venue_id']}")
            for item in approval["files"]:
                current_sha = hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
                if current_sha != item["sha256"]:
                    raise ValueError(f"Approved layout changed: {item['path']}")
                inputs[item["path"]] = current_sha
            approvals[approval["venue_id"]] = approval
    venue_patches.setdefault("SM-087", {}).update(allcraft(read(guide_rel), next(v for v in spec["maps"] if v["id"] == "SM-087"), guide_rel, "SM-087" in approvals))
    holds = {"SM-010": "Exact white-columned fraternity parcel and entrance remain unresolved",
             "SM-049": "Gate access state and timed entrance Choice binding remain unresolved",
             "SM-057": "Parent-map repair registration remains unverified and uninstalled",
             "SM-068": "Approved corrected text layout; records access remains locked"}
    for vid, reason in holds.items():
        if vid in approvals:
            rel = f"sublocations/production/maps/{vid}/geometry_study/registered_floor_guide.json"
            venue_patches.setdefault(vid, {}).update(stacked(vid, read(rel), next(v for v in spec["maps"] if v["id"] == vid), rel,
                                                           activate=vid in {"SM-049", "SM-068"}, reason=reason))
    if "SM-049" in approvals:
        venue_patches["SM-049"] = story_entry(venue_patches["SM-049"], "PERMANENT_VACANCY", "OM-032", gate_label=True)
        venue_patches["SM-049"]["reason"] = "Approved layout; gate permits story entry after the existing timed Choice"
    fraternity = read("sublocations/production/checks/20261101-circulation/fraternity_followup.json")
    place_patches["CAR-027"] = {"coordinates": None, "map_id": None, "status": "source_only", "navigable": False,
        "description": fraternity["source_identity"], "source_adjudication": fraternity,
        "runtime_entry_status": "requires_reviewed_parent_binding"}
    acceptance_rel = "Asset_Reconciliation/fraternity-parcel-acceptance.json"
    if (root / acceptance_rel).exists():
        if "SM-010" not in approvals:
            raise ValueError("Fraternity property binding also requires the approved floorplan layout")
        accepted = read(acceptance_rel)
        proposal_rel = "Asset_Reconciliation/fraternity-parcel-proposal.json"
        proposal = read(proposal_rel)
        if (accepted["actor"] != "human" or accepted["proposal_id"] != proposal["id"]
                or inputs[proposal_rel] != accepted["accepted_proposal_sha256"]):
            raise ValueError("Fraternity property acceptance does not match the reviewed proposal")
        px, py = accepted["site_anchor_px"]
        coords = [1000 - py / 12, 1000 - px / 12]
        graph = read("Illustrated_Map_Production/game_handoff/travel_graph.json")
        old = next(e for e in graph["edges"] if e["id"] == "EDGE-00334")
        if old["kind"] != "approach" or old["from"] != "CAR-027" or old["to"] != "J-town-153e92dc42":
            raise ValueError("Fraternity approach source changed; review the accepted property connector")
        road = next(e for e in graph["edges"] if e["id"] == "EDGE-00058")
        if road["kind"] != "road" or road["geometry"] != [[665, 380], [690, 380]]:
            raise ValueError("The existing road beside the fraternity changed; review its private approach")
        route_px = [[px, py], [px, 3945], [7440, 3945], [7440, 4020]]
        route = [[1000-y/12, 1000-x/12] for x, y in route_px]
        length = sum(math.dist(a, b) for a, b in zip(route, route[1:]))
        graph_node_patches["CAR-027"] = {"coordinates": coords, "map_id": "town"}
        graph_edge_patches[old["id"]] = {"geometry": route, "length_units": length,
            "cost": length * old["cost"] / old["length_units"],
            "reconciliation_basis": "User-accepted ADAPTATION site; same approach ID and public-road anchor; not collision geometry"}
        place_patches["CAR-027"].update(coordinates=coords, map_id="town", status="active", navigable=True,
            adaptation_property_binding=accepted, runtime_entry_status="logical_entry_bound_physical_door_unverified")
        rel = "sublocations/production/maps/SM-010/geometry_study/registered_floor_guide.json"
        repaired = stacked("SM-010", read(rel), next(v for v in spec["maps"] if v["id"] == "SM-010"), rel,
                           activate=True, reason="Accepted ADAPTATION property and approved layout; logical entry only")
        repaired = story_entry(repaired, "DELTA_EPSILON_DELTA", "OM-005")
        repaired["property_binding"] = accepted
        venue_patches["SM-010"].update(repaired)
    read("sublocations/production/checks/20261101-circulation/allcraft-source-evidence.json")

    # Bind the historical name to one property while retaining both archive IDs.
    alias_record = read("sublocations/production/maps/SM-058/source_adjudication.json")
    if (alias_record.get("kind") != "historical_name_alias"
            or alias_record.get("shared_geometry_map") != "SM-058"
            or alias_record.get("historical_name_map") != "SM-080"):
        raise ValueError("Witherhold historical alias adjudication changed; review binding")
    venue_patches.setdefault("SM-058", {}).update(
        additional_members=["CAR-166"], shared_geometry_map="SM-058",
        historical_names=["Woolsey Manor"], alias_evidence=alias_record)
    venue_patches.setdefault("SM-080", {}).update(
        alias_of_venue="SM-058", shared_geometry_map="SM-058", runtime_blocked=True,
        reason="Historical name of Witherhold; no independent property or runtime geometry",
        alias_evidence=alias_record)

    associations = []
    for v in spec["maps"]:
        for o in v.get("omens", []):
            oid = o["omen_id"]
            # Story destination and portable acquisition are different links.
            role = "story_setting" if o.get("linked_story_not_fixed_spawn") else "venue_context"
            if v["id"] == "SM-009" and oid == "OM-055":
                role = "superseded_parent_anchor"
            if v["id"] == "SM-087" and oid == "OM-058":
                role = "nearby_encounter"
            explicit_context = {
                ("SM-002", "OM-038"): "inventory_source",
                ("SM-002", "OM-107"): "inventory_source",
                ("SM-026", "OM-010"): "parent_context",
                ("SM-027", "OM-003"): "parent_context",
                ("SM-027", "OM-006"): "parent_context",
                ("SM-027", "OM-089"): "parent_context",
                ("SM-029", "OM-002"): "parent_context",
                ("SM-049", "OM-032"): "entrance_context",
                ("SM-051", "OM-001"): "entrance_context",
                ("SM-071", "OM-090"): "rejected_nonvenue",
                ("SM-095", "OM-023"): "regional_event",
            }
            role = explicit_context.get((v["id"], oid), role)
            if v["id"] == "SM-100":
                role = "river_route_context"
            associations.append({"venue_id": v["id"], "omen_id": oid, "relationship": role,
                                 "source_location": omen_index.get(oid, {}).get("Original Location"),
                                 "basis": "Original Location and linked_story_not_fixed_spawn; exact zone remains ADAPTATION"})
    queue_rel = "omen2story/Carousel_Omen_Canon_Audit_Queue_v0_3.md"
    queue = (root / queue_rel).read_bytes()
    inputs[queue_rel] = hashlib.sha256(queue).hexdigest()
    if "Part Two must inherit actual Part One ledger" not in queue.decode("utf-8-sig"):
        raise ValueError("Sequel continuity source policy changed; review prerequisite")
    return {"schema": "carousel-reconciled-game-references-v1", "classification": "source identities plus explicit adaptation links",
            "dossier_patches": {"OM-105": {"continuity": {
                "predecessor_omen_id": "OM-104", "predecessor_policy": "require_actual_concluded_run",
                "implementation_classification": "ADAPTATION enforcing saved canon-audit continuity requirement",
                "policy_source": queue_rel}}},
            "places": additions, "references": images, "image_gaps": gaps,
            "place_patches": place_patches,
            "omen_places": {oid: {"place_id": pid, "basis": omen_index[oid]["Original Location"],
                                   "source": omen_index[oid].get("Source / Chapter"),
                                   "coordinates_claimed": False} for oid, pid in omen_places.items()},
            "venue_patches": venue_patches, "associations": associations,
            "graph_node_patches": graph_node_patches, "graph_edge_patches": graph_edge_patches,
            "inputs": inputs, "studio_state_sha256": studio_sha,
            "human_approval_inferred": False, "illustrated_submaps_installed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("E:/Carousel"))
    args = parser.parse_args()
    data = build(args.source)
    target = args.source / "Asset_Reconciliation/game_reference.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Exported {len(data['places'])} reference identities, {len(data['references'])} selected images, "
          f"{len(data['image_gaps'])} explicit image gaps to {target}")
