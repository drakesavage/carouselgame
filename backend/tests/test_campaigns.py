"""Campaign management: start a new campaign, rename, archive, and delete (backed up first, game PC only, exact name)."""
from fastapi.testclient import TestClient

from app import backup
from app.db import one, rows
from app.main import create_app

PHONE = {"X-Forwarded-For": "192.168.1.50", "X-Forwarded-Prefix": "/dcc", "X-Forwarded-Proto": "http"}


def new(client, **kw):
    body = {"name": "Second Crawl", "character": "Donut Jr", "location": "the abandoned lot", "premise": "A new group drives into town.", **kw}
    r = client.post("/api/campaigns", json=body)
    assert r.status_code == 200, r.text
    return r.json()["campaign_id"]


def test_a_new_campaign_has_its_character_and_a_start_save_and_nothing_invented(client, conn):
    cid = new(client)
    camp = client.get(f"/api/campaigns/{cid}").json()
    assert camp["name"] == "Second Crawl" and "floor_clock" not in camp
    pc = one(conn, "SELECT * FROM characters WHERE id = ?", (camp["active_character_id"],))
    assert pc["name"] == "Donut Jr" and pc["kind"] == "pc" and pc["in_party"] == 1
    assert pc["mettle"] is None and pc["role_id"] is None      # unknown stays unknown: Silas assigns the role in play
    assert one(conn, "SELECT COUNT(*) n FROM items WHERE campaign_id = ?", (cid,))["n"] == 0
    assert [s["label"] for s in client.get(f"/api/campaigns/{cid}/saves").json()] == ["Campaign started"]
    assert client.post("/api/campaigns", json={"name": "Second Crawl", "character": "X"}).status_code == 422   # names are unique
    assert client.post("/api/campaigns", json={"name": " ", "character": "X"}).status_code == 422


def test_rename_and_archive(client):
    cid = new(client)
    assert client.patch(f"/api/campaigns/{cid}", json={"name": "Renamed Crawl", "archived": True}).status_code == 200
    row = next(c for c in client.get("/api/campaigns").json() if c["id"] == cid)
    assert row["name"] == "Renamed Crawl" and row["archived"] == 1
    managed = next(c for c in client.get("/api/campaigns-manage").json() if c["id"] == cid)
    assert managed["character"] == "Donut Jr" and managed["turns"] == 0
    assert client.patch(f"/api/campaigns/{cid}", json={"archived": False}).status_code == 200
    assert client.patch("/api/campaigns/9999", json={"name": "x"}).status_code == 404


