from app.carousel import opening
from app.gm.orchestrator import needs_mechanics


def new_campaign(client, premise="A road trip to a haunted fair"):
    r = client.post("/api/campaigns", json={"name": "Opening Night", "character": "Mara", "premise": premise})
    assert r.status_code == 200, r.text
    return r.json()["campaign_id"]


def start(client, cid, key="opening-key-1"):
    return client.post(f"/api/campaigns/{cid}/opening/start", json={"idempotency_key": key})


# ---------------------------------------------------------------- Task 5: the opening turn

def test_opening_start_is_idempotent(client, conn):
    cid = new_campaign(client)
    a, b = start(client, cid), start(client, cid)
    assert a.status_code == b.status_code == 200, a.text
    assert a.json()["turn"]["turn_id"] == b.json()["turn"]["turn_id"]
    turns = conn.execute("SELECT kind, player_input FROM turns WHERE campaign_id = ?", (cid,)).fetchall()
    assert len(turns) == 1 and turns[0]["kind"] == "opening"
    assert turns[0]["player_input"] == "(The game begins in the car.) Player's wishes: A road trip to a haunted fair"


def test_opening_starts_once(client, conn):
    cid = new_campaign(client)
    assert start(client, cid).status_code == 200
    assert start(client, cid, key="opening-key-2").status_code == 409


def test_opening_start_needs_an_opening(client, campaign_id):
    assert start(client, campaign_id).status_code == 409


def test_silas_turns_go_to_the_mechanics_gm(conn):
    from app.campaigns import create
    cid = create(conn, name="Routing", character="Mara")
    assert not needs_mechanics(conn, cid)
    conn.execute("UPDATE campaigns SET opening_stage = 'silas' WHERE id = ?", (cid,))
    assert needs_mechanics(conn, cid)
    conn.execute("UPDATE campaigns SET opening_stage = 'opening_omen' WHERE id = ?", (cid,))
    assert not needs_mechanics(conn, cid)
    assert opening.stage(conn, cid) == "opening_omen"
