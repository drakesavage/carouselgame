import json

import pytest

from app.carousel import venues
from app.db import transaction
from app.rules.engine import ChangeError
from app.venue_api import player_venue
from tests.conftest import apply


def test_sanatorium_approved_floors_are_navigable_but_records_stay_locked(real_campaign):
    conn, cid = real_campaign
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done',node_id='CAR-117' WHERE id=?", (cid,))
        arrival = venues.enter(conn, cid, "CAR-117")
    assert arrival and arrival["venue_id"] == "SM-068"
    assert {a["label"] for a in player_venue(conn, cid)["plan"]["zones"]} == {"Entrance", "Hall"}
    hall = "SM-068:guide_L0_hall"
    apply(conn, cid, {"op": "move_zone", "to": hall})
    record = next(a for a in player_venue(conn, cid)["plan"]["zones"] if a["label"] == "Locked records")
    assert not record["can_move"] and record["blocked"]
    before = conn.execute("SELECT game_time_minutes FROM campaigns WHERE id=?", (cid,)).fetchone()[0]
    with pytest.raises(ChangeError, match="no usable way"):
        apply(conn, cid, {"op": "move_zone", "to": record["id"]})
    assert venues.position(conn, cid)[1] == hall
    for dest in ["guide_L0_stairs", "guide_L1_stairs", "guide_L1_hall"]:
        apply(conn, cid, {"op": "move_zone", "to": "SM-068:" + dest})
    back = conn.execute("SELECT id,floor FROM ref.venue_zones WHERE venue_id='SM-068' AND label='Back ward' AND area_type!='retired_reference'").fetchone()
    assert back and back["floor"] == 1
    apply(conn, cid, {"op": "move_zone", "to": back["id"]})
    assert conn.execute("SELECT game_time_minutes FROM campaigns WHERE id=?", (cid,)).fetchone()[0] == before
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (cid,)).fetchone()


@pytest.mark.parametrize("vid,expected_floors", [("SM-057", {-1, 0, 1})])
def test_approved_layout_repairs_do_not_bypass_unresolved_parcel_or_access_binding(ref_real, vid, expected_floors):
    v = ref_real.execute("SELECT enterable,raw_json FROM venues WHERE id=?", (vid,)).fetchone()
    assert not v["enterable"]
    raw = json.loads(v["raw_json"])
    assert raw["reconciliation"]["topology_basis"]["layout_human_approved"]
    zones = ref_real.execute("SELECT id,floor,area_type FROM venue_zones WHERE venue_id=? AND area_type!='retired_reference'", (vid,)).fetchall()
    assert {r["floor"] for r in zones} == expected_floors
    assert all(r["floor"] == 0 for r in zones if r["area_type"] == "exterior")
    assert not ref_real.execute("SELECT 1 FROM venue_omens WHERE venue_id=? AND placed=1", (vid,)).fetchone()
