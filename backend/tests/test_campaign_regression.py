"""Regression tests on the real Denton campaign, imported from a throwaway clone of the ledger into a temp database.
The real ledger clone and data/campaign.db are never touched. Skipped when the ledger clone is absent."""
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

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


def test_briefing_carries_what_the_gm_needs_and_the_continuity_report_passes(conn, denton):
    from app.gm.integrity import continuity
    ctx = build_context(conn, denton, "What is everyone doing right now, and what do we still owe people?")
    text = ctx["text"]
    for needle in ("[prison-recon]", "[training-market]", "[saferoom]", "Personal Stabber", "siphon", "reservation", "commitment"):
        assert needle.lower() in text.lower(), needle
    assert ctx["categories"]["state"] > 0 and ctx["categories"]["contract_rules"] > 0
    report = continuity(conn, denton, ctx, [], 0, 0, [])
    failing = [c for c in report["checks"] if c["status"] != "pass"]
    assert not failing, failing


def test_cliff_dossier_preserves_the_record_exactly(client, conn, denton):
    """The dossier is a view: every value must equal the saved record (Cliff as the regression fixture)."""
    cliff = ids(conn, denton)["cliff"]
    d = client.get(f"/api/campaigns/{denton}/characters/{cliff['id']}/dossier").json()
    sheet = client.get(f"/api/campaigns/{denton}/characters/{cliff['id']}").json()
    assert (d["name"], d["level"], d["race"], d["class_name"], d["crawler_number"]) == \
        (sheet["name"], sheet["level"], sheet["race"], sheet["class_name"], sheet["crawler_number"])
    for a in d["attributes"]:
        assert a["value"] == sheet["stats"]["stats"][a["key"]] and a["modifier"] == sheet["stats"]["modifiers"][a["key"]]
    c = d["combat"]
    assert (c["health"]["current"], c["health"]["max"], c["mana"]["current"], c["mana"]["max"], c["ai_favor"]) == \
        (sheet["hp_current"], sheet["hp_max"], sheet["mana_current"], sheet["mana_max"], sheet["ai_favor"])
    for k in ("evade", "damage_resistance", "move", "step"):
        assert c[k] == sheet["derived"][k]
    by_id = {s["id"]: s for s in sheet["skills"]}
    assert len(d["skills"]) == len(sheet["skills"])  # one canonical card per skill
    for s in d["skills"]:
        raw = by_id[s["id"]]
        assert s["rank"] == raw["rank"] and s["gear_granted"] == (raw["kind"] == "equipment_skill")
        assert s["specialty"] == (raw["properties"] or {}).get("specialty")
    eng = next(s for s in d["skills"] if s["name"] == "Engineering")
    assert (eng["rank"], eng["specialty"], eng["perk"], eng["category"]) == (8, "Portable Traps", "Quick Deployment", "engineering")
    assert next(s for s in d["skills"] if s["name"] == "Aiming")["gear_granted"] is True
    assert {"Portable Traps", "Quick Deployment", "Make It Work"} <= {h["title"] for h in d["build"]["highlights"]}
    owned = {i["id"]: i for i in sheet["items"]}
    for i in d["equipment"]["equipped"] + d["equipment"]["carried"]:
        assert i["id"] in owned and i["equipped"] == bool(owned[i["id"]]["equipped"]) and i["quantity"] == owned[i["id"]]["quantity"]
    assert all(i.get("shared") for i in d["equipment"]["linked_shared"])  # shared stash shown as shared, never as owned
    assert all(q["kind"] != "reservation" or q["title"] not in {i["name"] for i in d["equipment"]["equipped"]} for q in d["equipment"]["commitments"])
    assert d["history"] and all(h["text"] for h in d["history"])
    # viewing changes nothing
    assert client.get(f"/api/campaigns/{denton}/characters/{cliff['id']}").json() == sheet


def test_every_party_dossier_builds_without_gm_material(client, conn, denton):
    secrets_text = " ".join(r["content"] for r in rows(conn, "SELECT content FROM gm_secrets WHERE campaign_id = ?", (denton,)))
    for ch in rows(conn, "SELECT id, name FROM characters WHERE campaign_id = ?", (denton,)):
        r = client.get(f"/api/campaigns/{denton}/characters/{ch['id']}/dossier")
        assert r.status_code == 200, ch["name"]
        body = r.text
        # no long run of GM-only text appears in any dossier
        sample = [s for s in re.split(r"(?<=[.!?])\s+", secrets_text) if len(s) > 60][:400]
        assert not any(s in body for s in sample), ch["name"]


