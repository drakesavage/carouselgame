"""Rules center (M7): lookups name the right source and precedence and mark missing code; only approved rulings are precedent."""
from pathlib import Path

import pytest

from app.db import now_iso, rows, transaction
from app.memory.context import build_context
from app.reference import rulebooks, rulecenter

from test_campaign_regression import denton  # noqa: F401  (the real-ledger campaign fixture)

REAL_INDEX = Path(__file__).resolve().parents[2] / "data" / "rulebooks.db"


@pytest.fixture(scope="module")
def ix():
    if not REAL_INDEX.exists():
        pytest.skip("rulebook index not present")
    rc = rulebooks.open_db(REAL_INDEX)
    yield rulecenter.Index(rc)
    rc.close()


@pytest.mark.parametrize("query,page", [("Stealth", 201), ("Catcher", 189), ("Toss", 183), ("Light on Your Feet", 197), ("Drowning", 78)])
def test_common_and_obscure_checks_find_their_rulebook_entry(ix, conn, demo_id, query, page):
    r = rulecenter.lookup(ix, conn, demo_id, query)
    assert r["book"] and r["book"][0]["ref"] == f"Dungeon Crawler Carl RPG — Core Rulebook, p. {page}"
    assert r["book"][0]["name"].lower() == query.lower() and len(r["book"][0]["excerpt"]) > 80
    assert r["precedence"][-1]["layer"] in ("book", "note")


def test_missing_code_is_marked_and_citations_resolve(ix, conn, demo_id):
    drowning = rulecenter.lookup(ix, conn, demo_id, "Drowning")["implementation"]
    assert drowning["status"] == "no_code" and "No dedicated code" in drowning["gap"]
    assert rulecenter.cite(ix, "Dungeon Crawler Carl RPG — Core Rulebook, p. 201") == {"document_id": rulecenter.cite(ix, "Dungeon Crawler Carl RPG — Core Rulebook, p. 1")["document_id"],
                                                                                    "page": 201, "document": "Dungeon Crawler Carl RPG — Core Rulebook"}
    assert rulecenter.cite(ix, "Not A Book, p. 3") is None
    names = [s["label"] for s in rulecenter.suggest(ix, conn, demo_id, "cat")]
    assert "Catcher" in names and not any(n.lower().startswith("plication") for n in names)


def test_house_rules_override_the_book_and_partial_code_names_its_gap(ix, conn, denton):
    r = rulecenter.lookup(ix, conn, denton, "AI Favor reroll")
    assert r["house_rules"] and r["implementation"]["status"] == "partial" and "reroll" in r["implementation"]["gap"]
    assert [l["layer"] for l in r["precedence"]][:2] == ["house_rule", "book"]


def test_rules_endpoints_with_an_index(client, settings, demo_id):
    if not REAL_INDEX.exists():
        pytest.skip("rulebook index not present")
    import shutil
    shutil.copy(REAL_INDEX, settings.rulebook_db)  # a copy in the test's data folder; the real index is only read
    r = client.get("/api/rules/lookup", params={"q": "Catcher", "cid": demo_id}).json()
    assert r["book"][0]["ref"] == "Dungeon Crawler Carl RPG — Core Rulebook, p. 189"
    assert any(s["label"] == "Catcher" for s in client.get("/api/rules/suggest", params={"q": "catc", "cid": demo_id}).json())
    books = client.get("/api/rules/browse").json()["books"]
    core = next(b for b in books if b["title"].endswith("Core Rulebook"))
    assert client.get("/api/rules/browse", params={"book_id": core["id"]}).json()["chapters"]
    assert client.get("/api/rules/cite", params={"ref": "Dungeon Crawler Carl RPG — Core Rulebook, p. 201"}).json()["page"] == 201
    assert client.get("/api/rules/cite", params={"ref": "Nope, p. 2"}).status_code == 404


def _ruling(conn, cid, topic, label, certainty, turn_id=None):
    with transaction(conn):
        return conn.execute("INSERT INTO rulings (campaign_id, turn_id, topic, ruling, certainty, label, sources_json, created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (cid, turn_id, topic, f"{topic}: ruled this way.", certainty, label, "[]", now_iso())).lastrowid


def test_only_approved_rulings_become_precedent(client, conn, demo_id):
    old = _ruling(conn, demo_id, "Kite tether strength", "ONE-SCENE", "provisional")
    owner = _ruling(conn, demo_id, "Barrel rolling momentum", "CAMPAIGN", "canonical")
    ctx = lambda: build_context(conn, demo_id, "I fly the kite on its tether and roll a barrel")["text"]
    text = ctx()
    assert "Barrel rolling momentum" in text and "Kite tether strength" not in text  # an old one-scene ruling is not precedent
    listed = {r["id"]: r["status"] for r in client.get(f"/api/campaigns/{demo_id}/rulings").json()}
    assert listed[old] == "provisional" and listed[owner] == "precedent"
    r = client.post(f"/api/campaigns/{demo_id}/rulings/{old}/review", json={"decision": "approve", "note": "keep it"})
    assert r.status_code == 200 and r.json()["status"] == "precedent"
    assert "Kite tether strength" in ctx()
    assert client.post(f"/api/campaigns/{demo_id}/rulings/{owner}/review", json={"decision": "reject"}).json()["status"] == "rejected"
    assert "Barrel rolling momentum" not in ctx()
    assert client.post(f"/api/campaigns/{demo_id}/rulings/{owner}/review", json={"decision": "maybe"}).status_code == 422
    assert client.post(f"/api/campaigns/{demo_id}/rulings/999999/review", json={"decision": "approve"}).status_code == 404


def test_recent_provisional_rulings_stay_visible_in_their_scene(conn, demo_id):
    with transaction(conn):
        tid = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                           (demo_id, "k-prov-1", "completed", "x", now_iso())).lastrowid
    _ruling(conn, demo_id, "Mirror camera angle", "INTERPRETATION", "provisional", tid)
    text = build_context(conn, demo_id, "I angle the mirror camera")["text"]
    assert "Mirror camera angle" in text and "NOT precedent" in text
    assert all(r["status"] for r in rulecenter.rulings(conn, demo_id))
    assert rows(conn, "SELECT review FROM rulings WHERE campaign_id = ? AND topic = 'Mirror camera angle'", (demo_id,))[0]["review"] is None
