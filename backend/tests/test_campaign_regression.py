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
    assert label["Party Skill Checks"] == label["Call a Play"] == "implemented" and label["Traps"] == "partial"
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
    rolls = rows(conn, "SELECT total, target, outcome FROM dice_rolls WHERE campaign_id = ? AND reason LIKE 'Craft %' AND rolled_back = 0", (denton,))
    assert len(rolls) == 1  # dice drawn once and reused by the staged replay, never re-rolled
    from app.rules.crafting import _salvaged
    r = rolls[0]  # real dice here: check the result follows the rule for whatever was rolled
    back = 0 if r["outcome"] == "success" else _salvaged(8, r["target"] - r["total"])
    assert one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = 'Miscellaneous Junk'", (denton,))["quantity"] == j0 - 8 + back
    built = one(conn, "SELECT 1 FROM items WHERE campaign_id = ? AND name = 'Pass-Pulse Gold Siphon'", (denton,))
    assert bool(built) == (r["outcome"] == "success")


def _party(conn, cid, dice, **a):
    from app.gm.tools import ToolRunner
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    run = ToolRunner(conn, None, DiceRoller(source=scripted(dice)), cid, 0)
    return run.run("party_check", a), run


def test_offscreen_recon_is_resolved_secretly_with_declared_stakes(conn, denton):
    rolls_before = one(conn, "SELECT count(*) AS n FROM dice_rolls WHERE campaign_id = ?", (denton,))["n"]
    for bad, why in ((dict(group="training-market", offscreen=True, objective="x", stakes="y"), "includes the player character"),
                     (dict(group="prison-recon", offscreen=True, objective="Scout Fallen Watch"), "declare the group's objective and the stakes"),
                     (dict(participants=["Cliff"]), "at least two participants"),
                     (dict(group="prison-recon", participants=["Cliff", "Scout"]), "only members of")):
        res, _ = _party(conn, denton, [10], skill="Stealth", stat="dex", reason="test", **bad)
        assert not res["ok"] and why in res["result"]["error"], (bad, res)
    assert one(conn, "SELECT count(*) AS n FROM dice_rolls WHERE campaign_id = ?", (denton,))["n"] == rolls_before  # refused: nothing rolled
    res, run = _party(conn, denton, [20], group="prison-recon", offscreen=True, skill="Stealth", stat="dex", dc_kind="opposed",
                      antagonist_mod=3, reason="Fallen Watch reconnaissance",
                      objective="Map the Fallen Watch guard rotation without being seen",
                      stakes="They are spotted and must withdraw; the prison is alerted", on_success="They map the rotation and withdraw unseen")
    out = res["result"]
    assert res["ok"] and out["participants"] == 6 and out["success"] and out["hidden"] and run.effects.rolls == []  # secret rolls
    ev = one(conn, "SELECT summary, visibility, payload_json FROM events WHERE campaign_id = ? AND type = 'offscreen_resolved' ORDER BY id DESC", (denton,))
    assert ev["visibility"] == "gm" and ev["payload"]["outcome"].startswith("They map") and len(ev["payload"]["roll_ids"]) == 6
    res, _ = _party(conn, denton, [1], group="prison-recon", offscreen=True, skill="Stealth", stat="dex", reason="second pass",
                    objective="Check the east wall", stakes="They are spotted and must withdraw")
    assert res["ok"] and not res["result"]["success"] and res["result"]["outcome"] == "They are spotted and must withdraw"


def test_onscreen_party_check_rolls_everyone_and_shows_the_result(conn, denton):
    res, run = _party(conn, denton, [15], participants=["Cliff", "Vada", "Nate", "Priya"], skill="Climbing", stat="dex", dc=12,
                      reason="climb down to the market roofs together")
    out = res["result"]
    assert res["ok"] and out["participants"] == 4 and len(run.effects.rolls) == 4 and not out["hidden"]
    assert out["summary"].startswith("Party ") and out["summary"] in run.effects.changes


