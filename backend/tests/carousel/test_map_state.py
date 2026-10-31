from app.carousel.importer import run_import
from app.db import connect, init_db, now_iso, transaction
from app.memory.snapshots import create_save_point, restore_save_point


def cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_schema_has_map_state(conn):
    assert {"node_id", "travel_seq"} <= cols(conn, "campaigns")
    assert "node_id" in cols(conn, "party_groups")
    assert {"campaign_id", "place_id", "how", "known_at_minutes"} <= cols(conn, "known_places")
    assert {"campaign_id", "flag", "set_at_minutes", "source"} <= cols(conn, "campaign_flags")
    assert {"campaign_id", "omen_id", "stage", "seen_at_node", "seen_at_minutes"} <= cols(conn, "omen_states")


def test_seed_starts_on_the_square_knowing_three_places(conn, campaign_id):
    assert tuple(conn.execute("SELECT node_id, travel_seq FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()) == ("CAR-002", 0)
    known = {r[0] for r in conn.execute("SELECT place_id FROM known_places WHERE campaign_id = ?", (campaign_id,))}
    assert known == {"CAR-047", "CAR-002", "CAR-006"}


def test_map_state_restores_with_a_save(conn, campaign_id):
    with transaction(conn):
        sp = create_save_point(conn, campaign_id, "before")
        conn.execute("UPDATE campaigns SET node_id = 'CAR-086', travel_seq = 5 WHERE id = ?", (campaign_id,))
        conn.execute("INSERT INTO known_places (campaign_id, place_id, how, known_at_minutes) VALUES (?, 'CAR-086', 'visited', 700)", (campaign_id,))
        conn.execute("INSERT INTO campaign_flags (campaign_id, flag, set_at_minutes, source) VALUES (?, 'akers_story', 700, 'test')", (campaign_id,))
        conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage, seen_at_node, seen_at_minutes) VALUES (?, 'OM-001', 'visible', 'CAR-086', 700)",
                     (campaign_id,))
    restore_save_point(conn, campaign_id, sp)
    assert tuple(conn.execute("SELECT node_id, travel_seq FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()) == ("CAR-002", 0)
    assert conn.execute("SELECT 1 FROM known_places WHERE campaign_id = ? AND place_id = 'CAR-086'", (campaign_id,)).fetchone() is None
    assert conn.execute("SELECT COUNT(*) FROM campaign_flags WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM omen_states WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM known_places WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 3


def test_reimport_protects_map_state(tmp_path):
    from app.config import Settings
    from tests.carousel.conftest import write_mini_source
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src"), gm_provider="mock")
    s.ensure_dirs()
    c = connect(s.campaign_db)
    init_db(c)
    c.execute("INSERT INTO campaigns (id, name, game_time_minutes, node_id, created_at, updated_at) VALUES (1, 'T', 0, 'J-404', ?, ?)",
              (now_iso(), now_iso()))
    c.execute("INSERT INTO party_groups (campaign_id, key, label, node_id) VALUES (1, 'main', 'Main', 'J-1')")
    c.execute("INSERT INTO known_places (campaign_id, place_id, how) VALUES (1, 'CAR-404', 'visited')")
    c.execute("INSERT INTO known_places (campaign_id, place_id, how) VALUES (1, 'CAR-017', 'visited')")   # an alias resolves
    c.execute("INSERT INTO omen_states (campaign_id, omen_id, stage) VALUES (1, 'OM-001', 'visible')")
    c.commit()
    c.close()
    r = run_import(s)
    assert not r.ok and r.missing == {"node": ["J-404"], "known_place": ["CAR-404"]}
