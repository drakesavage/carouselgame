import uuid

from app.db import transaction

CAMP_ONLY = "You can only save at camp (Dyer's Lodge, between stories)"


def at(conn, cid, node):
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id = ? WHERE id = ?", (node, cid))


def autos(conn, cid):
    return conn.execute("SELECT COUNT(*) FROM save_points WHERE campaign_id = ? AND kind = 'auto'", (cid,)).fetchone()[0]


def play(client, cid, text="I wait"):
    r = client.post(f"/api/campaigns/{cid}/turns", json={"text": text, "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text


def test_no_autosave_away_from_camp(client, conn, campaign_id):
    play(client, campaign_id)                                   # the seed stands on the Town Square
    assert autos(conn, campaign_id) == 0


def test_autosave_at_camp(client, conn, campaign_id):
    at(conn, campaign_id, "CAR-047")
    play(client, campaign_id)
    assert autos(conn, campaign_id) == 1


def test_manual_save_refused_mid_town_and_mid_story(client, conn, campaign_id):
    url = f"/api/campaigns/{campaign_id}/saves"
    r = client.post(url, json={"label": "here"})
    assert r.status_code == 409 and CAMP_ONLY in r.text
    at(conn, campaign_id, "CAR-047")
    from tests.carousel.test_story_ops import begin, seen
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    assert client.post(url, json={"label": "mid story"}).status_code == 409
    with transaction(conn):
        conn.execute("UPDATE stories SET status = 'concluded' WHERE campaign_id = ?", (campaign_id,))
    assert client.post(url, json={"label": "at camp"}).status_code == 200


def test_save_game_tool_refused_away_from_camp(conn, campaign_id):
    from app.gm.tools import ToolRunner
    r = ToolRunner(conn, None, campaign_id, 999)
    out = r.run("save_game", {"label": "please"})
    assert not out["ok"] and CAMP_ONLY in out["result"]["error"] and r.effects.save_label is None
    at(conn, campaign_id, "CAR-047")
    r = ToolRunner(conn, None, campaign_id, 1000)
    assert r.run("save_game", {"label": "please"})["ok"] and r.effects.save_label == "please"


def test_restore_works_from_anywhere(client, conn, campaign_id):
    at(conn, campaign_id, "CAR-047")
    sp = client.post(f"/api/campaigns/{campaign_id}/saves", json={"label": "camp"}).json()["save_point_id"]
    at(conn, campaign_id, "CAR-002")
    assert client.post(f"/api/campaigns/{campaign_id}/saves/{sp}/restore").status_code == 200
    assert conn.execute("SELECT node_id FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()[0] == "CAR-047"