def test_prison_assault_mechanics_ammo_called_play_intervene_and_catcher(conn, denton):
    from app.rules.combat import Combat, MobAttack, StartCombat, StatBlock
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    cb = lambda dice: Combat(conn, denton, None, DiceRoller(source=scripted(dice)))
    qty = lambda n: one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = ?", (denton, n))["quantity"]
    from app.ledger import sync
    from app.reconcile import ammunition_correction
    with pytest.raises(ChangeError, match="not established"):   # before the owner's ammunition ruling nobody can fire
        cb([1]).start(StartCombat(name="dry", party=["Cliff", "Marcus"], foes=[StatBlock(name="Rat", health_bar=[1], evade=5)]))
        cb([1]).declare([])
        cb([1]).resolve()
        cb([15]).attack("Cliff", "Rat", attack="Light Crossbow")
    cb([1]).end("test reset")
    ammunition_correction(conn, denton, sync.link(conn, denton)["root"], decided_by="owner (test)")
    bolts, rounds = qty("Crossbow Bolt"), qty("Pistol Round")
    cb([1]).start(StartCombat(name="Fallen Watch gate", party=["Cliff", "Marcus", "Vada", "Scout", "Terri", "Erin"], setup="test",
                              foes=[StatBlock(name="Warden", kind="elite", health_bar=[10] * 20, evade=25,
                                              attacks=[MobAttack(name="Swipe", to_hit=12, damage="1d4"),
                                                       MobAttack(name="Sweep", to_hit=12, damage="1d4", area=True)])]))
    cb([1]).declare([{"mob": "Warden", "attack": "Sweep", "target": "Vada"}, {"mob": "Warden", "attack": "Swipe", "target": "Vada"}])
    with pytest.raises(ChangeError, match="no Ranks in Catcher"):
        cb([1]).catch("Cliff", "Vada")
    with pytest.raises(ChangeError, match="non-Area"):
        cb([1]).catch("Scout", "Vada", attack="Sweep")
    assert "+5 DR from Catcher Rank 6" in cb([1]).catch("Scout", "Vada", attack="Swipe")["summary"]
    res = cb([4]).resolve()
    assert any("caught for Vada" in l for l in res["lines"]) and not any(l.startswith("Scout — Evade") for l in res["lines"])
    # Vada still takes the Area Sweep she did not Evade; the Swipe went to Scout
    assert sum(1 for l in res["lines"] if "→ Vada" in l) == 1 and sum(1 for l in res["lines"] if "→ Scout" in l) == 1

    # ammunition: one bolt per shot, Crossbow once per round, nothing spent on a refused shot
    hit = cb([15, 4, 4]).attack("Cliff", "Warden", attack="Light Crossbow")
    assert hit["hit"] and qty("Crossbow Bolt") == bolts - 1 and any("left)" in l for l in hit["lines"])
    with pytest.raises(ChangeError, match="once per round"):
        cb([20]).attack("Cliff", "Warden", attack="Light Crossbow")
    with pytest.raises(ChangeError, match="Arrow supply"):
        cb([20]).attack("Erin", "Warden", attack="Shortbow", stat="dexterity")
    assert qty("Crossbow Bolt") == bolts - 1
    # Called Play: +6 turns Marcus's miss by 3 into a hit
    assert cb([6, 2]).call_play("Vada", "Marcus", "smash the Warden's lantern arm", target="Warden")["bonus"] == 6
    play = cb([8, 3, 3]).attack("Marcus", "Warden", attack="Pipe Club")
    assert play["hit"] and any("Called Play +6: 28" in l for l in play["lines"])
    # Intervene: Erin's +6 turns Marcus's Near Miss into a hit
    near = cb([9]).attack("Marcus", "Warden", attack="Pipe Club")
    assert not near["hit"] and "Near Miss" in near["summary"]
    iv = cb([6, 3, 3]).intervene("Erin")
    assert iv["hit"] and "total 29" in iv["summary"]
    with pytest.raises(ChangeError, match="already hit"):
        cb([6]).intervene("Terri")
    # Handgun: a Critical Fail still fires a round and needs a reload Action
    cb([1]).attack("Terri", "Warden", attack="Service Pistol")
    assert qty("Pistol Round") == rounds - 1
    with pytest.raises(ChangeError, match="reload"):
        cb([20]).attack("Terri", "Warden", attack="Service Pistol")
    assert "reloaded" in cb([1]).spend_action("Terri", "Reload")["summary"]
    end = cb([1]).end("withdrew")
    assert end["ammunition_fired"] == {"Crossbow Bolt": 1, "Pistol Round": 1} and any("Ammunition fired" in l for l in end["lines"])


