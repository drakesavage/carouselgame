from collections import deque
import json

import pytest

from app.carousel import venues
from app.db import transaction
from app.rules.engine import ChangeError
from tests.conftest import apply


def zid(key):
    return "SM-087:route_" + key


def test_allcraft_real_runtime_rear_stairs_reach_every_floor_and_roof(real_campaign):
    conn, cid = real_campaign
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done',node_id='CAR-202' WHERE id=?", (cid,))
        entry = venues.enter(conn, cid, "CAR-202")
    assert entry["zone_id"] == zid("street")
    start = zid("F1_Grain_Matter_dining")
    with transaction(conn):
        conn.execute("UPDATE campaigns SET zone_id=? WHERE id=?", (start, cid))
    target = zid("ROOF")
    todo, previous = deque([start]), {start: None}
    while todo:
        here = todo.popleft()
        for way in venues.neighbours(conn, cid, here):
            z = way["zone_id"]
            if z not in previous and "front_landing" not in z and not way["blocked_reason"]:
                previous[z] = here
                todo.append(z)
    assert target in previous
    for level in [2, 3, 4]:
        assert zid(f"F{level}_Bedroom_A") in previous
    path, cursor = [], target
    while cursor != start:
        path.append(cursor)
        cursor = previous[cursor]
    for node in reversed(path):
        apply(conn, cid, {"op": "move_zone", "to": node})
    assert venues.position(conn, cid)[1] == target
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (cid,)).fetchone()
    assert not conn.execute("SELECT 1 FROM omen_states WHERE campaign_id=?", (cid,)).fetchone()
    assert conn.execute("SELECT floor FROM ref.venue_zones WHERE id=?", (target,)).fetchone()[0] == 5
    assert zid("F1_hall") not in {w["zone_id"] for w in venues.neighbours(conn, cid, zid("F1_front_landing"))}
    assert all("front_landing" not in w["zone_id"] for w in venues.neighbours(conn, cid, target))


def test_allcraft_archived_positions_remain_resolvable_without_guessing_new_room(real_campaign):
    conn, cid = real_campaign
    old = "SM-087:n05_loft_bedrooms"
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done',node_id='CAR-202',venue_id='SM-087',zone_id=? WHERE id=?", (old, cid))
    with pytest.raises(ChangeError, match="archived layout"):
        apply(conn, cid, {"op": "move_zone", "to": zid("F2_Bedroom_A")})
    assert venues.position(conn, cid)[1] == old
    raw = json.loads(conn.execute("SELECT raw_json FROM ref.venues WHERE id='SM-087'").fetchone()[0])
    assert not raw["illustrated_geometry_installed"]
    assert not raw["reconciliation"]["topology_basis"]["safety_scope_automatically_applied"]
    from app.venue_api import player_venue
    assert player_venue(conn, cid)["plan"] is None


def test_approved_plan_projects_only_discovered_areas_and_uses_legal_movement(real_campaign):
    from app.venue_api import player_venue
    conn, cid = real_campaign
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done',node_id='CAR-202' WHERE id=?", (cid,))
        venues.enter(conn, cid, "CAR-202")
    plan = player_venue(conn, cid)["plan"]
    assert {a["id"] for a in plan["zones"]} == {zid("street"), zid("F1_hall"), zid("F1_front_landing")}
    assert all(a["level"] == 1 for a in plan["zones"])
    assert all(secret not in json.dumps(plan) for secret in ["Bedroom", "Kitchen", "ROOF", "Writ", "source_writ_scope"])
    apply(conn, cid, {"op": "move_zone", "to": zid("F1_hall")})
    later = player_venue(conn, cid)["plan"]
    assert any(a["label"] == "Kitchen" and a["can_move"] for a in later["zones"])
    assert not any(a["level"] > 1 for a in later["zones"])
    assert next(a for a in later["zones"] if a["current"])["id"] == zid("F1_hall")


def test_plan_requires_layout_approval_and_respects_passage_blocks(real_campaign):
    from app.venue_api import player_venue
    conn, cid = real_campaign
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done',node_id='CAR-202' WHERE id=?", (cid,))
        venues.enter(conn, cid, "CAR-202")
        conn.execute("""INSERT INTO venue_overlays(campaign_id,venue_id,kind,a,b,reason,created_minutes)
            VALUES (?,'SM-087','block',?,?,'storm debris',0)""", (cid, zid("street"), zid("F1_hall")))
    plan = player_venue(conn, cid)["plan"]
    hall = next(a for a in plan["zones"] if a["id"] == zid("F1_hall"))
    assert not hall["can_move"] and hall["blocked"] == "storm debris"
    conn.execute("SAVEPOINT no_approval")
    try:
        raw = json.loads(conn.execute("SELECT raw_json FROM ref.venues WHERE id='SM-087'").fetchone()[0])
        raw["reconciliation"]["player_plan"]["layout_human_approved"] = False
        conn.execute("UPDATE ref.venues SET raw_json=? WHERE id='SM-087'", (json.dumps(raw),))
        assert player_venue(conn, cid)["plan"] is None
    finally:
        conn.execute("ROLLBACK TO no_approval")
        conn.execute("RELEASE no_approval")
