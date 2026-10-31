"""M9: backups from the app, save-point restore preview, redacted problem reports, and what a signed-in phone can't reach."""
import uuid

from fastapi.testclient import TestClient

from app import support
from app.db import now_iso, one, transaction
from app.main import create_app

PHONE = {"X-Forwarded-For": "192.168.1.50", "X-Forwarded-Prefix": "/dcc", "X-Forwarded-Proto": "http"}


def test_a_signed_in_phone_cannot_reach_backups_passcode_or_gm_material(settings, conn, campaign_id):
    app = create_app(settings, bootstrap=False, local_hosts={"testclient"})
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("crawler_console_access", app.state.gate.cookie_value())
    assert c.get("/api/campaigns", headers=PHONE).status_code == 200            # signed in: the game works
    for method, url in (("get", "/api/backups"), ("post", "/api/backups"), ("post", "/api/backups/x/verify"), ("get", "/api/access"),
                        ("post", "/api/access/regenerate")):
        assert getattr(c, method)(url, headers=PHONE, **({"json": {}} if method == "post" else {})).status_code == 403, url
    for url in (f"/api/gm/campaigns/{campaign_id}/secrets", f"/api/gm/campaigns/{campaign_id}/diagnostics", "/api/gm/runtime"):
        assert c.get(url, headers=PHONE).status_code == 403, url                  # no GM token
        assert c.get(url, headers={**PHONE, "X-GM-Token": "guess"}).status_code == 403, url


def test_backups_are_listed_made_verified_and_automatic_ones_are_database_only(client, settings):
    r = client.post("/api/backups", json={"label": "before test", "media": False})
    assert r.status_code == 200 and r.json()["verify"]["ok"]
    listed = client.get("/api/backups").json()
    assert listed["backups"][0]["name"] == r.json()["name"] and listed["newest_age_s"] < 120
    assert client.post(f"/api/backups/{r.json()['name']}/verify").json()["ok"]
    assert client.post("/api/backups/..%2F..%2Fx/verify").status_code in (404, 405)  # traversal never reaches a folder
    assert client.post("/api/backups/no-such-backup/verify").status_code == 404
    assert support.auto_backup(settings) is None                                 # a fresh backup exists: nothing to do
    import os, time
    for d in (settings.data_dir / "backups").iterdir():                          # age every backup past a day
        old = time.time() - 2 * 86400
        os.utime(d, (old, old))
        d.rename(d.with_name("20000101-000000-" + d.name.split("-", 2)[-1]))
    made = support.auto_backup(settings)
    assert made is not None and made.name.endswith("auto-daily") and not (made / "art").exists() and (made / "campaign.db").is_file()


def test_restore_preview_says_what_would_change_and_changes_nothing(client, conn, campaign_id):
    save = client.post(f"/api/campaigns/{campaign_id}/saves", json={"label": "before the haul"})
    assert save.status_code == 200
    sid = save.json()["save_point_id"]
    with transaction(conn):
        conn.execute("UPDATE campaigns SET game_time_minutes = game_time_minutes + 90, current_location = 'Somewhere New' WHERE id = ?", (campaign_id,))
        conn.execute("INSERT INTO items (campaign_id, name, quantity) VALUES (?,?,?)", (campaign_id, "Shiny Trinket", 2))
    before = one(conn, "SELECT game_time_minutes, current_location FROM campaigns WHERE id = ?", (campaign_id,))
    p = client.get(f"/api/campaigns/{campaign_id}/saves/{sid}/preview").json()
    what = {c["what"]: c for c in p["changes"]}
    assert what["Location"]["now"] == "Somewhere New" and what["Location"]["after_restore"] != "Somewhere New"
    assert "Game time" in what and any(i.startswith("Shiny Trinket (shared): 2 → none") for i in p["items"])
    assert one(conn, "SELECT game_time_minutes, current_location FROM campaigns WHERE id = ?", (campaign_id,)) == before
    assert client.get(f"/api/campaigns/{campaign_id}/saves/999999/preview").status_code == 404


def test_problem_reports_carry_the_turn_but_never_gm_material(client, conn, campaign_id, settings):
    with transaction(conn):
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, created_at) VALUES (?,?,?,?,?)",
                     (campaign_id, "plot", "plot", "GMSECRET-REPORT-8812 the vault is a mimic", now_iso()))
    r = client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I look around carefully", "idempotency_key": uuid.uuid4().hex})
    tid = r.json()["turn_id"]
    rep = client.post(f"/api/campaigns/{campaign_id}/reports", json={"turn_id": tid, "note": "The GM forgot my lantern."}).json()
    md = rep["markdown"]
    assert f"## Turn {tid}" in md and "I look around carefully" in md and "The GM forgot my lantern." in md and "## State now" in md
    assert "GMSECRET-REPORT-8812" not in md and settings.gm_token() not in md
    assert "Diagnostics" in md and "context_text" not in md
    assert open(rep["path"], encoding="utf-8").read() == md
    # a GM-only line quoted into player text would be redacted
    assert support.redact("He said: GMSECRET-REPORT-8812 the vault is a mimic!", ["GMSECRET-REPORT-8812 the vault is a mimic"]) == "He said: [redacted]!"
