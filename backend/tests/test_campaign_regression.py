"""Regression tests on the real Denton campaign, imported from a throwaway clone of the ledger into a temp database.
The real ledger clone and data/campaign.db are never touched. Skipped when the ledger clone is absent."""
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.db import one, rows, transaction
from app.gm.integrity import check
from app.gm.orchestrator import TurnService
from app.gm.providers import MockProvider, ProviderResponse, ToolCall
from app.importer.ledger import import_ledger
from app.memory.context import build_context
from app.rules.engine import ChangeError, apply_change

REAL = Path(__file__).resolve().parents[2] / "campaign_sources" / "dccrepository"
pytestmark = pytest.mark.skipif(not (REAL / ".git").exists(), reason="real campaign ledger clone not present")


@pytest.fixture
def denton(conn, tmp_path):
    root = tmp_path / "ledger"
    subprocess.run(["git", "-c", "core.longpaths=true", "clone", "-q", "--no-hardlinks", str(REAL), str(root)], check=True)
    cid, _ = import_ledger(conn, root)
    yield cid
    shutil.rmtree(root, ignore_errors=True)


def ids(conn, cid):
    return {r["slug"]: r for r in rows(conn, "SELECT * FROM characters WHERE campaign_id = ?", (cid,))}


def do(conn, cid, *changes):
    with transaction(conn):
        return [apply_change(conn, cid, None, c) for c in changes]


def state(conn, cid):
    c = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,))["game_time_minutes"]
    cyc = one(conn, "SELECT current FROM clocks WHERE campaign_id = ? AND key = 'advancement_cycles'", (cid,))["current"]
    return c, cyc


def test_floor3_party_assignments_are_seeded_from_the_ledger(conn, denton):
    g = {r["key"]: r for r in rows(conn, "SELECT * FROM party_groups WHERE campaign_id = ?", (denton,))}
    assert set(g) == {"prison-recon", "training-market", "saferoom"}
    ch = ids(conn, denton)
    assert ch["cliff"]["assignment"] == "training-market" and ch["scout"]["assignment"] == "prison-recon"
    assert ch["margit-winchgrinder"]["assignment"] == "saferoom"
    assert g["training-market"]["location"] == "Gilded Feathers, Heavensworth"
    assert g["prison-recon"]["location"] is None                    # not recorded: not invented
    assert "withdraw if detected" in g["prison-recon"]["activity"]
    scene = one(conn, "SELECT * FROM scenes WHERE campaign_id = ? AND status = 'active'", (denton,))
    assert scene["location"] == "Gilded Feathers, Heavensworth" and "Cliff Atkinson" in scene["participants"]


