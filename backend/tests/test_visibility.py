"""Plant unique GM-only strings everywhere GM knowledge can live, then crawl every player endpoint."""
import json
import uuid
from pathlib import Path

from app.db import now_iso, transaction
from app.importer.ledger import import_ledger
from app.rules.dice import DiceRoller, record_roll
from conftest import FIXTURES

SECRETS = ["GMSECRET-ALPHA-1", "GMSECRET-BRAVO-2", "GMSECRET-CHARLIE-3", "GMSECRET-DELTA-4", "GMSECRET-ECHO-5", "GMSECRET-FOXTROT-6",
           "FIXTURE-GM-SECRET-7731", "FIXTURE-GM-README-SECRET-4410", "DEMO SECRET"]


def _plant(conn, cid):
    with transaction(conn):
        conn.execute("INSERT INTO gm_secrets (campaign_id, subject, kind, content, created_at) VALUES (?,?,?,?,?)",
                     (cid, "plot", "plot", SECRETS[0], now_iso()))
        conn.execute("INSERT INTO lore_facts (campaign_id, subject, fact, certainty, visibility, created_at) VALUES (?,?,?,?,?,?)",
                     (cid, SECRETS[1], SECRETS[1], "canonical", "gm", now_iso()))
        conn.execute("INSERT INTO events (campaign_id, type, summary, visibility, created_at) VALUES (?,?,?,?,?)",
                     (cid, "fact_recorded", SECRETS[2], "gm", now_iso()))
        conn.execute("INSERT INTO messages (campaign_id, role, content, visibility, created_at) VALUES (?,?,?,?,?)",
                     (cid, "gm", SECRETS[3], "gm", now_iso()))
        conn.execute("INSERT INTO clocks (campaign_id, key, label, kind, visibility) VALUES (?,?,?,?,?)", (cid, "secret", SECRETS[4], "faction", "gm"))
        r = DiceRoller().roll("1d20", reason=SECRETS[5])
        record_roll(conn, cid, r, turn_id=None, hidden=True)


def _player_urls(client, cid):
    urls = ["/api/health", "/api/settings", "/api/campaigns", f"/api/campaigns/{cid}", f"/api/campaigns/{cid}/characters",
            f"/api/campaigns/{cid}/inventory", f"/api/campaigns/{cid}/quests", f"/api/campaigns/{cid}/npcs", f"/api/campaigns/{cid}/timeline",
            f"/api/campaigns/{cid}/messages", f"/api/campaigns/{cid}/rolls", f"/api/campaigns/{cid}/house-rules", f"/api/campaigns/{cid}/rulings",
            f"/api/campaigns/{cid}/lore", f"/api/campaigns/{cid}/records", f"/api/campaigns/{cid}/saves", "/api/import/reports",
            f"/api/campaigns/{cid}/records?q=secret debt syndicate hidden plans", f"/api/rules/search?q=secret&cid={cid}",
            f"/api/campaigns/{cid}/journal", f"/api/campaigns/{cid}/lore-notebook", f"/api/campaigns/{cid}/chronology",
            f"/api/campaigns/{cid}/search?q=gmsecret alpha bravo charlie delta echo foxtrot secret plot",
            f"/api/campaigns/{cid}/inventory", f"/api/campaigns/{cid}/places"]
    for ch in client.get(f"/api/campaigns/{cid}/characters").json():
        urls.append(f"/api/campaigns/{cid}/characters/{ch['id']}")
        urls.append(f"/api/campaigns/{cid}/characters/{ch['id']}/dossier")
    for rec in client.get(f"/api/campaigns/{cid}/records").json():
        urls.append(f"/api/campaigns/{cid}/records/{rec['id']}")
    return urls


def test_no_gm_material_in_any_player_response(client, conn, demo_id):
    cid_import, _ = import_ledger(conn, FIXTURES / "mini_ledger")
    for cid in (demo_id, cid_import):
        _plant(conn, cid)
        # a turn so that the GM context (which DOES contain secrets) is exercised
        client.post(f"/api/campaigns/{cid}/turns", json={"text": "I look around carefully", "idempotency_key": uuid.uuid4().hex})
        for url in _player_urls(client, cid):
            r = client.get(url)
            assert r.status_code == 200, url
            text = r.text
            for s in SECRETS:
                assert s not in text, f"{s} leaked via {url}"


def test_gm_endpoints_require_token(client, settings, demo_id):
    _plant_conn = None
    assert client.get(f"/api/gm/campaigns/{demo_id}/secrets").status_code == 403
    assert client.get(f"/api/gm/campaigns/{demo_id}/secrets", headers={"X-GM-Token": "wrong"}).status_code == 403
    ok = client.get(f"/api/gm/campaigns/{demo_id}/secrets", headers={"X-GM-Token": settings.gm_token()})
    assert ok.status_code == 200 and "DEMO SECRET" in ok.text


def test_frontend_bundle_contains_no_secrets():
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not dist.exists():
        return
    blob = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in dist.rglob("*") if p.is_file() and p.suffix in (".js", ".html", ".css"))
    for s in SECRETS:
        assert s not in blob
    assert "companion_sheet" not in blob
