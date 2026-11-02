"""Plan 7 Task 6: players see the residents around them and the Lodge roster; unmet residents stay unseen."""
import uuid

from app.campaigns import create
from tests.carousel.test_around import stand
from tests.conftest import apply


def lodge(conn):
    cid = create(conn, name="Lodge", character="Mara", opening=False, location="Dyer's Lodge")
    stand(conn, cid, "CAR-047")
    return cid


def rid(conn, cid, ref):
    return conn.execute("SELECT c.id FROM characters c JOIN npc_profiles p ON p.character_id = c.id WHERE c.campaign_id = ? AND p.ref_id = ?",
                        (cid, ref)).fetchone()[0]


def test_around_lists_met_residents_only(client, conn):
    cid = lodge(conn)
    assert client.get(f"/api/campaigns/{cid}/around").json() == []
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    assert client.get(f"/api/campaigns/{cid}/around").json() == [{"id": rid(conn, cid, "CAR-NPC-003"), "name": "Valerie Choi", "role": "Scout"}]
    stand(conn, cid, "CAR-002")
    assert client.get(f"/api/campaigns/{cid}/around").json() == []
    assert client.get("/api/campaigns/999/around").status_code == 404


def test_roster_shows_last_seen_and_known_lines(client, conn):
    cid = lodge(conn)
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003", "location": "the Lodge porch"})
    roster = client.get(f"/api/campaigns/{cid}/residents").json()
    assert roster == [{"id": rid(conn, cid, "CAR-NPC-003"), "name": "Valerie Choi", "role": "Scout", "last_seen": "the Lodge porch",
                       "in_party": False, "known": ["Knows the MINI-INTRO-LINE trails past the boathouse."]}]
    apply(conn, cid, {"op": "recruit_resident", "resident": "Valerie Choi", "reason": "trails"})
    assert client.get(f"/api/campaigns/{cid}/residents").json()[0]["in_party"] is True


def test_talk_needs_them_here(client, conn):
    cid = lodge(conn)
    v = rid(conn, cid, "CAR-NPC-003")
    assert client.post(f"/api/campaigns/{cid}/residents/{v}/talk", json={"idempotency_key": uuid.uuid4().hex}).status_code == 409   # unmet
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    stand(conn, cid, "CAR-002")
    assert client.post(f"/api/campaigns/{cid}/residents/{v}/talk", json={"idempotency_key": uuid.uuid4().hex}).status_code == 409   # away
    stand(conn, cid, "CAR-047")
    r = client.post(f"/api/campaigns/{cid}/residents/{v}/talk", json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    t = conn.execute("SELECT kind, player_input FROM turns WHERE id = ?", (r.json()["turn"]["turn_id"],)).fetchone()
    assert tuple(t) == ("talk_resident", "I go over to talk to Valerie Choi")
    assert client.post(f"/api/campaigns/{cid}/residents/123456/talk", json={"idempotency_key": uuid.uuid4().hex}).status_code == 404


def test_talk_routes_to_conversation():
    from app.gm.router import classify_turn
    assert classify_turn("I go over to talk to Valerie Choi", False, turn_kind="talk_resident")[0] == "talk"