def test_traps_need_a_record_lock_while_deployed_and_spend_real_costs(conn, denton):
    from app.gm.tools import ToolRunner
    from app.rules.combat import Combat, MobAttack, StartCombat, StatBlock
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    tool = lambda dice, **a: ToolRunner(conn, None, DiceRoller(source=scripted(dice)), denton, 0).run("trap", a)
    qty = lambda n: one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND owner_character_id IS NULL AND name = ?", (denton, n))["quantity"]
    clock = lambda: one(conn, "SELECT game_time_minutes FROM campaigns WHERE id = ?", (denton,))["game_time_minutes"]
    bad = tool([10], action="deploy", trap="Cinch Trap", location="gate")
    assert not bad["ok"] and "no trap record" in bad["result"]["error"]
    status = {t["name"]: t for t in tool([1], action="status")["result"]["traps"]}
    assert status["Cinch Trap"]["recorded"] is False and "Scatterburst Trap" in status
    spec = {"components": "spring housing, one paralytic dart", "ammunition": {"item": "Paralytic Dart", "per_trigger": 1},
            "deploy": {"actions": 1, "minutes": 10}, "trigger": "pressure plate", "area": "one 5-foot square", "check": {"kind": "attack", "attack_bonus": 6},
            "damage": "1d4 Piercing", "effects": "Paralyzed until it succeeds on a CON Stat Check (GM)", "reset": {"actions": 2, "minutes": 5},
            "reset_notes": "reload a dart and re-cock the spring", "bulk": "Small", "label": "INTERPRETATION", "source": "test ruling"}
    assert tool([1], action="record", trap="Porcupine Pod", spec=spec)["ok"]
    darts = qty("Paralytic Dart")
    Combat(conn, denton, None, DiceRoller(source=scripted([1]))).start(StartCombat(
        name="gate", party=["Cliff", "Marcus"], setup="test", foes=[StatBlock(name="Warden", health_bar=[4] * 10, evade=12, attacks=[MobAttack(name="Swipe", to_hit=10, damage="1d4")])]))
    Combat(conn, denton, None, DiceRoller()).declare([])
    Combat(conn, denton, None, DiceRoller(source=scripted([1]))).resolve()
    t0 = clock()
    dep = tool([1], action="deploy", trap="Porcupine Pod", location="gate lane", by="Cliff")
    assert dep["ok"] and "spends 1 Action" in dep["result"]["summary"] and clock() == t0   # in combat it costs Actions, not time
    locked = tool([1], action="record", trap="Porcupine Pod", spec={**spec, "damage": "3d6 Piercing"})
    assert not locked["ok"] and "locked" in locked["result"]["error"]
    hit = tool([15, 4], action="trigger", trap="Porcupine Pod", target="Warden")
    assert hit["ok"] and hit["result"]["hit"] and hit["result"]["condition"] == "triggered" and qty("Paralytic Dart") == darts - 1
    assert "Paralyzed" in hit["result"]["summary"]
    again = tool([15], action="trigger", trap="Porcupine Pod", target="Warden")
    assert not again["ok"] and "only an armed trap" in again["result"]["error"]
    short = tool([1], action="reset", trap="Porcupine Pod", by="Cliff")   # 2 Actions needed, Cliff has 1 left
    assert not short["ok"] and "no Actions left" in short["result"]["error"]
    enc = one(conn, "SELECT combatants_json FROM encounters WHERE campaign_id = ? AND status = 'active'", (denton,))["combatants"]
    assert next(c for c in enc["combatants"] if c["name"].startswith("Cliff"))["actions_left"] == 1   # the refused reset spent nothing
    Combat(conn, denton, None, DiceRoller()).end_round()
    assert tool([1], action="reset", trap="Porcupine Pod", by="Cliff")["result"]["summary"].endswith("re-armed at gate lane")
    Combat(conn, denton, None, DiceRoller()).end("done")
    assert "ready" in tool([1], action="stow", trap="Porcupine Pod")["result"]["summary"]
    t1 = clock()
    assert tool([1], action="deploy", trap="Porcupine Pod", location="saferoom door")["ok"] and clock() == t1 + 10   # out of combat: time


