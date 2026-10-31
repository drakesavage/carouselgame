"""M0 safety net: backup/verify/restore round trip on disposable data, and in-place migration of an old database copy."""
import shutil
import sqlite3
from pathlib import Path

import pytest

from app import backup
from app.db import SCHEMA_VERSION, connect, init_db


def test_backup_verify_restore_round_trip(settings, conn, campaign_id, tmp_path):
    (settings.data_dir / "portraits" / "p.png").write_bytes(b"\x89PNG fake")
    (settings.data_dir / "settings.json").write_text('{"gm_provider": "mock"}')
    (settings.data_dir / "gm_token.txt").write_text("never-copied")
    b = backup.create(settings, "test run")
    v = backup.verify(b)
    assert v["ok"] and v["tables"] > 20
    assert not (b / "gm_token.txt").exists()  # secrets stay out of backups
    # live data changes after the backup; the restored copy is the backup's state
    conn.execute("UPDATE campaigns SET game_time_minutes = game_time_minutes + 999 WHERE id = ?", (campaign_id,))
    target = backup.restore(b, tmp_path / "restored")
    r = sqlite3.connect(target / "campaign.db")
    live = conn.execute("SELECT game_time_minutes FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()[0]
    assert r.execute("SELECT game_time_minutes FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()[0] == live - 999
    assert (target / "portraits" / "p.png").read_bytes() == b"\x89PNG fake"
    with pytest.raises(RuntimeError, match="not empty"):
        backup.restore(b, target)  # never overwrites


def test_tampered_backup_fails_verification(settings, conn, campaign_id):
    b = backup.create(settings, "tamper")
    db = sqlite3.connect(b / "campaign.db")
    db.execute("DELETE FROM characters WHERE id = (SELECT MIN(id) FROM characters)")
    db.commit()
    db.close()
    v = backup.verify(b)
    assert not v["ok"] and any("changed campaign.db" in p for p in v["problems"])
    with pytest.raises(RuntimeError, match="failed verification"):
        backup.restore(b, b.parent / "nope")


OLD = Path(__file__).resolve().parents[2] / "data" / "backups" / "campaign-before-v2-2026-10-24.db"


@pytest.mark.skipif(not OLD.exists(), reason="pre-v2 backup of the real campaign not present")
def test_old_database_copy_migrates_in_place_without_losing_rows(tmp_path):
    copy = tmp_path / "old.db"
    shutil.copy2(OLD, copy)  # the original backup is never opened for writing
    before = backup._counts(copy)
    c = connect(copy)
    init_db(c)
    after = backup._counts(copy)
    assert c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
    assert all(after[t] == n for t, n in before.items())  # every pre-existing row survives
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
