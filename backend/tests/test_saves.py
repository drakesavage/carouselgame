import uuid

from app.db import connect, one, transaction


def at_camp(conn, cid):
    """Save points are made only at camp (carousel/fate.py at_camp); the seed stands on the Town Square."""
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id = 'CAR-047' WHERE id = ?", (cid,))

def carried(settings, cid) -> dict[str, int]:
    """Item name -> quantity for every character-held item (the player inventory route is gone)."""
    c = connect(settings.campaign_db)
    try:
        return {r["name"]: r["quantity"] for r in c.execute(
            "SELECT name, quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NOT NULL", (cid,))}
    finally:
        c.close()


def test_save_change_restore_and_undo_restore(client, settings, conn, campaign_id):
    at_camp(conn, campaign_id)
    sp = client.post(f"/api/campaigns/{campaign_id}/saves", json={"label": "before nap"}).json()["save_point_id"]
    t0 = client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"]
    client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I rest", "idempotency_key": uuid.uuid4().hex})
    client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I loot the rock", "idempotency_key": uuid.uuid4().hex})
    assert client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"] == t0 + 60
    assert "Suspicious Gray Rock" in carried(settings, campaign_id)

    out = client.post(f"/api/campaigns/{campaign_id}/saves/{sp}/restore").json()
    assert client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"] == t0
    assert "Suspicious Gray Rock" not in carried(settings, campaign_id)
    # transcript and timeline hide rolled-back rows, but the audit rows still exist
    assert not [m for m in client.get(f"/api/campaigns/{campaign_id}/messages").json() if m["role"] == "player"]
    assert not client.get(f"/api/campaigns/{campaign_id}/timeline").json()
    c = connect(settings.campaign_db)
    assert one(c, "SELECT COUNT(*) AS n FROM events WHERE campaign_id = ? AND rolled_back = 1", (campaign_id,))["n"] >= 2
    assert one(c, "SELECT COUNT(*) AS n FROM turns WHERE campaign_id = ? AND rolled_back = 1", (campaign_id,))["n"] == 2

    # the automatic pre-restore save lets the restore itself be undone
    client.post(f"/api/campaigns/{campaign_id}/saves/{out['backup_save_point_id']}/restore")
    assert client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"] == t0 + 60
    assert "Suspicious Gray Rock" in carried(settings, campaign_id)


def test_autosaves_are_pruned(client, settings, conn, campaign_id):
    at_camp(conn, campaign_id)
    settings.autosave_keep = 2
    for _ in range(4):
        client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I wait", "idempotency_key": uuid.uuid4().hex})
    saves = client.get(f"/api/campaigns/{campaign_id}/saves").json()
    assert sum(1 for s in saves if s["kind"] == "auto") == 2
    assert any(s["kind"] == "manual" for s in saves)  # manual saves are never pruned
