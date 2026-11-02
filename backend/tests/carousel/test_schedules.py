"""Plan 7 Task 2: the game clock and the residents' daily schedules."""
import pytest

from app.campaigns import create
from app.carousel import clock, residents
from app.db import transaction
from app.rules.engine import ChangeError
from tests.conftest import apply


@pytest.mark.parametrize("minutes,slot", [(0, "morning"), (4 * 60, "afternoon"), (14 * 60, "night"), (22 * 60, "morning"),
                                          (10 * 60 - 1, "afternoon"), (10 * 60, "evening"), (14 * 60 - 1, "evening")])
def test_slots(minutes, slot):
    assert clock.slot(minutes) == slot    # 08:00 + minutes; 22:00 is night, 06:00 morning


def test_hour_and_day():
    assert (clock.hour(0), clock.day(0)) == (8, 1)
    assert (clock.hour(16 * 60 - 1), clock.day(16 * 60 - 1)) == (23, 1)
    assert (clock.hour(16 * 60), clock.day(16 * 60)) == (0, 2)


@pytest.fixture
def cid(conn):
    return create(conn, name="Schedules", character="Mara", opening=False, location="Town Square")


def at(conn, cid, minutes, node="CAR-002"):
    with transaction(conn):
        conn.execute("UPDATE campaigns SET game_time_minutes = ?, node_id = ? WHERE id = ?", (minutes, node, cid))


def rid(conn, cid, ref):
    return conn.execute("SELECT character_id FROM npc_profiles p JOIN characters c ON c.id = p.character_id WHERE c.campaign_id = ? AND ref_id = ?",
                        (cid, ref)).fetchone()[0]


def test_where_follows_overrides_in_order(conn, cid):
    v = rid(conn, cid, "CAR-NPC-003")
    at(conn, cid, 60)   # 09:00: morning, at home
    assert residents.where(conn, cid, v) == {"place_id": "CAR-047", "zone_id": None, "why": "home"}
    apply(conn, cid, {"op": "resident_whereabouts", "resident": "CAR-NPC-003", "place": "CAR-006", "until_minutes": 600, "reason": "covering the counter"})
    assert residents.where(conn, cid, v)["why"] == "note"
    with transaction(conn):
        conn.execute("INSERT INTO party_groups (campaign_id, key, label, node_id) VALUES (?, 'scouts', 'Scouts', 'CAR-086')", (cid,))
        conn.execute("UPDATE characters SET assignment = 'scouts' WHERE id = ?", (v,))
    assert residents.where(conn, cid, v) == {"place_id": "CAR-086", "zone_id": None, "why": "group"}
    with transaction(conn):
        story = conn.execute("""INSERT INTO stories (campaign_id, omen_id, title, story_lock, choice_action, phase, status)
                                VALUES (?, 'OM-001', 't', 'L', 'c', 'Party', 'active')""", (cid,)).lastrowid
        conn.execute("INSERT INTO story_cast (story_id, character_id, casting) VALUES (?,?, 'support')", (story, v))
    assert residents.where(conn, cid, v) == {"place_id": "CAR-002", "zone_id": None, "why": "story"}
    with transaction(conn):
        conn.execute("UPDATE characters SET in_party = 1 WHERE id = ?", (v,))
    assert residents.where(conn, cid, v)["why"] == "party"
    with transaction(conn):
        conn.execute("UPDATE characters SET status = 'dead' WHERE id = ?", (v,))
    assert residents.where(conn, cid, v) is None


def test_party_follows_the_pcs_group(conn, cid):
    v = rid(conn, cid, "CAR-NPC-003")
    with transaction(conn):
        conn.execute("INSERT INTO party_groups (campaign_id, key, label, node_id, zone_id) VALUES (?, 'main', 'Main', 'CAR-086', 'SM-T01:n02_barn')", (cid,))
        conn.execute("UPDATE characters SET assignment = 'main' WHERE id = (SELECT active_character_id FROM campaigns WHERE id = ?)", (cid,))
        conn.execute("UPDATE characters SET in_party = 1 WHERE id = ?", (v,))
    assert residents.where(conn, cid, v) == {"place_id": "CAR-086", "zone_id": "SM-T01:n02_barn", "why": "party"}