def test_advancement_cycles_need_a_consequential_decision_and_one_combat_earns_one(conn, denton):
    """Playtest 1, H2: bookkeeping, travel, rests and every instruction inside one fight each counted a cycle."""
    from app.db import now_iso
    from app.gm.tools import ToolRunner
    from app.rules.combat import Combat, MobAttack, StartCombat, StatBlock
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    cyc = lambda: one(conn, "SELECT current FROM clocks WHERE campaign_id = ? AND key = 'advancement_cycles'", (denton,))["current"]

    def turn(words, changes=(), dice=(10,)):
        with transaction(conn):
            tid = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                               (denton, uuid.uuid4().hex, "processing", words, now_iso())).lastrowid
        run = ToolRunner(conn, None, DiceRoller(source=scripted(list(dice))), denton, tid)
        with transaction(conn):
            if changes:
                assert run.run("apply_changes", {"changes": list(changes)})["ok"]
            return run, run.run("count_cycle", {"decision": words, "resolution": "resolved"})

    c0 = cyc()
    for words, changes, why in (
            ("Quick status check: where is everyone?", [], "recap, status"),
            ("Marcus, hand your bolas to Priya.", [{"op": "transfer_item", "from_character": "Marcus", "to_character": "Priya", "name": "Bolas"}], "bookkeeping"),
            ("We walk back to the Molting Pigeon.", [{"op": "advance_time", "minutes": 20, "reason": "walk back"}], "bookkeeping"),
            ("I make my decision.", [], "nothing consequential")):
        _, res = turn(words, changes)
        assert not res["ok"] and why in res["result"]["error"], (words, res)
    assert cyc() == c0
    _, res = turn("If the armor is under 100 Gold, I buy it for Nate.", [
        {"op": "remove_item", "character": "shared", "name": "Gold", "quantity": 70, "cause": "armor"},
        {"op": "add_item", "character": "Nate", "name": "Used Fitted Torso Armor"}])
    assert res["ok"] and cyc() == c0 + 1
    audit = one(conn, "SELECT payload_json FROM events WHERE campaign_id = ? AND type = 'clock_changed' ORDER BY id DESC", (denton,))["payload"]
    assert audit["player_input"].startswith("If the armor") and audit["qualifying_events"]
    run = ToolRunner(conn, None, DiceRoller(), denton, one(conn, "SELECT max(id) AS m FROM turns", ())["m"])
    assert "already earned its cycle" in run.run("count_cycle", {"decision": "again", "resolution": "again"})["result"]["error"]

    # one combat earns one cycle, however many turns and instructions it takes
    Combat(conn, denton, None, DiceRoller(source=scripted([1]))).start(StartCombat(name="alley", party=["Cliff", "Marcus"], setup="test",
                                                                                      foes=[StatBlock(name="Rat", health_bar=[1] * 3, evade=30)]))
    Combat(conn, denton, None, DiceRoller()).declare([])
    Combat(conn, denton, None, DiceRoller(source=scripted([1]))).resolve()
    with transaction(conn):
        tid = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                           (denton, uuid.uuid4().hex, "processing", "Marcus, smash it.", now_iso())).lastrowid
    Combat(conn, denton, tid, DiceRoller(source=scripted([5]))).attack("Marcus", "Rat", attack="Pipe Club")
    with transaction(conn):
        first = ToolRunner(conn, None, DiceRoller(), denton, tid).run("count_cycle", {"decision": "fight the rat", "resolution": "fighting"})
    assert first["ok"]
    with transaction(conn):
        tid2 = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                            (denton, uuid.uuid4().hex, "processing", "Keep hitting it.", now_iso())).lastrowid
    Combat(conn, denton, tid2, DiceRoller()).end_round()
    Combat(conn, denton, tid2, DiceRoller()).declare([])
    Combat(conn, denton, tid2, DiceRoller(source=scripted([1]))).resolve()
    Combat(conn, denton, tid2, DiceRoller(source=scripted([5]))).attack("Marcus", "Rat", attack="Pipe Club")
    Combat(conn, denton, tid2, DiceRoller()).end("rat fled")
    with transaction(conn):
        second = ToolRunner(conn, None, DiceRoller(), denton, tid2).run("count_cycle", {"decision": "keep hitting", "resolution": "it fled"})
    assert not second["ok"] and "this combat already earned its cycle" in second["result"]["error"]
    assert cyc() == c0 + 2


