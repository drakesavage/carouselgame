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
