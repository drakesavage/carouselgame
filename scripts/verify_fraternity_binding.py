"""Read-only checks across installed references, Studio geography and source roads."""
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.carousel.travel import plan_route

out = Path("E:/Carousel/Asset_Reconciliation")
source = json.loads((out.parent / "Illustrated_Map_Production/game_handoff/travel_graph.json").read_text(encoding="utf-8"))
accepted = json.loads((out / "fraternity-parcel-acceptance.json").read_text(encoding="utf-8"))
px, py = accepted["site_anchor_px"]
coords = [1000 - py / 12, 1000 - px / 12]
installs = []
for name in ["root-installation.json", "active-installation.json"]:
    receipt = json.loads((out / name).read_text(encoding="utf-8"))
    data = Path(receipt["backup"]).parent.parent
    with sqlite3.connect(f"file:{(data / 'reference.db').as_posix()}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        p = c.execute("SELECT north,west,px,py,status FROM places WHERE id='CAR-027'").fetchone()
        assert list(p[:2]) == coords and abs(p[2]-px) < 1e-6 and abs(p[3]-py) < 1e-6 and p[4] == "active"
        for edge in source["edges"]:
            row = c.execute("SELECT from_id,to_id,kind,cost,geometry_json FROM graph_edges WHERE id=?", (edge["id"],)).fetchone()
            assert tuple(row[:3]) == (edge["from"], edge["to"], edge.get("kind"))
            if edge["id"] != "EDGE-00334":
                assert row[3] == edge.get("cost") and json.loads(row[4]) == (edge.get("geometry") or [])
        for node in source["nodes"]:
            if node["id"] != "CAR-027":
                row = c.execute("SELECT north,west FROM graph_nodes WHERE id=?", (node["id"],)).fetchone()
                assert list(row) == (node.get("coordinates") or [None, None])
        route = plan_route(c, "J-town-153e92dc42", "CAR-027", set())
        assert route and route.edges == ["EDGE-00334"] and route.minutes == 8
        omen = c.execute("SELECT placed,zone_id,choice_action FROM venue_omens WHERE venue_id='SM-010' AND omen_id='OM-005'").fetchone()
        assert tuple(omen) == (1, "SM-010:guide_L0_outside_0", "knock:fraternity_house_door")
        assert c.execute("SELECT enterable FROM venues WHERE id='SM-049'").fetchone()[0]
        installs.append({"data_dir": str(data), "accepted_anchor_px": [px, py], "private_approach_minutes": route.minutes,
                         "all_other_edge_geometry_and_costs_unchanged": True, "all_other_node_positions_unchanged": True,
                         "omen_link_verified": True})
with sqlite3.connect("file:E:/Carousel/Location_Art_Production/data/studio.sqlite3?mode=ro", uri=True) as c:
    row = c.execute("SELECT record_json,approved_asset_id,release_eligible FROM locations WHERE id='CAR-027'").fetchone()
    record = json.loads(row[0])
    assert record["geography"]["atlas"]["record"]["coordinates"] == coords
    assert record["map_integration"]["world_anchor"] == [px, py]
    assert record["geography"]["inherited_atlas"]["record"]["coordinates"] == [665, 370]
    assert row[1] is None and row[2] == 0  # Property acceptance is not image approval or map-painter release.
(out / "fraternity-binding-verification.json").write_text(json.dumps({"ok": True, "installations": installs,
    "studio_geography_synchronized": True, "inherited_geography_preserved": True,
    "physical_map_release_held": True, "canonical_coordinates_claimed": False}, indent=2) + "\n", encoding="utf-8")
print("Verified both property bindings, private approach, Omen link and synchronized Studio geography; public roads unchanged")
