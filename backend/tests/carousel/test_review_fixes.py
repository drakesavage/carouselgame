"""Plan 2 final-review findings, each pinned by a test."""
import sqlite3

import pytest

from app.carousel import importer
from app.carousel.importer import run_import
from app.config import Settings
from app.rules.engine import ChangeError
from tests.carousel.conftest import write_mini_source
from tests.conftest import apply


def test_stat_multipliers_are_refused(conn, campaign_id):
    with pytest.raises(ChangeError, match="only Plot Armor"):
        apply(conn, campaign_id, {"op": "add_modifier", "character": "Mara Vance", "target": "grit", "op_kind": "multiply",
                                  "value": 0.5, "source": "terror", "scope": "scene"})


def test_hidden_modifier_source_never_reaches_players(client, conn, campaign_id, npc):
    apply(conn, campaign_id, {"op": "add_modifier", "character": "Adeline Winter", "target": "plot_armor", "op_kind": "add",
                              "value": 3, "source": "HIDDENTROPE-GOLF-7", "scope": "scene", "hidden": True})
    for url in (f"/api/campaigns/{campaign_id}", f"/api/campaigns/{campaign_id}/characters", f"/api/campaigns/{campaign_id}/characters/{npc}",
                f"/api/campaigns/{campaign_id}/characters/{npc}/dossier", f"/api/campaigns/{campaign_id}/timeline",
                f"/api/campaigns/{campaign_id}/chronology"):
        assert "HIDDENTROPE" not in client.get(url).text, url
    from app.memory.context import build_context
    head, _, gm = build_context(conn, campaign_id, "Adeline, a word?")["text"].partition("## GM-ONLY SECRETS")
    assert "HIDDENTROPE-GOLF-7" in gm


def test_rulings_on_hidden_tropes_stay_gm_only_until_revealed(client, conn, campaign_id):
    from app.db import transaction
    from app.gm.tools import ToolRunner
    with transaction(conn):
        r = ToolRunner(conn, None, campaign_id, 1)
        assert r.run("record_ruling", {"topic": "How Adeline's trope works", "ruling": "It finds anyone hiding indoors.",
                                       "ticket": "trope:1"})["ok"]
    assert not any("Adeline's trope" in c for c in r.effects.changes)
    assert "Adeline's trope" not in client.get(f"/api/campaigns/{campaign_id}/rulings").text
    apply(conn, campaign_id, {"op": "reveal_ticket", "character": "Adeline Winter", "ticket": "trope:1", "how": "Trope Master"})
    assert "Adeline's trope" in client.get(f"/api/campaigns/{campaign_id}/rulings").text


def test_import_works_against_a_campaign_db_from_before_tickets(tmp_path):
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src"), gm_provider="mock")
    s.ensure_dirs()
    old = sqlite3.connect(s.campaign_db)   # Plan 1 schema: places and residents, no ticket table yet
    old.executescript("CREATE TABLE locations (id INTEGER PRIMARY KEY, ref_id TEXT); CREATE TABLE npc_profiles (character_id INTEGER, ref_id TEXT);"
                      "CREATE TABLE save_points (id INTEGER PRIMARY KEY, snapshot_json TEXT);")
    old.close()
    r = run_import(s)
    assert r.ok, r.error


def test_locked_swap_says_what_to_do(tmp_path, monkeypatch):
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src"), gm_provider="mock")
    s.ensure_dirs()

    def locked(src, dst):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(importer.os, "replace", locked)
    monkeypatch.setattr(importer, "SWAP_WAIT_S", 0)
    r = run_import(s)
    assert not r.ok and "stop the server or wait for the GM turn to finish" in r.error
