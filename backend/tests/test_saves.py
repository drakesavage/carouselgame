import uuid

from app.db import connect, one


def test_save_change_restore_and_undo_restore(client, settings, campaign_id):
    sp = client.post(f"/api/campaigns/{campaign_id}/saves", json={"label": "before nap"}).json()["save_point_id"]
    t0 = client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"]
    client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I rest", "idempotency_key": uuid.uuid4().hex})
    client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I loot the rock", "idempotency_key": uuid.uuid4().hex})
    assert client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"] == t0 + 60
    inv = client.get(f"/api/campaigns/{campaign_id}/inventory").json()
    assert any(i["name"] == "Suspicious Gray Rock" for i in inv["by_character"])

    out = client.post(f"/api/campaigns/{campaign_id}/saves/{sp}/restore").json()
    assert client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"] == t0
    inv = client.get(f"/api/campaigns/{campaign_id}/inventory").json()
    assert not any(i["name"] == "Suspicious Gray Rock" for i in inv["by_character"])
    # transcript and timeline hide rolled-back rows, but the audit rows still exist
    assert not [m for m in client.get(f"/api/campaigns/{campaign_id}/messages").json() if m["role"] == "player"]
    assert not client.get(f"/api/campaigns/{campaign_id}/timeline").json()
    c = connect(settings.campaign_db)
    assert one(c, "SELECT COUNT(*) AS n FROM events WHERE campaign_id = ? AND rolled_back = 1", (campaign_id,))["n"] >= 2
    assert one(c, "SELECT COUNT(*) AS n FROM turns WHERE campaign_id = ? AND rolled_back = 1", (campaign_id,))["n"] == 2

    # the automatic pre-restore save lets the restore itself be undone
    client.post(f"/api/campaigns/{campaign_id}/saves/{out['backup_save_point_id']}/restore")
    assert client.get(f"/api/campaigns/{campaign_id}").json()["game_time_minutes"] == t0 + 60
    inv = client.get(f"/api/campaigns/{campaign_id}/inventory").json()
    assert any(i["name"] == "Suspicious Gray Rock" for i in inv["by_character"])


def test_autosaves_are_pruned(client, settings, campaign_id):
    settings.autosave_keep = 2
    for _ in range(4):
        client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I wait", "idempotency_key": uuid.uuid4().hex})
    saves = client.get(f"/api/campaigns/{campaign_id}/saves").json()
    assert sum(1 for s in saves if s["kind"] == "auto") == 2
    assert any(s["kind"] == "manual" for s in saves)  # manual saves are never pruned