def test_recap_does_not_advance_time_or_cycles(client, conn, denton, settings):
    class Narrator(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            return ProviderResponse([], "Previously on Dungeon Crawler Carl… (recap)")
    client.app_state.turns = TurnService(settings, Narrator, run_async=False)
    before = state(conn, denton)
    r = client.post(f"/api/campaigns/{denton}/turns", json={"text": "Give me a 'Previously on' recap.", "idempotency_key": uuid.uuid4().hex}).json()
    assert r["status"] == "completed" and state(conn, denton) == before


def test_counting_a_cycle_on_a_recap_is_flagged(conn, denton):
    flags = check(conn, denton, "Give me a recap of where we are", "Here's where things stand.", ["Advancement cycles: 4 → 5/12 — recap"], [], [], [])
    assert any("recap" in f["message"] for f in flags)


def test_skill_training_follows_house_rules(conn, denton):
    do(conn, denton, {"op": "skill", "character": "vada", "name": "Persuasion", "delta": 1, "source": "training", "reason": "Deathstroke Hall session"})
    assert one(conn, "SELECT rank FROM skills s JOIN characters c ON c.id = s.character_id WHERE c.slug = 'vada' AND s.name = 'Persuasion'")["rank"] == 4
    with pytest.raises(ChangeError, match="Rank 15 cap"):
        do(conn, denton, {"op": "skill", "character": "cliff", "name": "Investigation", "rank": 16, "source": "training", "reason": "overtraining"})
    # Cliff's Class lets Engineering exceed the cap only when the benefit is cited
    do(conn, denton, {"op": "skill", "character": "cliff", "name": "Engineering", "rank": 16, "source": "class", "reason": "Class",
                      "cap_override": "Field-Expedient Disaster Manager: Engineering may advance to Rank 20"})


def test_group_departure_moves_only_that_group(conn, denton):
    before = {s: c["location"] for s, c in ids(conn, denton).items()}
    do(conn, denton, {"op": "move_group", "key": "prison-recon", "destination": "Fallen Watch perimeter", "reason": "final reconnaissance"})
    after = ids(conn, denton)
    for slug in ("maya", "erin", "scout", "eli-ramirez", "ghost", "terri-campion"):
        assert after[slug]["location"] == "Fallen Watch perimeter"
    for slug in ("cliff", "vada", "priya-nair", "jonai", "margit-winchgrinder"):
        assert after[slug]["location"] == before[slug]


def test_inventory_cannot_overspend_or_duplicate(conn, denton):
    with pytest.raises(ChangeError, match="only 4046"):
        do(conn, denton, {"op": "remove_item", "character": "shared", "name": "Gold", "quantity": 5000})
    n = one(conn, "SELECT COUNT(*) AS n FROM items WHERE campaign_id = ? AND name = 'Healing Potion'", (denton,))["n"]
    do(conn, denton, {"op": "add_item", "character": "shared", "name": "Healing Potion", "quantity": 2})
    assert one(conn, "SELECT COUNT(*) AS n FROM items WHERE campaign_id = ? AND name = 'Healing Potion'", (denton,))["n"] == n
    with pytest.raises(ChangeError, match="does not establish"):
        do(conn, denton, {"op": "remove_item", "character": "shared", "name": "Crossbow Bolt"})


def test_reserved_stabber_stays_a_reservation(conn, denton):
    res = one(conn, "SELECT * FROM quests WHERE campaign_id = ? AND kind = 'reservation'", (denton,))
    assert res["title"] == "Reserved: Personal Stabber" and "conflict" in res["details"]
    assert not one(conn, "SELECT id FROM items WHERE campaign_id = ? AND name LIKE '%Personal Stabber%'", (denton,))
    assert "Reservations are held, not owned" in build_context(conn, denton, "I pick up Scout's stabber")["text"]


def test_margit_siphon_project_keeps_its_design(conn, denton):
    q = one(conn, "SELECT * FROM quests WHERE campaign_id = ? AND title LIKE '%Pass-Pulse Gold Siphon%'", (denton,))
    d = q["details"]
    assert (d["status"], d["lead"], d["assistant"], d["engineering_difficulty"], d["build_time"], d["misc_junk_cost"]) == \
        ("designed-not-built", "Cliff", "Margit Winchgrinder", 16, "8h", 8)
    do(conn, denton, {"op": "quest", "title": q["title"], "progress": "Materials gathered", "details": {"contributors": ["Cliff", "Margit", "Nate"]}})
    d2 = one(conn, "SELECT details_json FROM quests WHERE id = ?", (q["id"],))["details"]
    assert d2["engineering_difficulty"] == 16 and d2["contributors"] == ["Cliff", "Margit", "Nate"]


def test_private_motivation_surfaces_only_when_the_character_is_relevant(conn, denton):
    sheet = one(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet' AND subject = 'scout'", (denton,))["content"]
    line = next(l.strip() for l in sheet.splitlines() if len(l.strip()) > 60 and ":" in l)
    snippet = line[-40:]
    assert snippet not in build_context(conn, denton, "I compare armor prices at the smithy")["text"]   # Scout is away on recon
    ctx = build_context(conn, denton, "I tell Scout over the network that I found his magic stabber")
    assert snippet in ctx["text"] and "Scout (id" in ctx["text"] and "NOT PRESENT" in ctx["text"]


def test_npc_relationship_evolves_without_losing_personality(conn, denton):
    m = ids(conn, denton)["margit-winchgrinder"]
    before = one(conn, "SELECT personality FROM npc_profiles WHERE character_id = ?", (m["id"],))["personality"]
    do(conn, denton, {"op": "npc_attitude", "npc": "margit", "delta": 1, "note": "talked shop all afternoon"},
       {"op": "remember", "subject": "margit", "content": "Cliff asked her opinion on the kite reel before anyone else's.", "importance": 1})
    p = one(conn, "SELECT * FROM npc_profiles WHERE character_id = ?", (m["id"],))
    assert p["attitude"] == 1 and p["personality"] == before
    assert "kite reel" in build_context(conn, denton, "I ask Margit about the siphon cables")["text"]


def test_player_payloads_never_carry_gm_material(client, conn, denton):
    secrets = [r["content"][:60] for r in rows(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ?", (denton,))]
    do(conn, denton, {"op": "remember", "subject": "vada", "content": "GMONLY-REGRESSION-BEAT-0042", "visibility": "gm"})
    for url in (f"/api/campaigns/{denton}", f"/api/campaigns/{denton}/characters", f"/api/campaigns/{denton}/npcs",
                f"/api/campaigns/{denton}/quests", f"/api/campaigns/{denton}/timeline", f"/api/campaigns/{denton}/records?q=scout"):
        body = client.get(url).text
        assert "GMONLY-REGRESSION-BEAT-0042" not in body
        assert not any(s in body for s in secrets if len(s) >= 40), url
    assert client.get("/api/gm/campaigns/%d/diagnostics" % denton).status_code == 403


def test_rule_citations_come_from_retrieved_passages(conn, denton, tmp_path):
    import pymupdf
    from app.gm.tools import ToolRunner
    from app.reference import rulebooks
    from app.rules.dice import DiceRoller
    pdf = tmp_path / "rb" / "mini_rulebook.pdf"
    pdf.parent.mkdir()
    doc = pymupdf.open()
    for t in ("Intro page.", "Evade: spend an Action to Evade every Attack declared against you this round."):
        doc.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 760), t, fontsize=11)
    doc.save(pdf)
    rc = rulebooks.open_db(tmp_path / "rb.db")
    rulebooks.ingest_directory(rc, pdf.parent)
    run = ToolRunner(conn, rc, DiceRoller(), denton, 0)
    out = run.run("search_rules", {"queries": ["evade attack action"]})
    refs = [p["ref"] for p in out["result"]["official"]]
    assert refs and refs[0].endswith("p. 2") and run.effects.rules_cited == refs


def test_restart_keeps_continuity(conn, denton, settings):
    from app.db import connect
    first = build_context(conn, denton, "Where is everyone?")["text"]
    fresh = connect(settings.campaign_db)          # a new process/connection sees the same persisted briefing
    assert build_context(fresh, denton, "Where is everyone?")["text"] == first
    fresh.close()


def test_repeated_request_never_applies_twice(client, conn, denton, settings):
    class OneHour(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("apply_changes", {"changes": [{"op": "advance_time", "minutes": 60, "reason": "browse stalls"}]})],
                                        "An hour of haggling passes.")
            return ProviderResponse([], "done")
    client.app_state.turns = TurnService(settings, OneHour, run_async=False)
    key = uuid.uuid4().hex
    t0 = state(conn, denton)[0]
    for _ in range(3):
        client.post(f"/api/campaigns/{denton}/turns", json={"text": "We browse the stalls for an hour.", "idempotency_key": key})
    assert state(conn, denton)[0] == t0 + 60
    assert one(conn, "SELECT usage_json FROM turn_diagnostics ORDER BY turn_id DESC LIMIT 1")["usage"]["game_time_after"] == t0 + 60
