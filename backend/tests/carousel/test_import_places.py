import json

from app.carousel.import_places import place_by_name, resolve_place
from app.carousel.refdb import count


def test_place_counts(ref_real):
    assert count(ref_real, "places") == 311   # installed geography plus source-only identities; no invented pins
    assert count(ref_real, "places", "in_town_map = 1") == 195  # Fraternity now uses its accepted ADAPTATION parcel.
    assert count(ref_real, "graph_nodes") == 515
    assert count(ref_real, "graph_edges") == 552
    assert count(ref_real, "place_aliases") == 8


def test_rejected_and_alias_records_are_kept(ref_real):
    got = dict(ref_real.execute("SELECT status, COUNT(*) FROM places GROUP BY status").fetchall())
    assert got == {"active": 217, "alias": 8, "rejected": 5, "source_only": 81}


def test_aliases_resolve(ref_real):
    assert resolve_place(ref_real, "CAR-017") == "CAR-016"
    assert resolve_place(ref_real, "CAR-016") == "CAR-016"
    assert resolve_place(ref_real, "CAR-999") is None
    for (target,) in ref_real.execute("SELECT target_id FROM place_aliases"):
        assert resolve_place(ref_real, target) == target   # every alias target exists


def test_place_by_name(ref_real):
    assert place_by_name(ref_real, "Town Square") == "CAR-002"
    assert place_by_name(ref_real, "  town   SQUARE ") == "CAR-002"
    assert place_by_name(ref_real, "No Such Place") is None


def test_flags_preserved(ref_real):
    e = ref_real.execute("SELECT requires_json, directed FROM graph_edges WHERE id = 'EDGE-00001'").fetchone()
    assert json.loads(e[0]) == [] and e[1] == 0
    gated = ref_real.execute("SELECT requires_json FROM graph_edges WHERE id = 'EDGE-00145'").fetchone()
    assert json.loads(gated[0]) == ["western_passage"]
    assert count(ref_real, "graph_edges", "requires_json != '[]'") == 132
    assert count(ref_real, "graph_edges", "directed = 1") == 1


def test_pixels_match_handoff(ref_real, real_files):
    anchors = real_files["pixel_anchors"].data["locations"]
    assert len(anchors) == 195
    for a in anchors:
        px, py = ref_real.execute("SELECT px, py FROM places WHERE id = ?", (a["id"],)).fetchone()
        override = real_files["reconciliation"].data.get("place_patches", {}).get(a["id"], {})
        if override.get("adaptation_property_binding"):
            assert override["adaptation_property_binding"]["actor"] == "human"
            assert not override["adaptation_property_binding"]["canonical_coordinates_claimed"]
            from app.carousel.coords import to_pixel
            assert (px, py) == to_pixel(*override["coordinates"])
            continue
        if "coordinates" in override and override["coordinates"] is None:
            assert (px, py) == (None, None)
            assert override["status"] == "source_only" and override.get("source_adjudication")
            continue
        assert abs(px - a["image_px"][0]) < 0.5 and abs(py - a["image_px"][1]) < 0.5, a["id"]
