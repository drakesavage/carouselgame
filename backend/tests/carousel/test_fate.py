import pytest

from app.carousel import fate
from app.db import transaction
from app.memory.snapshots import create_save_point, restore_save_point
from tests.carousel.test_story_ops import begin, seen


@pytest.mark.parametrize("phys,wall,basis,want", [
    ("alive", "active", None, "full"), ("injured", "active", None, "full"), ("dying", "active", None, "limited"),
    ("alive", "dead", None, "limited"),          # cinematic Dead on a living body (Bible §11.10 test 10)
    ("alive", "off_screen", None, "limited"), ("alive", "written_off", None, "none"),
    ("dead", "dead", None, "observer"), ("dead", "dead", "trope:3", "posthumous_limited"), ("uncertain", "unknown", None, "limited")])
def test_rights_follow_the_axes(phys, wall, basis, want):
    assert fate.rights(phys, wall, basis) == want


def test_new_characters_start_alive_and_ongoing(conn, pc):
    c = dict(conn.execute("SELECT * FROM characters WHERE id = ?", (pc,)).fetchone())
    assert (c["physical_condition"], c["wallpaper_status"], c["participation"], c["persistent_fate"], c["rescue_eligibility"], c["rights_basis"]) == \
        ("alive", "active", "full", "ongoing", "not_applicable", None)


def test_at_camp(conn, campaign_id):
    assert not fate.at_camp(conn, campaign_id)                      # the seed stands on the Town Square
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id = 'CAR-047' WHERE id = ?", (campaign_id,))
    assert fate.at_camp(conn, campaign_id)
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage = 'escort' WHERE id = ?", (campaign_id,))
    assert not fate.at_camp(conn, campaign_id)
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage = 'done' WHERE id = ?", (campaign_id,))
    assert fate.at_camp(conn, campaign_id)
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    assert not fate.at_camp(conn, campaign_id)                      # a running story is never camp
    with transaction(conn):
        conn.execute("UPDATE stories SET status = 'concluded' WHERE campaign_id = ?", (campaign_id,))
        conn.execute("UPDATE campaigns SET node_id = NULL WHERE id = ?", (campaign_id,))
    assert fate.at_camp(conn, campaign_id)                          # no map: legacy campaigns keep saving


def test_ticket_kinds_reads_trope_type_and_tags(conn):
    assert {"Rescue", "Story alteration"} <= fate.ticket_kinds(conn, "trope:2")
    assert "Death/sacrifice" in fate.ticket_kinds(conn, "trope:3")
    assert fate.ticket_kinds(conn, "hb.ticket.base.final-girl") == set() and fate.ticket_kinds(conn, "trope:404") == set()


def test_set_rights_stores_participation(conn, pc):
    with transaction(conn):
        conn.execute("UPDATE characters SET physical_condition = 'dead', wallpaper_status = 'dead' WHERE id = ?", (pc,))
        assert fate.set_rights(conn, pc) == "observer"
    assert conn.execute("SELECT participation FROM characters WHERE id = ?", (pc,)).fetchone()[0] == "observer"


def test_fate_tables_roll_back_with_save_points(conn, campaign_id, pc):
    sp = create_save_point(conn, campaign_id, "before")
    with transaction(conn):
        conn.execute("INSERT INTO missing_posters (campaign_id, character_id, group_no, name) VALUES (?,?,1,'Mara Vance')", (campaign_id, pc))
        conn.execute("INSERT INTO death_events (campaign_id, character_id, phase, cause) VALUES (?,?,'Party','x')", (campaign_id, pc))
        conn.execute("INSERT INTO danger_warnings (campaign_id, rule, warning) VALUES (?,'leave_town','the road ends')", (campaign_id,))
    restore_save_point(conn, campaign_id, sp)
    for t in ("missing_posters", "death_events", "danger_warnings"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0, t


def test_missing_and_lost_characters_leave_the_roster(conn, campaign_id, companion):
    from app.memory.views import campaign_summary
    assert companion in {c["id"] for c in campaign_summary(conn, campaign_id)["roster"]}
    for status in ("missing", "lost"):
        with transaction(conn):
            conn.execute("UPDATE characters SET status = ? WHERE id = ?", (status, companion))
        assert companion not in {c["id"] for c in campaign_summary(conn, campaign_id)["roster"]}, status