def test_gold_stabber_correction_is_evidenced_reversible_and_reaches_the_ledger(conn, denton):
    from app.ledger import sync
    from app.reconcile import STABBER_NOTE_NEW, corrections, gold_stabber_correction
    root = sync.link(conn, denton)["root"]
    res = gold_stabber_correction(conn, denton, root, decided_by="owner (test)")
    gold = one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Gold'", (denton,))
    assert gold["quantity"] == 4016
    q = one(conn, "SELECT progress, details_json FROM quests WHERE campaign_id = ? AND title = 'Reserved: Personal Stabber'", (denton,))
    assert q["details"]["payment"] == "paid" and q["details"]["owned"] is False and "Secondhand Saviors" in q["progress"]
    assert not one(conn, "SELECT id FROM items WHERE campaign_id = ? AND lower(name) LIKE '%stabber%'", (denton,))  # not owned
    cor = corrections(conn, denton)[0]
    assert len(cor["evidence"]) == 4 and cor["decided_by"] == "owner (test)" and cor["save_point_id"] == res["save_point_id"]
    ctx = build_context(conn, denton, "How much gold do we have?")["text"]
    assert "Gold ×4016" in ctx and "Paid 30 Gold" in ctx
    with pytest.raises(ValueError, match="not the 4,046"):  # can't be applied twice
        gold_stabber_correction(conn, denton, root, decided_by="owner (test)")
    pv = sync.preview(conn, denton, fetch=False)
    mine = [c for c in pv["changes"] if c["file"] == "state/shared_inventory.yaml"]
    assert mine and all(c["status"] == "clean" for c in pv["changes"]) and pv["blockers"] == []
    out = sync.apply(conn, denton, fingerprint=pv["fingerprint"], selected=None, message="Correct Gold and Stabber",
                     author_name="Test", author_email="test@example.com")
    ledger = yaml.safe_load((root / "state/shared_inventory.yaml").read_text(encoding="utf-8"))
    assert next(i for i in ledger["items"] if i["id"] == "gold")["quantity"] == 4016 and STABBER_NOTE_NEW in ledger["notes"]
    diff = subprocess.run(["git", "-C", str(root), "show", "--format=", out["commit"], "--", "state/shared_inventory.yaml"],
                          capture_output=True, text=True, encoding="utf-8").stdout
    changed = [l for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    assert len(changed) <= 8, changed  # quantity line and the one note, nothing else reformatted


def test_clock_and_location_corrections_reach_hud_context_and_ledger(conn, denton):
    from app.ledger import sync
    from app.memory.views import campaign_summary
    from app.reconcile import clock_correction, location_correction
    clock_correction(conn, denton, decided_by="owner (test)")
    location_correction(conn, denton, decided_by="owner (test)")
    camp = campaign_summary(conn, denton)
    assert camp["game_time_minutes"] == 1519 and camp["floor_clock"]["remaining_minutes"] == 12881
    assert camp["current_location"].startswith("Gilded Feathers") and not camp["safe_room"]
    ctx = build_context(conn, denton, "Where are we?")["text"]
    assert "[NOT a saferoom]" in ctx and "but the current scene is at" not in ctx and "FLOOR COLLAPSE in 214h41m" in ctx
    groups = {g["key"]: g["location"] for g in camp["groups"]}
    assert groups["saferoom"].startswith("The Molting Pigeon") and groups["prison-recon"] is None  # unknown stays unknown
    with pytest.raises(ValueError):
        clock_correction(conn, denton, decided_by="owner (test)")
    with pytest.raises(ValueError):
        location_correction(conn, denton, decided_by="owner (test)")
    pv = sync.preview(conn, denton, fetch=False)
    assert pv["blockers"] == [] and all(c["status"] == "clean" for c in pv["changes"])
    sync.apply(conn, denton, fingerprint=pv["fingerprint"], selected=None, message="Clock and location", author_name="Test", author_email="t@example.com")
    root = sync.link(conn, denton)["root"]
    cy = yaml.safe_load((root / "state/campaign.yaml").read_text(encoding="utf-8"))
    assert cy["floor_clock"]["remaining_minutes"] == 12881 and cy["safe_room_status"] is False and cy["current_location"].startswith("Gilded Feathers")


def test_silver_earth_box_correction_settles_status_adds_rewards_and_reaches_the_ledger(conn, denton):
    from app.ledger import sync
    from app.reconcile import silver_earth_box_correction
    root = sync.link(conn, denton)["root"]
    silver_earth_box_correction(conn, denton, root, decided_by="owner (test)")
    cliff = one(conn, "SELECT id, sheet_json FROM characters WHERE campaign_id = ? AND name LIKE 'Cliff%'", (denton,))
    assert cliff["sheet"]["floor_3_selection"]["silver_earth_box"]["status"] == "opened-and-resolved"
    assert one(conn, "SELECT quantity FROM items WHERE owner_character_id = ? AND name = 'Family-size bag of peanut M&Ms'", (cliff["id"],))["quantity"] == 1
    with pytest.raises(ValueError):
        silver_earth_box_correction(conn, denton, root, decided_by="owner (test)")
    pv = sync.preview(conn, denton, fetch=False)
    assert pv["blockers"] == [] and all(c["status"] == "clean" for c in pv["changes"])
    sync.apply(conn, denton, fingerprint=pv["fingerprint"], selected=None, message="Boxes", author_name="Test", author_email="t@example.com")
    doc = yaml.safe_load((root / "state/characters/cliff.yaml").read_text(encoding="utf-8"))
    assert doc["floor_3_selection"]["silver_earth_box"]["status"] == "opened-and-resolved"
    assert any(e.get("name") == "Family-size bag of peanut M&Ms" and e.get("quantity") == 1 for e in doc["inventory"])
    camp = yaml.safe_load((root / "state/campaign.yaml").read_text(encoding="utf-8"))
    assert camp["floor_3_selection_resolution"]["unopened_rewards"]["silver_earth_boxes"] == 0


def test_write_back_mirrors_the_live_advancement_count_into_campaign_current_state(conn, denton):
    from app.ledger import sync
    root = sync.link(conn, denton)["root"]
    assert yaml.safe_load((root / "state/campaign.yaml").read_text(encoding="utf-8"))["current_state"]["advancement_cycles"] == "11/12"
    pv = sync.preview(conn, denton, fetch=False)
    mirror = [c for c in pv["changes"] if c["summary"].startswith("Campaign mirror of advancement cycles")]
    assert len(mirror) == 1 and mirror[0]["detail"] == {"from": "11/12", "to": "4/12"} and mirror[0]["status"] == "clean"
    sync.apply(conn, denton, fingerprint=pv["fingerprint"], selected=None, message="Mirror", author_name="Test", author_email="t@example.com")
    camp = yaml.safe_load((root / "state/campaign.yaml").read_text(encoding="utf-8"))
    assert camp["current_state"]["advancement_cycles"] == "4/12"
    assert camp["floor_3_molting_pigeon_checkpoint"]["advancement_cycles"] == "11/12"  # the DCC-DENTON-014 snapshot stays as recorded
    assert not [c for c in sync.preview(conn, denton, fetch=False)["changes"] if c["summary"].startswith("Campaign mirror")]


def test_import_report_corrections_settle_ammo_assets_and_driscoll_and_reach_the_ledger(conn, denton):
    from app.ledger import sync
    from app.reconcile import ammunition_correction, driscoll_correction, floor_2_assets_correction
    root = sync.link(conn, denton)["root"]
    ammunition_correction(conn, denton, root, decided_by="owner (test)")
    floor_2_assets_correction(conn, denton, root, decided_by="owner (test)")
    driscoll_correction(conn, denton, root, decided_by="owner (test)")
    q = lambda n: one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = ?", (denton, n))["quantity"]
    assert q("Crossbow Bolt") == 64 and q("Pistol Round") == 22 and q("Crawler Biscuit") == 40 and q("Explosive Rune Plate") == 3
    assert "Molting Pigeon" in one(conn, "SELECT description FROM items WHERE campaign_id = ? AND name LIKE '1987 Toyota Pickup%'", (denton,))["description"]
    for fn in (ammunition_correction, floor_2_assets_correction):
        with pytest.raises(ValueError):
            fn(conn, denton, root, decided_by="owner (test)")
    pv = sync.preview(conn, denton, fetch=False)
    assert pv["blockers"] == [] and all(c["status"] == "clean" for c in pv["changes"])
    sync.apply(conn, denton, fingerprint=pv["fingerprint"], selected=None, message="Import report", author_name="Test", author_email="t@example.com")
    inv = yaml.safe_load((root / "state/shared_inventory.yaml").read_text(encoding="utf-8"))
    bolt = next(e for e in inv["items"] if e.get("id") == "crossbow-bolt")
    assert bolt["quantity"] == 64 and bolt["quantity_semantics"].startswith("Exact count by owner ruling")
    assert inv["ammunition_reconciliation"]["status"] == "resolved-by-owner-ruling"
    assert any(e.get("name") == "Crawler Biscuit" and e.get("quantity") == 40 for e in inv["items"])
    camp = yaml.safe_load((root / "state/campaign.yaml").read_text(encoding="utf-8"))
    assert camp["shared_resource_snapshot"]["pistol_rounds"] == 22
    assert yaml.safe_load((root / "state/characters/mad-driscoll.yaml").read_text(encoding="utf-8"))["roster_category"] == "deceased_npcs"


def test_import_report_shows_resolved_and_open_entries_and_consistency_finds_disagreements(conn, denton):
    from app.consistency import summary
    from app.ledger import sync
    from app.reconcile import (advancement_mirror_record, annotate_report, clock_correction, gold_stabber_correction,
                               location_correction)
    root = sync.link(conn, denton)["root"]
    before = {c["check"]: c["ok"] for c in summary(conn, denton)["checks"]}
    assert before["Reserved: Personal Stabber"] is False  # paid in the market record, 'no payment' in the stash note
    gold_stabber_correction(conn, denton, root, decided_by="owner (test)")
    clock_correction(conn, denton, decided_by="owner (test)")
    location_correction(conn, denton, decided_by="owner (test)")
    advancement_mirror_record(conn, denton, decided_by="owner (test)")
    rep = annotate_report(conn, denton, one(conn, "SELECT report_json FROM import_reports WHERE campaign_id = ? ORDER BY id DESC", (denton,))["report"])
    status = {e["key"]: e["status"] for e in rep["conflicts"]}
    assert status["conflict:gold"] == status["conflict:floor clock"] == status["conflict:advancement cycles"] == "resolved"
    assert status["conflict:Silver Earth Box status:cliff"] == "open"  # its correction was not applied here
    assert all(e["status"] == "open" for e in rep["manual_review"]) and rep["open"] > 0
    checks = {c["check"]: c for c in summary(conn, denton)["checks"]}
    for name in ("Gold", "Floor clock (minutes remaining)", "Clock: Advancement cycles", "Location and saferoom", "Reserved: Personal Stabber"):
        assert checks[name]["ok"], checks[name]
    assert checks["Scout: position"]["ok"] is False  # the stale checkpoint location is reported, not silently fixed


def test_positions_follow_groups_in_gm_context_consistency_and_ledger(conn, denton):
    from app.consistency import summary
    from app.ledger import sync
    from app.reconcile import location_correction, positions_correction
    root = sync.link(conn, denton)["root"]
    location_correction(conn, denton, decided_by="owner (test)")
    positions_correction(conn, denton, root, decided_by="owner (test)")
    checks = {c["check"]: c for c in summary(conn, denton)["checks"]}
    assert all(v["ok"] for k, v in checks.items() if k.endswith(": position")), [k for k, v in checks.items() if not v["ok"]]
    ctx = build_context(conn, denton, "what is Scout doing?")["text"]
    assert "NOT PRESENT (group prison-recon, location unknown)" in ctx
    with pytest.raises(ValueError):
        positions_correction(conn, denton, root, decided_by="owner (test)")
    pv = sync.preview(conn, denton, fetch=False)
    assert pv["blockers"] == [] and all(c["status"] == "clean" for c in pv["changes"])
    sync.apply(conn, denton, fingerprint=pv["fingerprint"], selected=None, message="Positions", author_name="Test", author_email="t@example.com")
    sheet = lambda s: yaml.safe_load((root / f"state/characters/{s}.yaml").read_text(encoding="utf-8"))["location"]
    assert sheet("cliff") == "Gilded Feathers, Heavensworth" and sheet("scout").startswith("Not recorded: with the") and sheet("jonai") == "The Molting Pigeon"


def test_immediate_objective_is_its_linked_quests_and_drops_finished_ones(conn, denton):
    from app.memory.views import campaign_summary
    from app.reconcile import objective_correction
    objective_correction(conn, denton, decided_by="owner (test)")
    qs = campaign_summary(conn, denton)["quests"]
    now = [q["title"][:20] for q in qs if q["immediate"]]
    assert now == ["Use current daylight", "Complete final recon", "Build the Margit-ass"] and all(q["immediate"] for q in qs[:3])
    ctx = build_context(conn, denton, "status")["text"]
    assert "Immediate objective (quest records" in ctx and "[quest #" in ctx and "Use the remaining daylight" not in ctx
    with transaction(conn):
        apply_change(conn, denton, None, {"op": "quest", "title": qs[0]["title"], "status": "completed"})
        apply_change(conn, denton, None, {"op": "quest", "title": "Claim the Personal Stabber", "immediate": True})
    now = [q["title"] for q in campaign_summary(conn, denton)["quests"] if q["immediate"]]
    assert now[-1] == "Claim the Personal Stabber" and not now[0].startswith("Use current daylight")
    with pytest.raises(ValueError):
        objective_correction(conn, denton, decided_by="owner (test)")


def test_every_imported_house_rule_is_classified_with_existing_tests(conn, denton):
    import ast
    from app.rules import coverage
    from app.rules.coverage import MATRIX, STATUSES, key_for
    tree = ast.parse(Path(coverage.__file__).read_text(encoding="utf-8"))
    keys = [k.value for n in ast.walk(tree) if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "MATRIX" for k in n.value.keys]
    assert len(keys) == len(set(keys)), "a rule appears twice in MATRIX (the later entry silently wins)"
    rules = rows(conn, "SELECT section, title FROM house_rules WHERE campaign_id = ?", (denton,))
    assert len(rules) == 104
    assert [key_for(r) for r in rules if key_for(r) not in MATRIX] == []
    assert len({key_for(r) for r in rules}) == len(rules)  # no two rules share an entry
    defined = set(re.findall(r"^def (test_\w+)", "\n".join(p.read_text(encoding="utf-8") for p in Path(__file__).parent.glob("test_*.py")), re.M))
    for k, e in MATRIX.items():
        assert e["status"] in STATUSES, k
        assert not set(e["tests"]) - defined, (k, set(e["tests"]) - defined)
        assert e["status"] != "tested" or e["tests"], f"{k} is 'tested' but cites no test"
        assert e["status"] not in ("missing", "partial", "untested") or (e["gap"] and e["priority"]), f"{k} needs a gap and a priority"


def test_rule_labels_come_from_the_matrix(conn, denton):
    from app.importer.ledger import refresh_mechanical_status
    from app.rules.coverage import classify
    refresh_mechanical_status(conn)
    label = {r["title"]: r["mechanical_status"] for r in rows(conn, "SELECT title, mechanical_status FROM house_rules WHERE campaign_id = ?", (denton,))}
    assert label["Mending"] == "implemented" and label["Rest"] == "implemented"      # import labelled both pending
    assert label["Party Skill Checks"] == "pending" and label["Call a Play"] == "pending"
    assert classify({"section": "7.9", "title": "Crazy Dungeon critical flourish"})["status"] == "gm_ruling"


def _craft(conn, cid, dice, **a):
    from app.gm.tools import ToolRunner
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    run = ToolRunner(conn, None, DiceRoller(source=scripted(dice)), cid, 0)
    return run.run("craft", a), run


def test_gold_siphon_is_built_from_its_declared_design(conn, denton):
    junk = lambda: one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Miscellaneous Junk'", (denton,))["quantity"]
    t0, j0 = one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (denton,))["game_time_minutes"], junk()
    bad, _ = _craft(conn, denton, [10], crafter="Nate", item="Pass-Pulse Gold Siphon", project="Pass-Pulse Gold Siphon")
    assert not bad["ok"] and "led by Cliff" in bad["result"]["error"] and junk() == j0            # wrong lead: nothing spent
    res, run = _craft(conn, denton, [10, 4, 4], crafter="Cliff", item="Pass-Pulse Gold Siphon", project="Pass-Pulse Gold Siphon",
                      help=[{"helper": "Margit Winchgrinder", "dice": "1d6"}])
    out = res["result"]
    assert res["ok"] and out["success"] and out["skill"] == "Engineering" and out["check"]["dc"] == 16
    assert "keep 10" in out["check"]["summary"]                                                     # Margit's Advantage from the design
    assert junk() == j0 - 8 and one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (denton,))["game_time_minutes"] == t0 + 480
    assert one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND name = 'Pass-Pulse Gold Siphon'", (denton,))["quantity"] == 1
    q = one(conn, "SELECT status, details_json FROM quests WHERE campaign_id = ? AND title LIKE 'Build the Margit%'", (denton,))
    assert q["status"] == "completed" and q["details"]["status"] == "built" and run.effects.rolls
    again, _ = _craft(conn, denton, [10], crafter="Cliff", item="Pass-Pulse Gold Siphon", project="Pass-Pulse Gold Siphon")
    assert not again["ok"] and "already" in again["result"]["error"] and junk() == j0 - 8           # cannot be built twice