def test_an_absent_characters_report_without_offscreen_resolution_is_flagged(conn, denton):
    from app.db import now_iso
    from app.gm.integrity import check as integrity
    from app.ledger import sync
    from app.reconcile import location_correction, positions_correction
    location_correction(conn, denton, decided_by="owner (test)")   # as on the live campaign: Cliff at Gilded Feathers, recon unknown
    positions_correction(conn, denton, sync.link(conn, denton)["root"], decided_by="owner (test)")
    with transaction(conn):
        tid = conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, created_at) VALUES (?,?,?,?,?)",
                           (denton, uuid.uuid4().hex, "processing", "I message the recon team.", now_iso())).lastrowid
    story = '**[Party Chat] Scout:** "Found three entry points!" **Maya (Party HUD):** "Six Brain Boilers in the courtyard."'
    flags = integrity(conn, denton, "I message the recon team.", story, [], [], [], [], tid)
    assert any("not with the player character" in f["message"] and "Maya" in f["message"] and "Scout" in f["message"] for f in flags)
    with transaction(conn):
        conn.execute("INSERT INTO events (campaign_id, turn_id, type, summary, payload_json, visibility, created_at) VALUES (?,?,?,?,?,?,?)",
                     (denton, tid, "offscreen_resolved", "Prison Recon (off-screen): report", "{}", "gm", now_iso()))
    assert not any("not with the player character" in f["message"] for f in integrity(conn, denton, "x", story, [], [], [], [], tid))


def test_one_location_model_campaign_group_and_members_move_together(conn, denton):
    """Playtest 1, H4/H5: the campaign and Cliff's group drifted apart, and a group placed somewhere left its members behind."""
    from app.consistency import summary
    from app.reconcile import location_correction, positions_correction
    from app.ledger import sync
    location_correction(conn, denton, decided_by="owner (test)")
    positions_correction(conn, denton, sync.link(conn, denton)["root"], decided_by="owner (test)")
    do(conn, denton, {"op": "set_location", "location": "Secondhand Saviors, Gilded Feathers, Heavensworth", "safe_room": False})
    camp = one(conn, "SELECT current_location, safe_room FROM campaigns WHERE id = ?", (denton,))
    g = one(conn, "SELECT location FROM party_groups WHERE campaign_id = ? AND key = 'training-market'", (denton,))
    assert g["location"] == camp["current_location"] == "Secondhand Saviors, Gilded Feathers, Heavensworth"
    assert one(conn, "SELECT location FROM characters WHERE campaign_id = ? AND name = 'Nate Holloway'", (denton,))["location"] == g["location"]
    do(conn, denton, {"op": "move_group", "key": "training-market", "destination": "The Molting Pigeon, Heavensworth, Over City", "reason": "home"})
    camp = one(conn, "SELECT current_location, safe_room FROM campaigns WHERE id = ?", (denton,))
    assert camp["current_location"].startswith("The Molting Pigeon") and camp["safe_room"] == 1      # known saferoom: the support group is there
    do(conn, denton, {"op": "move_group", "key": "training-market", "destination": "Rear alley behind The Molting Pigeon", "reason": "sweep"})
    assert one(conn, "SELECT safe_room FROM campaigns WHERE id = ?", (denton,))["safe_room"] == 0             # the flag never follows the party out
    do(conn, denton, {"op": "group", "key": "prison-recon", "location": "Ruined brick masonry yard, northwest of Fallen Watch"})
    scout = one(conn, "SELECT location FROM characters WHERE campaign_id = ? AND name = 'Scout'", (denton,))["location"]
    assert scout == "Ruined brick masonry yard, northwest of Fallen Watch"                                     # members move with their group
    assert one(conn, "SELECT current_location FROM campaigns WHERE id = ?", (denton,))["current_location"] == "Rear alley behind The Molting Pigeon"
    assert [c["check"] for c in summary(conn, denton)["checks"] if not c["ok"] and ("position" in c["check"] or "where" in c["check"] or "Location" in c["check"])] == []


