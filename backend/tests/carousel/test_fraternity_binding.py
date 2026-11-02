import json

import pytest

from app.carousel import venues
from app.carousel import refdb
from app.carousel.importer import IMPORTERS
from app.carousel.sources import SOURCE_FILES, load_sources
from app.carousel.travel import plan_route
from app.db import transaction
from app.rules.engine import ChangeError
from app.venue_api import player_venue
from tests.carousel.test_story_ops import on_turn, turn
from tests.conftest import apply


def test_fraternity_accepted_anchor_and_private_spur_preserve_public_roads(real_campaign, real_files):
    conn, cid = real_campaign
    p = conn.execute("SELECT north,west,px,py,status,in_town_map,raw_json FROM ref.places WHERE id='CAR-027'").fetchone()
    assert tuple(p[2:4]) == pytest.approx((7620, 3860))
    assert tuple(p[4:6]) == ("active", 1)
    assert not json.loads(p[6])["adaptation_property_binding"]["canonical_coordinates_claimed"]
    route = plan_route(conn, "J-town-153e92dc42", "CAR-027", set())
    assert route and route.nodes == ["J-town-153e92dc42", "CAR-027"]
    assert route.ticks == pytest.approx(17 / 6, abs=1e-6)
    assert route.minutes == 8  # Existing six-decimal tick and nearest-minute rounding policy.
    assert route.geometry_px[-1] == pytest.approx((7620, 3860))
    for e in real_files["travel_graph"].data["edges"]:
        row = conn.execute("SELECT from_id,to_id,kind,cost,geometry_json FROM ref.graph_edges WHERE id=?", (e["id"],)).fetchone()
        assert tuple(row[:3]) == (e["from"], e["to"], e.get("kind"))
        if e["id"] != "EDGE-00334":
            assert row[3] == e.get("cost") and json.loads(row[4]) == (e.get("geometry") or [])
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done',node_id='J-town-153e92dc42' WHERE id=?", (cid,))
    apply(conn, cid, {"op": "travel", "to": "CAR-027"})
    assert venues.position(conn, cid) == ("SM-010", "SM-010:guide_L0_outside_0")
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (cid,)).fetchone()
    placed = conn.execute("SELECT zone_id,placed,choice_action FROM ref.venue_omens WHERE venue_id='SM-010' AND omen_id='OM-005'").fetchone()
    assert tuple(placed) == ("SM-010:guide_L0_outside_0", 1, "knock:fraternity_house_door")


@pytest.mark.parametrize("vid,pid,oid,action,clock", [
    ("SM-010", "CAR-027", "OM-005", "knock:fraternity_house_door", 600),
    ("SM-049", "CAR-085", "OM-032", "attempt_open:olde_hill_gate_when_woman_present", 1163),
])
def test_registry_choice_opens_only_its_story_entry_and_does_not_trap_egress(real_campaign, vid, pid, oid, action, clock):
    conn, cid = real_campaign
    outside, hall = vid + ":guide_L0_outside_0", vid + ":guide_L0_hall"
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done',node_id=?,game_time_minutes=? WHERE id=?", (pid, clock, cid))
        venues.enter(conn, cid, pid)
    apply(conn, cid, {"op": "sight_omen", "omen": oid, "how": "Source-qualified cue at its entrance"})
    apply(conn, cid, {"op": "examine_omen", "omen": oid})
    with pytest.raises(ChangeError, match="door is closed"):
        apply(conn, cid, {"op": "move_zone", "to": hall})
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (cid,)).fetchone()
    plan = player_venue(conn, cid)["plan"]
    assert not next(a for a in plan["zones"] if a["id"] == hall)["can_move"]
    conn.execute("SAVEPOINT unrelated_story")
    try:
        other = conn.execute("SELECT id,story_lock FROM ref.omens WHERE record_type='Playable Omen' AND id!=? LIMIT 1", (oid,)).fetchone()
        conn.execute("INSERT INTO stories(campaign_id,omen_id,story_lock,title,status,phase) VALUES (?,?,?,'Unrelated fixture','active','Party')", (cid, other["id"], other["story_lock"]))
        assert not next(a for a in player_venue(conn, cid)["plan"]["zones"] if a["id"] == hall)["can_move"]
    finally:
        conn.execute("ROLLBACK TO unrelated_story")
        conn.execute("RELEASE unrelated_story")
    player_turn = turn(conn, cid)
    with transaction(conn):
        conn.execute("UPDATE turns SET player_input=? WHERE id=?", (
            "I knock at the fraternity house door." if oid == "OM-005" else "I try to open the gate while the woman is there.", player_turn))
    begun = on_turn(conn, cid, player_turn, {"op": "begin_story", "omen": oid, "action": action,
        "cast": [{"character": "Mara", "casting": "lead"}], "reason": "The player explicitly commits the registry Choice"})
    assert next(a for a in player_venue(conn, cid)["plan"]["zones"] if a["id"] == hall)["can_move"]
    apply(conn, cid, {"op": "block_passage", "a": outside, "b": hall, "reason": "staff barrier"})
    with pytest.raises(ChangeError, match="staff barrier"):
        apply(conn, cid, {"op": "move_zone", "to": hall})
    apply(conn, cid, {"op": "open_passage", "a": outside, "b": hall, "reason": "barrier removed"})
    apply(conn, cid, {"op": "move_zone", "to": hall})
    # A concluded-story fixture verifies that an entry condition never traps a
    # surviving actor inside; full ending/reward rules have their own tests.
    with transaction(conn):
        conn.execute("UPDATE stories SET status='concluded',phase='The End',ending='survive_without_victory',ended_minutes=? WHERE id=?", (clock, begun["story_id"]))
    apply(conn, cid, {"op": "move_zone", "to": outside})
    with pytest.raises(ChangeError, match="door is closed"):
        apply(conn, cid, {"op": "move_zone", "to": hall})
    assert conn.execute("SELECT COUNT(*) FROM stories WHERE campaign_id=?", (cid,)).fetchone()[0] == 1


@pytest.mark.parametrize("unknown", [False, True])
def test_navigation_reconciliation_cannot_silently_replace_roads_or_saved_ids(mini_source, unknown):
    files = load_sources(mini_source)
    road = next(e for e in files["travel_graph"].data["edges"] if e["kind"] != "approach")
    correction = {"graph_node_patches": {"UNKNOWN-SAVED-ID": {"coordinates": [1, 2]}}} if unknown else {
        "graph_edge_patches": {road["id"]: {"geometry": [[1, 2], [3, 4]]}}}
    (mini_source / SOURCE_FILES["reconciliation"]).write_text(json.dumps(correction), encoding="utf-8")
    conn = refdb.connect(":memory:")
    refdb.init(conn)
    try:
        with pytest.raises(ValueError, match="unknown saved graph ID" if unknown else "private approach spurs"):
            for importer in IMPORTERS:
                importer(conn, load_sources(mini_source))
    finally:
        conn.close()