def test_failed_crafting_consumes_then_salvages_by_margin_and_nothing_is_spent_when_short(conn, denton):
    junk = lambda: one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Miscellaneous Junk'", (denton,))["quantity"]
    j0 = junk()
    res, _ = _craft(conn, denton, [3], crafter="Cliff", item="Pulley Rig", size="small", moving_parts=True, dc=25)
    out = res["result"]                                                                           # 3 + 8 + 5 = 16 vs 25: missed by 9
    assert res["ok"] and not out["success"] and out["salvaged"] == ["2x Miscellaneous Junk"] and junk() == j0 - 4 + 2
    assert not one(conn, "SELECT 1 FROM items WHERE campaign_id = ? AND name = 'Pulley Rig'", (denton,))
    res, _ = _craft(conn, denton, [1], crafter="Cliff", item="Desk Barricade", size="medium", dc=16)  # nat 1: 1+2+5 = 8, missed by 8 -> half
    assert res["ok"] and res["result"]["salvaged"] == ["16x Miscellaneous Junk"] and res["result"]["skill"] == "Fabricate"
    j1, t1 = junk(), one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (denton,))["game_time_minutes"]
    short, _ = _craft(conn, denton, [20], crafter="Cliff", item="Barn", size="colossal")
    assert not short["ok"] and "nothing was spent" in short["result"]["error"]
    assert junk() == j1 and one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (denton,))["game_time_minutes"] == t1
    magic, _ = _craft(conn, denton, [20], crafter="Cliff", item="+2 Dexterity Dagger", kind="magical")
    assert not magic["ok"] and "Fourth Floor" in magic["result"]["error"]
    common, _ = _craft(conn, denton, [20], crafter="Cliff", item="Roll of Tape", kind="common")
    assert common["ok"] and junk() == j1 - 1 and one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND name = 'Roll of Tape'", (denton,))["quantity"] == 1


def test_gm_turn_crafts_the_siphon_once_with_one_set_of_rolls(client, conn, denton, settings):
    class Build(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("craft", {"crafter": "Cliff", "item": "Pass-Pulse Gold Siphon", "project": "Pass-Pulse Gold Siphon",
                                                            "help": [{"helper": "Margit Winchgrinder", "dice": "1d6"}]})], "")
            return ProviderResponse([], "Through the night, Cliff and Margit work on the siphon.")
    client.app_state.turns = TurnService(settings, Build, run_async=False)
    j0 = one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Miscellaneous Junk'", (denton,))["quantity"]
    t0 = state(conn, denton)[0]
    r = client.post(f"/api/campaigns/{denton}/turns", json={"text": "After nightfall Cliff and Margit build the Pass-Pulse Gold Siphon.",
                                                            "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    assert state(conn, denton)[0] == t0 + 480
    assert one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Miscellaneous Junk'", (denton,))["quantity"] == j0 - 8
    rolls = rows(conn, "SELECT reason FROM dice_rolls WHERE campaign_id = ? AND reason LIKE 'Craft %' AND rolled_back = 0", (denton,))
    assert len(rolls) == 1  # dice drawn once and reused by the staged replay, never re-rolled
