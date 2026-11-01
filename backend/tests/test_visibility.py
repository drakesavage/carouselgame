"""Plant unique GM-only strings everywhere GM knowledge can live, then crawl every player endpoint."""
import json
import uuid
from pathlib import Path

from app.db import now_iso, transaction

SECRETS = ["GMSECRET-ALPHA-1", "GMSECRET-BRAVO-2", "GMSECRET-CHARLIE-3", "GMSECRET-DELTA-4", "GMSECRET-ECHO-5", "GMSECRET-FOXTROT-6",
           "DEMO SECRET", "MINI-SEALED-TRUTH", "MINI-NPC-SECRET",   # Omen sealed truth and NPC gm_only (mini reference.db, conftest)
           "HIDDENTROPE-GOLF-7", "Doors open for it alone.", "trope:1",   # Adeline's hidden trope: name, text, ref
           "UNKNOWNPLACE-HOTEL-8",   # a spoiler place the group doesn't know (mini CAR-099)
           "MINI-OBLIGATION", "MINI-COUNTERPLAY", "MINI-CLUE",   # the active story's screenplay and sealed house story
           "REVISION-INDIA-9", "ENDING-JULIET-10",   # a revision's private operations/reason and a revised ending predicate
           "LURE-TRUTH-KILO-11", "MINI-NPC-SECRET-2",   # a new game's lure truth and its introduced resident's gm_only (mini CAR-NPC-003)
           "MINI-HIDDEN-ROOM", "OM-005", "open:cellar_door"]   # an unreached farm room, and the Omen placed in a room not yet entered


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
        conn.execute("INSERT INTO memories (campaign_id, layer, subject, content, visibility, created_at) VALUES (?,?,?,?,?,?)",
                     (cid, "npc", "adeline", SECRETS[5], "gm", now_iso()))
    from tests.conftest import apply
    apply(conn, cid, {"op": "use_trope", "character": "Adeline Winter", "ticket": "trope:1", "effect": "she knew where they hid"})
    _plant_story(conn, cid)


def _plant_story(conn, cid):
    """An active story whose packet, revision and endings hold planted strings (the sealed truth is MINI-SEALED-TRUTH)."""
    from tests.carousel.test_revisions import COMMANDO
    from tests.carousel.test_story_ops import begin, seen
    from tests.conftest import apply
    seen(conn, cid, "OM-001")
    begin(conn, cid)
    apply(conn, cid, {"op": "grant_ticket", "character": "Jonah", "ticket": COMMANDO, "source": "test"})
    apply(conn, cid, {"op": "equip_ticket", "character": "Jonah", "ticket": COMMANDO})
    apply(conn, cid, {"op": "revise_screenplay", "character": "Jonah", "ticket": COMMANDO, "base_version": 0, "scope": "ending_conditions",
                      "operations": [{"family": "rewrite_conditional_objective", "detail": "REVISION-INDIA-9"}],
                      "costs": ["REVISION-INDIA-9 cost"], "new_dangers": ["REVISION-INDIA-9 danger"], "preserved_facts": [],
                      "new_endings": {"full_success": {"predicate": "ENDING-JULIET-10"}},
                      "public_summary": "Jonah has a plan for the barn.", "reason": "REVISION-INDIA-9 reason"})


def _plant_opening(conn):
    """A second campaign in its opening: a person lure whose truth is planted, and that resident introduced."""
    from app.campaigns import create
    from tests.conftest import apply
    cid = create(conn, name="Opening crawl", character="Mara")
    apply(conn, cid, {"op": "set_lure", "kind": "person", "resident": "CAR-NPC-003", "belief": "Valerie wrote to say she'd found work here",
                      "truth": "LURE-TRUTH-KILO-11"})
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    return cid


def _player_urls(client, cid):
    urls = ["/api/health", "/api/settings", "/api/campaigns", f"/api/campaigns/{cid}", f"/api/campaigns/{cid}/characters",
            f"/api/campaigns/{cid}/inventory", f"/api/campaigns/{cid}/quests", f"/api/campaigns/{cid}/npcs", f"/api/campaigns/{cid}/timeline",
            f"/api/campaigns/{cid}/messages", f"/api/campaigns/{cid}/house-rules", f"/api/campaigns/{cid}/rulings",
            f"/api/campaigns/{cid}/lore", f"/api/campaigns/{cid}/records", f"/api/campaigns/{cid}/saves",
            f"/api/campaigns/{cid}/records?q=secret debt syndicate hidden plans", f"/api/rules/search?q=secret&cid={cid}",
            f"/api/campaigns/{cid}/journal", f"/api/campaigns/{cid}/lore-notebook", f"/api/campaigns/{cid}/chronology",
            f"/api/campaigns/{cid}/search?q=gmsecret alpha bravo charlie delta echo foxtrot secret plot",
            f"/api/campaigns/{cid}/inventory", f"/api/campaigns/{cid}/map", "/api/reference/status",
            f"/api/campaigns/{cid}/story", f"/api/campaigns/{cid}/stories", f"/api/campaigns/{cid}/venue"]
    for ch in client.get(f"/api/campaigns/{cid}/characters").json():
        urls.append(f"/api/campaigns/{cid}/characters/{ch['id']}")
        urls.append(f"/api/campaigns/{cid}/characters/{ch['id']}/dossier")
    for rec in client.get(f"/api/campaigns/{cid}/records").json():
        urls.append(f"/api/campaigns/{cid}/records/{rec['id']}")
    return urls


def test_no_gm_material_in_any_player_response(client, conn, campaign_id):
    cid = campaign_id
    _plant(conn, cid)
    # a turn so that the GM context (which DOES contain secrets) is exercised
    client.post(f"/api/campaigns/{cid}/turns", json={"text": "I look around carefully", "idempotency_key": uuid.uuid4().hex})
    # and a walk next door to the spoiler place, with its Omen sighting and arrival turn
    client.post(f"/api/campaigns/{cid}/travel", json={"to": "CAR-086", "idempotency_key": uuid.uuid4().hex})
    # then into the farm's barn: the cellar door is now one link away, the room past it is not
    r = client.post(f"/api/campaigns/{cid}/venue/move", json={"to": "SM-T01:n02_barn", "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/campaigns/{cid}/story").json()["active"]["revisions"] == ["Jonah has a plan for the barn."]
    other = _plant_opening(conn)
    for url in [*_player_urls(client, cid), *_player_urls(client, other)]:
        r = client.get(url)
        assert r.status_code == 200, url
        text = r.text
        for s in SECRETS:
            assert s not in text, f"{s} leaked via {url}"


def test_gm_endpoints_require_token(client, settings, campaign_id):
    _plant_conn = None
    assert client.get(f"/api/gm/campaigns/{campaign_id}/secrets").status_code == 403
    assert client.get(f"/api/gm/campaigns/{campaign_id}/secrets", headers={"X-GM-Token": "wrong"}).status_code == 403
    ok = client.get(f"/api/gm/campaigns/{campaign_id}/secrets", headers={"X-GM-Token": settings.gm_token()})
    assert ok.status_code == 200 and "DEMO SECRET" in ok.text


def test_frontend_bundle_contains_no_secrets():
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not dist.exists():
        return
    blob = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in dist.rglob("*") if p.is_file() and p.suffix in (".js", ".html", ".css"))
    for s in SECRETS:
        assert s not in blob
    assert "companion_sheet" not in blob
