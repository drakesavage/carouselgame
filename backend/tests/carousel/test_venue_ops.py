import json

import pytest

from app.db import transaction
from app.rules.engine import ChangeError
from tests.conftest import apply

GATE, BARN, CELLAR, HIDDEN = (f"SM-T01:{n}" for n in ("n01_farm_gate", "n02_barn", "n03_cellar_door", "n04_MINI-HIDDEN-ROOM"))


def at_farm(conn, cid):
    with transaction(conn):
        conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage) VALUES (?, 'OM-001', 'visible')", (cid,))
    apply(conn, cid, {"op": "travel", "to": "CAR-086"})


def zone_of(conn, cid):
    return conn.execute("SELECT zone_id FROM campaigns WHERE id = ?", (cid,)).fetchone()[0]


def clock(conn, cid):
    return conn.execute("SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,)).fetchone()[0]


def test_move_zone_follows_links(conn, campaign_id):
    at_farm(conn, campaign_id)
    with pytest.raises(ChangeError, match="no way from farm gate to cellar door"):
        apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert zone_of(conn, campaign_id) == GATE
    r = apply(conn, campaign_id, {"op": "move_zone", "to": "barn"})   # an exact label works too
    assert r["zone_id"] == BARN and zone_of(conn, campaign_id) == BARN
    apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert zone_of(conn, campaign_id) == CELLAR
    visits = {r[0] for r in conn.execute("SELECT zone_id FROM zone_visits WHERE campaign_id = ?", (campaign_id,))}
    assert visits == {GATE, BARN, CELLAR}
    ev = conn.execute("SELECT summary, visibility FROM events WHERE campaign_id = ? AND type = 'zone_entered' ORDER BY id DESC",
                      (campaign_id,)).fetchone()
    assert tuple(ev) == ("The group moves to cellar door", "player")


def test_move_zone_refusals(conn, campaign_id):
    with pytest.raises(ChangeError, match="not in a venue"):
        apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    at_farm(conn, campaign_id)
    with pytest.raises(ChangeError, match="unknown zone"):
        apply(conn, campaign_id, {"op": "move_zone", "to": "the attic"})
    with pytest.raises(ChangeError, match="unknown zone"):
        apply(conn, campaign_id, {"op": "move_zone", "to": "SM-T02:n01_platform"})   # another venue's zone


def test_move_zone_refused_in_the_car(conn, campaign_id):
    at_farm(conn, campaign_id)
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage = 'car' WHERE id = ?", (campaign_id,))
    with pytest.raises(ChangeError, match="car park"):
        apply(conn, campaign_id, {"op": "move_zone", "to": BARN})


def test_entering_omen_zone_sights_only(conn, campaign_id):
    at_farm(conn, campaign_id)
    apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    before = clock(conn, campaign_id)
    r = apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert r["sighted"] == ["OM-005"]
    assert conn.execute("SELECT stage FROM omen_states WHERE campaign_id = ? AND omen_id = 'OM-005'", (campaign_id,)).fetchone()[0] == "visible"
    assert conn.execute("SELECT COUNT(*) FROM stories WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0
    assert clock(conn, campaign_id) == before
    ev = conn.execute("SELECT summary, payload_json FROM events WHERE campaign_id = ? AND type = 'omen_sighted' ORDER BY id DESC",
                      (campaign_id,)).fetchone()
    payload = json.loads(ev["payload_json"])
    assert payload["omen_id"] == "OM-005" and payload["zone"] == "cellar door" and payload["node"] == "CAR-086"
    assert "A cellar door breathing" in ev["summary"]


def test_reentering_sights_nothing_new(conn, campaign_id):
    at_farm(conn, campaign_id)
    for z in (BARN, CELLAR, BARN):
        apply(conn, campaign_id, {"op": "move_zone", "to": z})
    assert apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})["sighted"] == []


def test_a_block_keeps_the_group_where_it_is(conn, campaign_id):
    at_farm(conn, campaign_id)
    apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    with transaction(conn):
        conn.execute("""INSERT INTO venue_overlays (campaign_id, venue_id, kind, a, b, reason, created_minutes)
                        VALUES (?, 'SM-T01', 'block', ?, ?, 'the door has swollen shut', 0)""", (campaign_id, BARN, CELLAR))
    with pytest.raises(ChangeError, match="cellar door is blocked: the door has swollen shut"):
        apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert zone_of(conn, campaign_id) == BARN
