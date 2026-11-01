from app.campaigns import create
from app.carousel import opening
from app.carousel.importer import run_import
from app.db import connect, init_db, now_iso, transaction
from app.memory.snapshots import create_save_point, restore_save_point


def known(conn, cid):
    return {r[0] for r in conn.execute("SELECT place_id FROM known_places WHERE campaign_id = ?", (cid,))}


def test_new_campaign_opens_in_the_car(conn):
    cid = create(conn, name="Opening", character="Mara", location="Town Square")
    assert opening.stage(conn, cid) == "car"
    assert conn.execute("SELECT node_id FROM campaigns WHERE id = ?", (cid,)).fetchone()[0] == opening.ARRIVAL_LOT == "CAR-082"
    assert known(conn, cid) == {"CAR-082"}


def test_without_an_opening_the_group_starts_where_it_is_told(conn):
    cid = create(conn, name="No opening", character="Mara", location="Town Square", opening=False)
    assert opening.stage(conn, cid) is None
    assert conn.execute("SELECT node_id FROM campaigns WHERE id = ?", (cid,)).fetchone()[0] == "CAR-002"
    assert known(conn, cid) == {"CAR-002", "CAR-047"}


def test_restore_rolls_back_opening(conn):
    cid = create(conn, name="Opening", character="Mara")
    with transaction(conn):
        sp = create_save_point(conn, cid, "in the car")
        conn.execute("UPDATE campaigns SET opening_stage = 'silas', lure_belief = 'A postcard from my sister' WHERE id = ?", (cid,))
    restore_save_point(conn, cid, sp)
    assert conn.execute("SELECT opening_stage, lure_belief FROM campaigns WHERE id = ?", (cid,)).fetchone()[:] == ("car", None)


def test_reimport_protects_the_lure_resident(tmp_path):
    from app.config import Settings
    from tests.carousel.conftest import write_mini_source
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src"), gm_provider="mock")
    s.ensure_dirs()
    c = connect(s.campaign_db)
    init_db(c)
    c.execute("INSERT INTO campaigns (id, name, game_time_minutes, created_at, updated_at, lure_resident) VALUES (1, 'T', 0, ?, ?, 'CAR-NPC-404')",
              (now_iso(), now_iso()))
    c.commit()
    c.close()
    r = run_import(s)
    assert not r.ok and r.missing == {"lure_resident": ["CAR-NPC-404"]}