def test_delete_needs_the_exact_name_backs_up_first_and_removes_only_that_campaign(client, conn, settings, campaign_id):
    cid = new(client)
    client.post(f"/api/campaigns/{cid}/turns", json={"text": "I look around", "idempotency_key": "delete-test-0001"})
    art = settings.data_dir / "art" / f"c{cid}"
    art.mkdir(parents=True)
    (art / "item-1.png").write_bytes(b"png")
    (settings.portraits_dir / f"c{cid}-donut-jr-abc123.png").write_bytes(b"png")
    before_demo = {t: one(conn, f"SELECT COUNT(*) n FROM {t} WHERE campaign_id = ?", (campaign_id,))["n"] for t in ("characters", "items", "messages")}

    assert client.post(f"/api/campaigns/{cid}/delete", json={"confirm_name": "second crawl"}).status_code == 422
    assert one(conn, "SELECT id FROM campaigns WHERE id = ?", (cid,))
    r = client.post(f"/api/campaigns/{cid}/delete", json={"confirm_name": "Second Crawl"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["pictures_moved"] == 2 and out["rows"]["characters"] == 3   # the player character and the two mini residents

    assert not one(conn, "SELECT id FROM campaigns WHERE id = ?", (cid,))
    for t in [x["name"] for x in rows(conn, "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE 'CREATE VIRTUAL%'")]:
        if any(c["name"] == "campaign_id" for c in rows(conn, f"PRAGMA table_info({t})")):
            assert one(conn, f"SELECT COUNT(*) n FROM {t} WHERE campaign_id = ?", (cid,))["n"] == 0, t
    for t in ("character_tickets", "stat_modifiers", "status_effects"):
        assert one(conn, f"SELECT COUNT(*) n FROM {t} WHERE character_id NOT IN (SELECT id FROM characters)")["n"] == 0
    assert {t: one(conn, f"SELECT COUNT(*) n FROM {t} WHERE campaign_id = ?", (campaign_id,))["n"] for t in before_demo} == before_demo

    kept = settings.data_dir / "backups" / out["backup"]
    assert backup.verify(kept)["ok"] and (kept / "art" / f"c{cid}" / "item-1.png").exists() and not art.exists()
    assert (kept / "portraits" / f"c{cid}-donut-jr-abc123.png").exists()
    import sqlite3
    old = sqlite3.connect(kept / "campaign.db")
    assert old.execute("SELECT name FROM campaigns WHERE id = ?", (cid,)).fetchone()[0] == "Second Crawl"   # restorable
    assert client.post(f"/api/campaigns/{cid}/delete", json={"confirm_name": "Second Crawl"}).status_code == 404
    assert new(client) > cid                                                     # a deleted campaign's id is never reused


def test_delete_is_refused_from_a_phone_and_while_a_turn_runs(settings, conn, client):
    cid = new(client)
    app = create_app(settings, bootstrap=False, local_hosts={"testclient"})
    phone = TestClient(app, follow_redirects=False)
    phone.cookies.set("crawler_console_access", app.state.gate.cookie_value())
    assert phone.post(f"/api/campaigns/{cid}/delete", json={"confirm_name": "Second Crawl"}, headers=PHONE).status_code == 403
    conn.execute("INSERT INTO turns (campaign_id, idempotency_key, player_input, status, created_at) VALUES (?,?,?,?,datetime('now'))",
                 (cid, "running-turn-1", "x", "processing"))
    assert client.post(f"/api/campaigns/{cid}/delete", json={"confirm_name": "Second Crawl"}).status_code == 409
    assert one(conn, "SELECT id FROM campaigns WHERE id = ?", (cid,))


def test_npc_op_puts_a_met_person_on_the_people_page_and_updates_them(client, conn):
    from app.db import transaction
    from app.rules.engine import ChangeError, apply_change
    cid = new(client)
    with transaction(conn):
        apply_change(conn, cid, None, {"op": "npc", "name": "Marn", "role": "Game Guide", "category": "allied_npcs",
                                       "description": "A squat, gray-skinned woman in a mustard vest.", "location": "Tutorial Guild",
                                       "personality": "Dry, careful", "attitude": 1})
        apply_change(conn, cid, None, {"op": "npc", "name": "marn", "known_motivation": "Keep crawlers alive long enough to learn"})
    people = client.get(f"/api/campaigns/{cid}/npcs").json()
    marn = [p for p in people if p["name"] == "Marn"]
    assert len(marn) == 1 and marn[0]["known_motivation"].startswith("Keep") and marn[0]["personality"] == "Dry, careful"
    assert marn[0]["attitude"] == 1 and marn[0]["where"] == "Tutorial Guild"
    with transaction(conn):
        import pytest
        with pytest.raises(ChangeError):
            apply_change(conn, cid, None, {"op": "npc", "name": "Donut Jr"})       # the player character is not an NPC


def test_the_gm_input_names_no_particular_player():
    from app.gm.providers import render_steps
    assert "Cliff" not in render_steps([{"kind": "player", "text": "hello"}])


def test_an_npc_carries_gender_and_pronouns_and_the_gm_is_told_when_they_are_missing(client, conn):
    from app.db import transaction
    from app.memory.context import build_context
    from app.rules.engine import apply_change
    cid = new(client, location="Paintball course")
    with transaction(conn):
        apply_change(conn, cid, None, {"op": "npc", "name": "Jamie Cole", "location": "Paintball course", "description": "Pinned by the fence."})
    ctx = build_context(conn, cid, "I help Jamie up"); text = ctx if isinstance(ctx, str) else getattr(ctx, "text", str(ctx))
    assert "gender and pronouns NOT RECORDED" in text
    with transaction(conn):
        apply_change(conn, cid, None, {"op": "npc", "name": "Jamie Cole", "gender": "woman", "pronouns": "she/her", "apparent_age": "early 30s"})
    ctx = build_context(conn, cid, "I help Jamie up"); text = ctx if isinstance(ctx, str) else getattr(ctx, "text", str(ctx))
    assert "pronouns she/her" in text and "NOT RECORDED" not in text
    jamie = [p for p in client.get(f"/api/campaigns/{cid}/npcs").json() if p["name"] == "Jamie Cole"][0]
    assert jamie["identity"] == {"gender": "woman", "pronouns": "she/her", "apparent_age": "early 30s"}



def test_a_new_campaign_starts_on_the_map(client, conn):
    """A campaign with no opening (opening=False) starts at Dyer's Lodge, unless its location names a place."""
    from app.campaigns import create
    lodge = create(conn, name="Lodge Start", character="Donut Jr", opening=False)
    m = client.get(f"/api/campaigns/{lodge}/map").json()
    assert m["position"]["node_id"] == "CAR-047" and m["position"]["label"] == "Dyer's Lodge"
    assert {p["id"] for p in m["known"]} == {"CAR-047"}
    square = create(conn, name="Square Start", character="Donut Jr", location="Town Square", opening=False)
    m = client.get(f"/api/campaigns/{square}/map").json()
    assert m["position"]["node_id"] == "CAR-002" and {p["id"] for p in m["known"]} == {"CAR-002", "CAR-047"}
    vague = create(conn, name="Vague Start", character="Donut Jr", location="the abandoned lot", opening=False)
    m = client.get(f"/api/campaigns/{vague}/map").json()
    assert m["position"]["node_id"] == "CAR-047" and m["position"]["label"] == "the abandoned lot"
