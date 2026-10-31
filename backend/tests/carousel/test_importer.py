import json

import pytest

from app.carousel import importer, refdb
from app.carousel.importer import run_import
from app.carousel.sources import SOURCE_FILES
from app.config import Settings
from app.db import connect, init_db, now_iso
from tests.carousel.conftest import write_mini_source


@pytest.fixture
def settings_mini(tmp_path):
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src"), gm_provider="mock")
    s.ensure_dirs()
    return s


@pytest.fixture
def campaign_conn(settings_mini):
    c = connect(settings_mini.campaign_db)
    init_db(c)
    c.execute("INSERT INTO campaigns (id, name, game_time_minutes, created_at, updated_at) VALUES (1, 'T', 0, ?, ?)", (now_iso(), now_iso()))
    c.execute("INSERT INTO locations (campaign_id, name, ref_id) VALUES (1, 'Town Square', 'CAR-001')")
    c.commit()
    yield c
    c.close()


def test_import_builds_reference_db(real_source, tmp_path):
    s = Settings(data_dir=tmp_path / "data", source_dir=real_source, gm_provider="mock")
    s.ensure_dirs()
    r = run_import(s)
    assert r.ok, (r.error, r.missing)
    assert r.counts["places"] == 234 and r.counts["omens"] == 111 and r.counts["npc_templates"] == 70
    assert s.reference_db.exists() and not s.reference_db.with_suffix(".db.new").exists()
    assert json.loads(r.report_path.read_text(encoding="utf-8"))["ok"] is True


def test_manifest_records_every_source(settings_mini):
    assert run_import(settings_mini).ok
    c = refdb.connect(settings_mini.reference_db)
    assert {k for (k,) in c.execute("SELECT key FROM manifest")} == set(SOURCE_FILES)
    c.close()


def test_reimport_refuses_missing_referenced_id(settings_mini, campaign_conn):
    assert run_import(settings_mini).ok
    before = settings_mini.reference_db.read_bytes()
    campaign_conn.execute("UPDATE locations SET ref_id = 'CAR-999'")
    campaign_conn.commit()
    r = run_import(settings_mini)
    assert not r.ok and r.missing == {"place": ["CAR-999"]}
    assert settings_mini.reference_db.read_bytes() == before
    assert not settings_mini.reference_db.with_suffix(".db.new").exists()


def test_reimport_accepts_alias(settings_mini, campaign_conn):
    campaign_conn.execute("UPDATE locations SET ref_id = 'CAR-017'")   # alias of CAR-016
    campaign_conn.commit()
    assert run_import(settings_mini).ok


def test_missing_npc_template_is_refused(settings_mini, campaign_conn):
    cid = campaign_conn.execute("INSERT INTO characters (campaign_id, slug, name, kind) VALUES (1, 'x', 'X', 'npc')").lastrowid
    campaign_conn.execute("INSERT INTO npc_profiles (character_id, ref_id) VALUES (?, 'CAR-NPC-404')", (cid,))
    campaign_conn.commit()
    r = run_import(settings_mini)
    assert not r.ok and r.missing == {"npc": ["CAR-NPC-404"]}


def test_import_aborts_if_source_changes_midway(settings_mini, monkeypatch):
    real = importer.IMPORTERS[-1]

    def meddling(ref, files):
        real(ref, files)
        p = settings_mini.source_dir / SOURCE_FILES["omens"]
        p.write_text(p.read_text(encoding="utf-8") + " ", encoding="utf-8")

    monkeypatch.setattr(importer, "IMPORTERS", [*importer.IMPORTERS[:-1], meddling])
    r = run_import(settings_mini)
    assert not r.ok and "omens" in r.error
    assert not settings_mini.reference_db.exists()


def test_missing_source_is_reported_not_raised(settings_mini):
    (settings_mini.source_dir / SOURCE_FILES["npcs"]).unlink()
    r = run_import(settings_mini)
    assert not r.ok and "npcs" in r.error and not settings_mini.reference_db.exists()
