import shutil
from pathlib import Path

import pytest

from app.db import one, rows
from app.importer.ledger import import_ledger, report_markdown
from conftest import FIXTURES

REAL_LEDGER = Path(__file__).resolve().parents[2] / "campaign_sources" / "dccrepository"


def test_fixture_import_maps_records_and_reports_conflicts(conn):
    cid, rep = import_ledger(conn, FIXTURES / "mini_ledger")
    assert cid and rep["source"]["unchanged_after_import"] is True
    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    assert (camp["current_floor"], camp["game_time_minutes"], camp["floor_deadline_minutes"]) == (3, 200, 1200)
    hero = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND slug = 'hero'", (cid,))
    assert camp["active_character_id"] == hero["id"] and hero["level"] == 7 and hero["in_party"] == 1
    assert hero["stats"]["modifiers"]["intelligence"] == 4
    eng = one(conn, "SELECT * FROM skills WHERE character_id = ? AND name = 'Engineering'", (hero["id"],))
    assert eng["rank"] == 4 and eng["marked"] == 1
    assert one(conn, "SELECT * FROM skills WHERE character_id = ? AND kind = 'spell'", (hero["id"],))["mana_cost"] == 2
    # unresolved / unquantified counts are imported as unknown, never as a number
    bolt = one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND name = 'Crossbow Bolt'", (cid,))
    assert bolt is not None and bolt["quantity"] is None
    assert one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND name = 'Assorted Scrap'", (cid,))["quantity"] is None
    assert one(conn, "SELECT quantity FROM items WHERE campaign_id = ? AND name = 'Tool Roll'", (cid,))["quantity"] is None
    assert not one(conn, "SELECT id FROM items WHERE campaign_id = ? AND name = 'Opened Box'", (cid,))
    assert one(conn, "SELECT deployed FROM items WHERE campaign_id = ? AND name = 'Tripwire Trap'", (cid,))["deployed"] == 1
    assert one(conn, "SELECT current FROM clocks WHERE campaign_id = ? AND key = 'advancement_cycles'", (cid,))["current"] == 4
    topics = {c["topic"] for c in rep["conflicts"]}
    assert {"floor clock", "gold", "advancement cycles", "roster category", "roster membership", "health above maximum"} <= topics
    assert any("Crossbow Bolt" in m["issue"] for m in rep["manual_review"])
    assert any(r["rule"] == "mending" for r in rep["rules_pending_implementation"])
    assert any(r["rule"].startswith("§13.7") for r in rep["rules_pending_implementation"])
    rules = rows(conn, "SELECT section, label, always_include FROM house_rules WHERE campaign_id = ? AND source = 'CORE_MECHANICS.md'", (cid,))
    labels_79 = {r["label"] for r in rules if r["section"] == "7.9"}
    assert labels_79 == {"BOOK", "CAMPAIGN"}  # the unnumbered flourish subsection inherits §7.9 with its own label
    assert any(r["section"] == "7" and r["always_include"] for r in rules)
    # GM-only material lands only in GM tables / GM-visibility sections
    assert one(conn, "SELECT COUNT(*) AS n FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet'", (cid,))["n"] == 1
    gm_sections = rows(conn, "SELECT visibility FROM source_sections WHERE campaign_id = ? AND document_path LIKE 'gm_state/%'", (cid,))
    assert gm_sections and all(s["visibility"] == "gm" for s in gm_sections)
    md = report_markdown(rep)
    assert "FIXTURE-GM-SECRET-7731" not in md and "FIXTURE-GM-README-SECRET-4410" not in md
    assert one(conn, "SELECT COUNT(*) AS n FROM save_points WHERE campaign_id = ? AND kind = 'import'", (cid,))["n"] == 1


def test_each_import_creates_a_new_campaign(conn):
    a, _ = import_ledger(conn, FIXTURES / "mini_ledger")
    b, _ = import_ledger(conn, FIXTURES / "mini_ledger")
    assert a != b


def test_bootstrap_ledger_imports_nothing(conn, tmp_path):
    root = tmp_path / "empty_ledger"
    shutil.copytree(FIXTURES / "mini_ledger", root)
    text = (root / "state" / "campaign.yaml").read_text(encoding="utf-8").replace("lifecycle: active", "lifecycle: bootstrap_pending")
    (root / "state" / "campaign.yaml").write_text(text, encoding="utf-8")
    cid, rep = import_ledger(conn, root)
    assert cid is None and "Nothing imported" in " ".join(rep["notes"])
    assert one(conn, "SELECT COUNT(*) AS n FROM campaigns")["n"] == 0


def test_not_a_ledger(conn, tmp_path):
    cid, rep = import_ledger(conn, tmp_path)
    assert cid is None


@pytest.mark.skipif(not (REAL_LEDGER / "state" / "campaign.yaml").exists(), reason="real campaign ledger not present")
def test_real_ledger_import_validation(conn):
    cid, rep = import_ledger(conn, REAL_LEDGER)
    assert rep["source"]["unchanged_after_import"] is True
    cliff = one(conn, "SELECT * FROM characters WHERE campaign_id = ? AND slug = 'cliff'", (cid,))
    assert cliff["name"] == "Cliff Atkinson" and cliff["level"] is not None and cliff["hp_max"] is not None
    camp = one(conn, "SELECT * FROM campaigns WHERE id = ?", (cid,))
    assert camp["active_character_id"] == cliff["id"]
    assert one(conn, "SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ? AND in_party = 1", (cid,))["n"] >= 1
    assert one(conn, "SELECT COUNT(*) AS n FROM gm_secrets WHERE campaign_id = ?", (cid,))["n"] >= 1