def test_playtest_one_medium_fixes(conn, denton, client, settings):
    """Playtest 1, M1-M6 and L3, on the disposable copy."""
    from app.gm.integrity import check as integrity
    from app.ledger import sync
    from app.reconcile import ammunition_correction
    from app.rules.combat import Combat, MobAttack, StartCombat, StatBlock
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    cb = lambda dice: Combat(conn, denton, None, DiceRoller(source=scripted(dice)))
    ammunition_correction(conn, denton, sync.link(conn, denton)["root"], decided_by="owner (test)")
    cb([1]).start(StartCombat(name="test", party=["Cliff", "Marcus", "Erin", "Terri", "Vada"], setup="test",
                              foes=[StatBlock(name="Boiler", health_bar=[10] * 10, evade=25, attacks=[MobAttack(name="Lash", to_hit=10, damage="1d4")])]))
    cb([1]).declare([])
    cb([1]).resolve()
    # M2: Marcus's Near Miss stays open to Intervene after Cliff's later Attack
    assert "Near Miss" in cb([9]).attack("Marcus", "Boiler", attack="Pipe Club")["summary"]
    cb([3]).attack("Cliff", "Boiler", attack="Light Crossbow")
    iv = cb([6, 3, 3]).intervene("Vada", attacker="Marcus")
    assert iv["hit"] and "Marcus's total 29" in iv["summary"]
    # M1: a sheet that names its supply keeps it
    with pytest.raises(ChangeError, match="owner ruling"):
        cb([20]).attack("Erin", "Boiler", attack="Shortbow", stat="dexterity", ammunition="Recovered Arrow Cache")
    # M3: a Called Play's roll records the rolled total, the bonus and the effective total that set the degree
    cb([1]).end_round(); cb([1]).declare([]); cb([1]).resolve()
    cb([6, 2]).call_play("Vada", "Terri", "shoot the Boiler", target="Boiler")
    cb([10, 3, 3]).attack("Terri", "Boiler", attack="Service Pistol")
    r = one(conn, "SELECT total, target, degree, detail_json FROM dice_rolls WHERE campaign_id = ? AND reason LIKE 'Terri Campion attacks%' ORDER BY id DESC", (denton,))
    assert r["total"] == 26 and r["degree"] == "Success" and r["detail"]["called_play"]["rolled_total"] == 20 and r["detail"]["called_play"]["bonus"] == 6
    cb([1]).end("test over")
    # M4 scene aliases, M5 importance clamp
    do(conn, denton, {"op": "scene", "action": "begin", "title": "Test scene", "location": "the loft", "summary": "the market scene ended"},
       {"op": "scene", "action": "end", "summary": "it ended"},
       {"op": "remember", "subject": "Vada", "content": "Vada was brave in the loft", "importance": 4})
    assert one(conn, "SELECT importance FROM memories WHERE campaign_id = ? ORDER BY id DESC", (denton,))["importance"] == 3
    # L3: a quoted price is not a Gold transaction; a payment still is
    msg = lambda story: [f["message"] for f in integrity(conn, denton, "how much is it?", story, [], [], [], []) if "Gold" in f["message"]]
    assert msg("The vendor says it costs 70 Gold.") == [] and msg("**Buy one for Nate for 70 Gold?**") == [] and msg("You pay 70 Gold for the armor.")


