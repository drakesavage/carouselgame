import uuid

from app.db import transaction
from app.gm.orchestrator import WRITE_LOCK
from app.gm.router import classify_turn


def seen(conn, cid, *omens):
    with transaction(conn):
        for o in omens:
            conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage) VALUES (?, ?, 'visible')", (cid, o))


def where(conn, cid):
    return tuple(conn.execute("SELECT node_id, game_time_minutes FROM campaigns WHERE id = ?", (cid,)).fetchone())


def turns(conn, cid):
    return conn.execute("SELECT COUNT(*) FROM turns WHERE campaign_id = ?", (cid,)).fetchone()[0]


def travel(client, cid, key=None, **body):
    return client.post(f"/api/campaigns/{cid}/travel", json={**body, "idempotency_key": key or uuid.uuid4().hex})


def test_travel_moves_and_starts_the_arrival_turn(client, conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-002", "OM-003")
    r = travel(client, campaign_id, to="CAR-047")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["travel"]["arrived"] == "CAR-047" and body["turn"]["status"] == "completed"
    assert body["turn"]["result"]["narration"]
    assert where(conn, campaign_id) == ("CAR-047", 645)
    t = conn.execute("SELECT player_input, kind FROM turns WHERE id = ?", (body["turn"]["turn_id"],)).fetchone()
    assert tuple(t) == ("[arrival] We arrive at Dyer's Lodge.", "arrival")
    ev = conn.execute("SELECT turn_id FROM events WHERE campaign_id = ? AND type = 'travel'", (campaign_id,)).fetchone()
    assert ev["turn_id"] == body["turn"]["turn_id"]


def test_travel_refused_changes_nothing(client, conn, campaign_id):
    before, n = where(conn, campaign_id), turns(conn, campaign_id)
    r = travel(client, campaign_id, to="CAR-099")
    assert r.status_code == 422 and "akers_story" in r.json()["detail"]
    assert where(conn, campaign_id) == before and turns(conn, campaign_id) == n


def test_travel_refused_while_a_turn_runs(client, conn, campaign_id):
    before = where(conn, campaign_id)
    assert WRITE_LOCK.acquire(timeout=1)
    try:
        assert travel(client, campaign_id, to="CAR-047").status_code == 409
    finally:
        WRITE_LOCK.release()
    assert where(conn, campaign_id) == before


def test_same_key_moves_once(client, conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-002", "OM-003")
    key = uuid.uuid4().hex
    first = travel(client, campaign_id, key, to="CAR-047").json()
    again = travel(client, campaign_id, key, to="CAR-047")
    assert again.status_code == 200 and again.json()["turn"]["turn_id"] == first["turn"]["turn_id"] and again.json()["turn"]["replayed"]
    assert where(conn, campaign_id) == ("CAR-047", 645)
    assert conn.execute("SELECT COUNT(*) FROM events WHERE campaign_id = ? AND type = 'travel'", (campaign_id,)).fetchone()[0] == 1


def test_interrupted_travel_routes_as_arrival_omen(client, conn, campaign_id):
    seen(conn, campaign_id, "OM-002", "OM-003")
    body = travel(client, campaign_id, to="CAR-086").json()
    assert body["travel"]["interrupted"]["omen_id"] == "OM-001"
    t = conn.execute("SELECT player_input, kind FROM turns WHERE id = ?", (body["turn"]["turn_id"],)).fetchone()
    assert t["kind"] == "arrival_omen" and t["player_input"] == "[arrival] On the way to Farm, we stop: A scarecrow facing the wrong way."


def test_arrival_kinds_route_by_kind():
    assert classify_turn("[arrival] We arrive at Dyer's Lodge.", False, frozenset(), "arrival")[0] == "talk"
    assert classify_turn("[arrival] On the way, we stop: blood.", False, frozenset(), "arrival_omen")[0] == "action"
    assert classify_turn("[arrival] We arrive at Dyer's Lodge.", True, frozenset(), "arrival")[0] == "action"   # a story is active


def get_map(client, cid):
    r = client.get(f"/api/campaigns/{cid}/map")
    assert r.status_code == 200, r.text
    return r.json()


def test_map_shows_only_known_places(client, conn, campaign_id):
    m = get_map(client, campaign_id)
    assert {p["id"] for p in m["known"]} == {"CAR-047", "CAR-002", "CAR-006"}
    square = next(p for p in m["known"] if p["id"] == "CAR-002")
    assert square["safe"] is True and square["image"] == "/location-art/identity_images/CAR-002_identity_v001.png"
    assert m["position"]["node_id"] == "CAR-002" and m["position"]["place_id"] == "CAR-002" and m["position"]["label"] == "Town Square"
    assert m["omens"] == [] and m["last_route"] is None
    assert m["tiles"]["template"] == "/tiles/{z}/{x}/{y}.webp" and m["tiles"]["size"] == 12000
    assert "UNKNOWNPLACE-HOTEL-8" not in client.get(f"/api/campaigns/{campaign_id}/map").text


def test_travel_reveals_the_farm_and_its_omen(client, conn, campaign_id):
    seen_before = get_map(client, campaign_id)
    assert seen_before["omens"] == []
    travel(client, campaign_id, to="CAR-086")
    m = get_map(client, campaign_id)
    assert "CAR-086" in {p["id"] for p in m["known"]}
    assert m["position"]["node_id"] == "CAR-086"
    sighted = {o["id"]: o for o in m["omens"]}
    assert "OM-001" in sighted and sighted["OM-001"]["description"] == "A scarecrow facing the wrong way" and sighted["OM-001"]["stage"] == "visible"
    assert m["last_route"]["geometry_px"] and m["last_route"]["interrupted"]["omen_id"] in sighted
    assert "UNKNOWNPLACE-HOTEL-8" not in client.get(f"/api/campaigns/{campaign_id}/map").text


def test_old_known_world_route_is_gone(client, campaign_id):
    assert client.get(f"/api/campaigns/{campaign_id}/places").status_code == 404


def test_location_art_is_served_read_only(client):
    assert client.get("/location-art/%2e%2e/data/review_reference_index.json").status_code == 404
