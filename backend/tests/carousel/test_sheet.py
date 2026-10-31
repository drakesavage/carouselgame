import pytest

from app.carousel.sheet import sheet
from app.db import now_iso
from app.memory.snapshots import create_save_point, restore_save_point


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def campaign_id(conn):
    conn.execute("INSERT INTO campaigns (id, name, game_time_minutes, created_at, updated_at) VALUES (1, 'Sheet', 0, ?, ?)", (now_iso(), now_iso()))
    return 1


@pytest.fixture
def pc(conn, campaign_id):
    return conn.execute("INSERT INTO characters (campaign_id, slug, name, kind) VALUES (?, 'mara', 'Mara Vance', 'pc')", (campaign_id,)).lastrowid


def set_stats(conn, cid, **stats):
    conn.execute(f"UPDATE characters SET {', '.join(f'{k} = ?' for k in stats)} WHERE id = ?", (*stats.values(), cid))


def add_mod(conn, cid, target, op, value, source, scope):
    camp = conn.execute("SELECT campaign_id FROM characters WHERE id = ?", (cid,)).fetchone()[0]
    conn.execute("INSERT INTO stat_modifiers (campaign_id, character_id, target, op, value, source, scope) VALUES (?,?,?,?,?,?,?)",
                 (camp, cid, target, op, value, source, scope))


def test_plot_armor_layers(conn, campaign_id, pc):
    set_stats(conn, pc, mettle=1, moxie=3, hustle=1, savvy=5, grit=1)                 # Film Buff, base 11
    add_mod(conn, pc, "moxie", "add", 1, "pep talk", "scene")
    add_mod(conn, pc, "plot_armor", "multiply", 0.5, "Trope Master", "until_removed")
    s = sheet(conn, pc)
    assert s["plot_armor"] == {"base": 11, "current": 12, "effective": 6}
    assert s["stats"]["Moxie"] == 3 and s["current"]["Moxie"] == 4                      # base points untouched
    assert [m["source"] for m in s["modifiers"]] == ["pep talk", "Trope Master"]


def test_unassigned_stats_have_no_plot_armor(conn, pc):
    assert sheet(conn, pc)["plot_armor"] == {"base": None, "current": None, "effective": None}


def test_modifier_targets_are_validated(conn, pc):
    with pytest.raises(Exception):
        add_mod(conn, pc, "hp", "add", 1, "nope", "scene")


def test_restore_rolls_back_tickets_and_modifiers(conn, campaign_id, pc):
    before = count(conn, "character_tickets")
    sp = create_save_point(conn, campaign_id, "before")
    add_mod(conn, pc, "grit", "add", 2, "adrenaline", "scene")
    conn.execute("INSERT INTO character_tickets (campaign_id, character_id, ticket_ref) VALUES (?,?,?)",
                 (campaign_id, pc, "hb.ticket.starter.shared.quick-thinker"))
    restore_save_point(conn, campaign_id, sp)
    assert count(conn, "stat_modifiers") == 0 and count(conn, "character_tickets") == before
