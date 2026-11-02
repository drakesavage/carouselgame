import json

import pytest

from app.carousel import refdb
from app.carousel.importer import IMPORTERS
from app.carousel.sources import SOURCE_FILES, SourceError, load_sources
from app.carousel.validate_assets import validate_assets


def test_sunken_cradle_sequel_has_explicit_predecessor_without_changing_public_card(ref_real):
    row = ref_real.execute("SELECT sealed_json,dossier_json FROM omen_beats WHERE omen_id='OM-105'").fetchone()
    assert json.loads(row[0])["continuity"]["predecessor_omen_id"] == "OM-104"
    assert "predecessor_omen_id" not in row[1]


def test_woolsey_shares_witherhold_property_without_second_runtime_layout(ref_real):
    assert ref_real.execute("SELECT venue_id FROM venue_places WHERE place_id='CAR-166'").fetchone()[0] == "SM-058"
    historical = ref_real.execute("SELECT enterable,raw_json FROM venues WHERE id='SM-080'").fetchone()
    assert not historical["enterable"]
    assert json.loads(historical["raw_json"])["alias_of_venue"] == "SM-058"
    assert not ref_real.execute("SELECT 1 FROM venue_places WHERE venue_id='SM-080'").fetchone()
    # Existing zone IDs remain available to the saved-reference guard.
    assert ref_real.execute("SELECT 1 FROM venue_zones WHERE venue_id='SM-080'").fetchone()
    ref_real.execute("SAVEPOINT invalid_alias")
    try:
        ref_real.execute("UPDATE venues SET enterable=1 WHERE id='SM-080'")
        with pytest.raises(ValueError, match="independent historical venue"):
            validate_assets(ref_real)
    finally:
        ref_real.execute("ROLLBACK TO invalid_alias")
        ref_real.execute("RELEASE invalid_alias")


def test_source_identity_is_imported_without_fabricated_coordinates(ref_real):
    for pid in ["AUD-LOC-JUST-DESSERTS", "AUD-LOC-BETTER-BEA-WARES", "AUD-LOC-COMMUNITY-POOL"]:
        p = ref_real.execute("SELECT north,west,status,in_town_map FROM places WHERE id=?", (pid,)).fetchone()
        assert tuple(p) == (None, None, "source_only", 0)
        assert not ref_real.execute("SELECT 1 FROM graph_nodes WHERE location_id=?", (pid,)).fetchone()


def test_bowling_membership_and_independent_omens(ref_real):
    assert ref_real.execute("SELECT venue_id FROM venue_places WHERE place_id='AUD-LOC-BOWLING-ALLEY'").fetchone()[0] == "SM-086"
    assert ref_real.execute("SELECT enterable FROM venues WHERE id='SM-086'").fetchone()[0] == 1
    ids = {r[0] for r in ref_real.execute("SELECT omen_id FROM venue_omens WHERE venue_id='SM-086'")}
    assert ids == {"OM-019", "OM-020", "OM-021", "OM-022", "OM-071"}
    raw = json.loads(ref_real.execute("SELECT raw_json FROM venues WHERE id='SM-086'").fetchone()[0])
    assert raw["navigation_mode"] == "text_graph" and not raw["illustrated_geometry_installed"]
    assert any(p["from_node_id"] == "AUD-LOC-BOWLING-ALLEY" and p["state"] == "open" for p in raw["logical_portals"])


def test_source_corrections_and_nonvenues_cannot_be_entered(ref_real):
    for vid in ["SM-022", "SM-052", "SM-070", "SM-071", "SM-077", "SM-078", "SM-103", "SM-104", "SM-105", "SM-106"]:
        v = ref_real.execute("SELECT enterable,raw_json FROM venues WHERE id=?", (vid,)).fetchone()
        assert not v["enterable"], vid
        assert json.loads(v["raw_json"])["reconciliation"], vid
        assert not ref_real.execute("SELECT 1 FROM venue_omens WHERE venue_id=? AND placed=1", (vid,)).fetchone()


def test_museum_omen_is_at_museum_and_museum_arrives_through_city_hall(ref_real):
    assert ref_real.execute("SELECT place_id FROM omens WHERE id='OM-053'").fetchone()[0] == "CAR-014"
    m = ref_real.execute("SELECT north,west,raw_json FROM places WHERE id='CAR-014'").fetchone()
    assert (m["north"], m["west"]) == (485, 490)
    assert json.loads(m["raw_json"])["parent_location_id"] == "CAR-003"


def test_story_destination_is_not_static_spawn(ref_real):
    r = ref_real.execute("SELECT relationship FROM asset_relationships WHERE venue_id='SM-073' AND omen_id='OM-104'").fetchone()
    assert r[0] == "story_setting"
    assert ref_real.execute("SELECT placed FROM venue_omens WHERE venue_id='SM-073' AND omen_id='OM-104'").fetchone()[0] == 0


def test_stale_reconciliation_is_refused(mini_source):
    p = mini_source / SOURCE_FILES["reconciliation"]
    p.write_text(json.dumps({"inputs": {SOURCE_FILES["gazetteer"]: "0" * 64}}), encoding="utf-8")
    with pytest.raises(SourceError, match="reconciliation input changed"):
        load_sources(mini_source)


def test_unknown_reconciled_omen_place_fails_instead_of_using_wrong_parent(mini_source):
    p = mini_source / SOURCE_FILES["reconciliation"]
    p.write_text(json.dumps({"omen_places": {"OM-001": {"place_id": "MISSING"}}}), encoding="utf-8")
    c = refdb.connect(":memory:")
    refdb.init(c)
    with pytest.raises(ValueError, match="unknown place"):
        for fn in IMPORTERS:
            fn(c, load_sources(mini_source))
    c.close()


def test_broken_zone_and_travel_links_refuse_publication(ref_real):
    ref_real.execute("SAVEPOINT broken_assets")
    try:
        ref_real.execute("UPDATE graph_edges SET to_id='MISSING' WHERE id=(SELECT id FROM graph_edges LIMIT 1)")
        with pytest.raises(ValueError, match="travel edge endpoint"):
            validate_assets(ref_real)
    finally:
        ref_real.execute("ROLLBACK TO broken_assets")
        ref_real.execute("RELEASE broken_assets")


def test_corrected_story_sites_are_not_normal_town_pins(ref_real):
    for pid in ["CAR-039", "CAR-088", "CAR-148", "CAR-161", "CAR-163", "CAR-151", "CAR-152", "CAR-153"]:
        p = ref_real.execute("SELECT north,west,status,in_town_map FROM places WHERE id=?", (pid,)).fetchone()
        assert tuple(p) == (None, None, "source_only", 0), pid
        n = ref_real.execute("SELECT requires_json FROM graph_nodes WHERE location_id=?", (pid,)).fetchone()
        if n:
            assert "source_topology_pending:" + pid in json.loads(n[0])
