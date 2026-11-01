import uuid

from app.gm.router import classify_turn
from tests.carousel.test_map_api import seen, travel, turns
from tests.carousel.test_venue_ops import BARN, CELLAR, GATE, HIDDEN


def move(client, cid, to, key=None):
    return client.post(f"/api/campaigns/{cid}/venue/move", json={"to": to, "idempotency_key": key or uuid.uuid4().hex})


def at_barn(client, conn, cid):
    seen(conn, cid, "OM-001")
    assert travel(client, cid, to="CAR-086").status_code == 200
    r = move(client, cid, BARN)
    assert r.status_code == 200, r.text
    return r


def test_no_venue_on_the_square(client, campaign_id):
    r = client.get(f"/api/campaigns/{campaign_id}/venue")
    assert r.status_code == 200 and r.json() is None


def test_venue_at_the_barn(client, conn, campaign_id):
    at_barn(client, conn, campaign_id)
    v = client.get(f"/api/campaigns/{campaign_id}/venue").json()
    assert v["title"] == "Farm grounds" and v["zone"] == {"id": BARN, "label": "barn"}
    assert v["ways"] == [{"id": GATE, "label": "farm gate", "blocked": None}, {"id": CELLAR, "label": "cellar door", "blocked": None}]
    assert {z["id"] for z in v["seen"]} == {GATE, BARN}
    assert (v["entry"], v["exit"]) == ("farm gate", "barn")
    assert "MINI-HIDDEN-ROOM" not in str(v) and "OM-" not in str(v)


def test_a_move_with_no_way_is_422(client, conn, campaign_id):
    at_barn(client, conn, campaign_id)
    before = turns(conn, campaign_id)
    r = move(client, campaign_id, HIDDEN)
    assert r.status_code == 422 and "no way from" in r.json()["detail"]
    assert turns(conn, campaign_id) == before


def test_the_same_key_twice_is_one_turn(client, conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    travel(client, campaign_id, to="CAR-086")
    before = turns(conn, campaign_id)
    a, b = move(client, campaign_id, BARN, key="same-key-1234"), move(client, campaign_id, BARN, key="same-key-1234")
    assert a.status_code == b.status_code == 200
    assert a.json()["turn"]["turn_id"] == b.json()["turn"]["turn_id"]
    assert turns(conn, campaign_id) == before + 1
    t = conn.execute("SELECT player_input, kind FROM turns WHERE id = ?", (a.json()["turn"]["turn_id"],)).fetchone()
    assert tuple(t) == ("(The group moves to barn.)", "zone")


def test_a_move_into_an_omen_room_is_a_zone_omen_turn(client, conn, campaign_id):
    at_barn(client, conn, campaign_id)
    r = move(client, campaign_id, CELLAR)
    assert r.status_code == 200 and r.json()["move"]["sighted"] == ["OM-005"]
    kind = conn.execute("SELECT kind FROM turns WHERE id = ?", (r.json()["turn"]["turn_id"],)).fetchone()[0]
    assert kind == "zone_omen"


def test_zone_kinds_route_by_kind():
    assert classify_turn("(The group moves to barn.)", False, turn_kind="zone")[0] == "talk"
    assert classify_turn("(The group moves to cellar door.)", False, turn_kind="zone_omen")[0] == "action"