def test_afternoon_hangout_is_stable_within_a_day(real_campaign):
    conn, cid = real_campaign
    stats = {}
    for ref in [f"CAR-NPC-{n:03d}" for n in range(44, 52)]:   # the Atlas Table: hangouts Town Square, Diner
        v = rid(conn, cid, ref)
        at(conn, cid, 5 * 60, "CAR-082")        # day 1, 13:00
        a = residents.where(conn, cid, v)
        at(conn, cid, 9 * 60, "CAR-082")        # day 1, 17:00
        b = residents.where(conn, cid, v)
        assert a == b and a["why"] == "hangout" and a["place_id"] in ("CAR-002", "CAR-006")
        stats[ref] = []
        for d in range(6):                      # 13:00 on days 1-6
            at(conn, cid, d * 1440 + 5 * 60, "CAR-082")
            stats[ref].append(residents.where(conn, cid, v)["place_id"])
        at(conn, cid, 0, "CAR-082")
        assert residents.where(conn, cid, v)["why"] == "home"
    assert any(len(set(days)) > 1 for days in stats.values())   # some resident's afternoon changes between days


def test_note_expires(conn, cid):
    v = rid(conn, cid, "CAR-NPC-003")
    at(conn, cid, 60)
    apply(conn, cid, {"op": "resident_whereabouts", "resident": "Valerie Choi", "place": "CAR-006", "until_minutes": 120, "reason": "a late breakfast"})
    assert residents.where(conn, cid, v) == {"place_id": "CAR-006", "zone_id": None, "why": "note"}
    at(conn, cid, 120)
    assert residents.where(conn, cid, v)["why"] == "home"
    gm = conn.execute("SELECT visibility FROM events WHERE campaign_id = ? AND type = 'resident_whereabouts'", (cid,)).fetchone()[0]
    assert gm == "gm"


def test_whereabouts_refusals(conn, cid):
    at(conn, cid, 60)
    for change, msg in [({"resident": "Mara", "place": "CAR-006"}, "not a resident"),
                        ({"resident": "CAR-NPC-003", "place": "CAR-999"}, "no place"),
                        ({"resident": "CAR-NPC-003", "place": "CAR-006", "zone": "SM-T01:n02_barn"}, "not at"),
                        ({"resident": "CAR-NPC-003", "place": "CAR-006", "until_minutes": 30}, "already past")]:
        with pytest.raises(ChangeError, match=msg):
            apply(conn, cid, {"op": "resident_whereabouts", "until_minutes": 600, "reason": "x", **change})


def test_real_data_every_resident_has_a_home(real_campaign):
    conn, cid = real_campaign
    table = residents.hangout_table(conn)
    assert table["report"] == []
    homes = {t["id"]: residents.home(conn, t) for t in [dict(r) for r in conn.execute("SELECT id, usual_location, player_json FROM ref.npc_templates")]}
    assert len(homes) == 70 and all(h["place"] in ("CAR-046", "CAR-047") for h in homes.values())
    fallback = [i for i, h in homes.items() if h["why"] == "default_home"]
    assert len(fallback) == 8, fallback   # dock, paths, firepits, garden, balcony, second floor
    adeline = homes["CAR-NPC-001"]
    assert (adeline["place"], adeline["zone"]) == ("CAR-047", "SM-028:n02_shared_living_common_ro")


def test_bowlers_spend_afternoons_at_the_bowling_alley(real_campaign):
    conn, cid = real_campaign
    tpl = dict(conn.execute("SELECT id, usual_location, player_json FROM ref.npc_templates WHERE id = 'CAR-NPC-012'").fetchone())
    assert residents.hangouts(conn, tpl) == [{"place": "AUD-LOC-BOWLING-ALLEY", "zone": None}]
    cove = dict(conn.execute("SELECT id, usual_location, player_json FROM ref.npc_templates WHERE id = 'CAR-NPC-013'").fetchone())
    assert [h["place"] for h in residents.hangouts(conn, cove)] == ["CAR-179", "CAR-006"]


def test_missing_places_are_dropped_and_reported(conn, client):
    """The mini reference data has no bowling alley, no Camp Dyer and no Lodge zones: those entries drop and are reported."""
    table = residents.hangout_table(conn)
    assert any("bowling alley" in line.lower() for line in table["report"])
    assert any("The Bowlers" in line for line in table["report"])
    assert table["default_home"] == {"place": "CAR-047", "zone": None}
    status = client.get("/api/reference/status").json()
    assert status["hangouts"] == table["report"]
