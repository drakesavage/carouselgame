from app.gm.orchestrator import WRITE_LOCK
from tests.carousel.test_rewards import AWARDS
from tests.carousel.test_story_ops import begin, seen
from tests.conftest import apply

ACTIVE_KEYS = {"id", "title", "premise", "phase", "phases", "camera", "cast", "revisions"}
CAST_KEYS = {"id", "name", "casting", "screen_moments"}
HISTORY_KEYS = {"id", "title", "ending", "started_minutes", "ended_minutes", "novelty", "cast"}


def test_no_story(client, campaign_id):
    assert client.get(f"/api/campaigns/{campaign_id}/story").json() == {"active": None}
    assert client.get(f"/api/campaigns/{campaign_id}/stories").json() == []


def test_active_story_shape_has_no_secrets(client, conn, campaign_id, pc):
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    apply(conn, campaign_id, {"op": "camera", "on": True, "focus": ["Mara"], "reason": "r"})
    a = client.get(f"/api/campaigns/{campaign_id}/story").json()["active"]
    assert set(a) == ACTIVE_KEYS
    assert (a["title"], a["premise"], a["phase"], a["camera"]) == ("Mini OM-001", "Something is wrong at the farm.", "Party", "on")
    assert a["phases"][0] == "Omen" and a["phases"][-1] == "The End" and len(a["phases"]) == 8
    assert all(set(c) == CAST_KEYS for c in a["cast"])
    assert {c["name"]: (c["casting"], c["screen_moments"]) for c in a["cast"]} == {"Mara Vance": ("lead", 1), "Jonah Reyes": ("support", 0)}
    assert a["revisions"] == []


def test_history_after_a_conclusion(client, conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    apply(conn, campaign_id, {"op": "conclude_story", "ending": "defeat", "reason": "r", "awards": AWARDS})
    assert client.get(f"/api/campaigns/{campaign_id}/story").json() == {"active": None}
    [h] = client.get(f"/api/campaigns/{campaign_id}/stories").json()
    assert set(h) == HISTORY_KEYS
    assert (h["title"], h["ending"], h["novelty"], sorted(h["cast"])) == ("Mini OM-001", "defeat", 2, ["Jonah Reyes", "Mara Vance"])


def test_card_shows_stars_and_points(client, conn, campaign_id, pc):
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    apply(conn, campaign_id, {"op": "conclude_story", "ending": "defeat", "reason": "r", "awards": AWARDS})
    card = client.get(f"/api/campaigns/{campaign_id}/characters/{pc}").json()
    assert (card["stars"], card["pending_stat_points"]) == (0, 1)


def test_spend_stat_point_endpoint(client, conn, campaign_id, pc):
    url = f"/api/campaigns/{campaign_id}/characters/{pc}/stat-point"
    assert client.post(url, json={"stat": "grit"}).status_code == 422          # no points yet
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    apply(conn, campaign_id, {"op": "conclude_story", "ending": "defeat", "reason": "r", "awards": AWARDS})
    assert WRITE_LOCK.acquire(timeout=1)
    try:
        assert client.post(url, json={"stat": "grit"}).status_code == 409
    finally:
        WRITE_LOCK.release()
    r = client.post(url, json={"stat": "grit"})
    assert r.status_code == 200
    row = conn.execute("SELECT grit, pending_stat_points FROM characters WHERE id = ?", (pc,)).fetchone()
    assert tuple(row) == (3, 0)
    assert client.post(url, json={"stat": "luck"}).status_code == 422
    assert client.post(f"/api/campaigns/{campaign_id}/characters/99999/stat-point", json={"stat": "grit"}).status_code == 404