def test_explicit_save_creates_a_save_point_and_a_checkpoint_leaves_level_up_work(client, conn, denton, settings):
    """Playtest 1, M6 (a narrated bookmark, no save) and M5 (unprocessed Level-up benefits)."""
    from app.rules.checks import resolve_advancement_checkpoint
    from app.rules.dice import DiceRoller
    class Save(MockProvider):
        def complete(self, system, steps, tools, *, job_label):
            if not any(s["kind"] == "tool_results" for s in steps):
                return ProviderResponse([ToolCall("save_game", {"label": "Before the prison"})], "")
            return ProviderResponse([], "Saved.")
    client.app_state.turns = TurnService(settings, Save, run_async=False)
    r = client.post(f"/api/campaigns/{denton}/turns", json={"text": "Let's save here.", "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    sp = one(conn, "SELECT label, kind FROM save_points WHERE campaign_id = ? AND label = 'Before the prison'", (denton,))
    assert sp and sp["kind"] == "manual"
    with transaction(conn):
        conn.execute("UPDATE clocks SET current = 12 WHERE campaign_id = ? AND key = 'advancement_cycles'", (denton,))
        resolve_advancement_checkpoint(conn, conn, DiceRoller(), denton, None, "test checkpoint")
    q = one(conn, "SELECT status, details_json FROM quests WHERE campaign_id = ? AND title = 'Level-up benefits: test checkpoint'", (denton,))
    assert q["status"] == "active" and q["details"]["crawlers"]


def test_erin_fires_her_arrows_and_the_training_ruling_is_found_and_written_back(conn, denton):
    from app.ledger import sync
    from app.reconcile import erin_arrows_correction, floor_2_assets_correction, training_ruling
    from app.rules.combat import Combat, StartCombat, StatBlock
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    root = sync.link(conn, denton)["root"]
    floor_2_assets_correction(conn, denton, root, decided_by="owner (test)")   # puts the 15-arrow cache in the shared stash, as live
    erin_arrows_correction(conn, denton, root, decided_by="owner (test)")
    arrows = lambda: one(conn, "SELECT i.quantity FROM items i JOIN characters c ON c.id = i.owner_character_id WHERE c.campaign_id = ? AND c.name = 'Erin' AND i.name = 'Arrow'", (denton,))["quantity"]
    assert arrows() == 15 and not one(conn, "SELECT 1 FROM items WHERE campaign_id = ? AND name = 'Recovered Arrow Cache' AND quantity > 0", (denton,))
    Combat(conn, denton, None, DiceRoller(source=scripted([1]))).start(StartCombat(name="t", party=["Erin", "Cliff"], setup="t", foes=[StatBlock(name="Rat", health_bar=[1], evade=5)]))
    Combat(conn, denton, None, DiceRoller()).declare([])
    Combat(conn, denton, None, DiceRoller(source=scripted([1]))).resolve()
    Combat(conn, denton, None, DiceRoller(source=scripted([15, 3]))).attack("Erin", "Rat", attack="Shortbow", stat="dexterity")   # her sheet's "Arrow supply"
    assert arrows() == 14
    Combat(conn, denton, None, DiceRoller()).end("done")
    training_ruling(conn, denton, decided_by="owner (test)")
    from app.gm.tools import ToolRunner
    found = ToolRunner(conn, None, DiceRoller(), denton, 0).run("search_rules", {"queries": ["paid training guild Rank cost"]})
    assert found["ok"] and "500 Gold" in json.dumps(found["result"])
    pv = sync.preview(conn, denton, fetch=False)
    assert pv["blockers"] == [] and all(c["status"] == "clean" for c in pv["changes"])
    assert any(c["summary"].startswith("Ruling: Paid training") for c in pv["changes"])
    assert any("ammunition_note" in json.dumps(c) or "Correction" in c["summary"] for c in pv["changes"] if c["file"] == "state/characters/erin.yaml")


def test_approved_trap_records_make_the_traps_deployable(conn, denton):
    from app.ledger import sync
    from app.reconcile import floor_2_assets_correction, trap_records_correction
    from app.rules import traps
    from app.rules.dice import DiceRoller
    from tests.conftest import scripted
    floor_2_assets_correction(conn, denton, sync.link(conn, denton)["root"], decided_by="owner (test)")   # the stake cache, as live
    trap_records_correction(conn, denton, decided_by="owner (test)")
    st = {t["name"]: t for t in traps.status(conn, denton)}
    for name in ("Cinch Trap", "Scatterburst Trap", "Snap-Deploy Stake Thrower", "Porcupine Pod", "Accordion Barricade", "Murder Drum Stake Battery Mk II"):
        assert st[name]["recorded"] and st[name]["condition"] == "ready", name
    stakes = lambda: one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND name = 'Recovered Trap Stake Cache'", (denton,))["quantity"]
    s0 = stakes()
    with transaction(conn):
        traps.deploy(conn, DiceRoller(), denton, None, "Snap-Deploy Stake Thrower", "the alley mouth")
        out = traps.trigger(conn, DiceRoller(source=scripted([15, 5])), denton, None, "Snap-Deploy Stake Thrower", "a Brain Boiler", target_number=16)
    assert out["hit"] and stakes() == s0 - 1 and "Damage 1d8 Piercing" in out["summary"]
    with pytest.raises(ValueError, match="already has a trap record"):
        trap_records_correction(conn, denton, decided_by="owner (test)")
