"""Plan 7 Task 4: residents join the group, and stories, only when met, present and willing."""
import pytest

from app.campaigns import create
from app.db import transaction
from app.rules.engine import ChangeError
from tests.carousel.test_around import stand
from tests.carousel.test_fate_end import jonah_dies, posters
from tests.carousel.test_fate_ops import kill
from tests.carousel.test_rewards import conclude
from tests.carousel.test_story_ops import begin, seen
from tests.conftest import apply


@pytest.fixture
def lodge(conn):
    cid = create(conn, name="Recruits", character="Mara", opening=False, location="Dyer's Lodge")
    stand(conn, cid, "CAR-047")
    return cid


def row(conn, cid, name):
    return dict(conn.execute("""SELECT c.*, p.attitude, p.met FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                                WHERE c.campaign_id = ? AND c.name = ?""", (cid, name)).fetchone())


def recruit(conn, cid, who="Valerie Choi"):
    return apply(conn, cid, {"op": "recruit_resident", "resident": who, "reason": "she knows the trails"})


def test_recruit_needs_met_present_and_willing(conn, lodge):
    with pytest.raises(ChangeError, match="meet them first: introduce_resident"):
        recruit(conn, lodge)
    apply(conn, lodge, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    stand(conn, lodge, "CAR-002")
    with pytest.raises(ChangeError, match="Valerie Choi is at Dyer's Lodge, not here"):
        recruit(conn, lodge)
    stand(conn, lodge, "CAR-047")
    apply(conn, lodge, {"op": "npc_attitude", "npc": "Valerie Choi", "delta": -1})
    with pytest.raises(ChangeError, match="won't come: attitude -1"):
        recruit(conn, lodge)
    apply(conn, lodge, {"op": "npc_attitude", "npc": "Valerie Choi", "delta": 1})
    recruit(conn, lodge)
    assert row(conn, lodge, "Valerie Choi")["in_party"] == 1
    ev = conn.execute("SELECT summary, visibility FROM events WHERE campaign_id = ? AND type = 'resident_recruited'", (lodge,)).fetchone()
    assert tuple(ev) == ("Valerie Choi joins the group", "player")
    with pytest.raises(ChangeError, match="already with the group"):
        recruit(conn, lodge)


def test_recruit_refuses_the_gone(conn, lodge):
    apply(conn, lodge, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    with transaction(conn):
        conn.execute("UPDATE characters SET status = 'missing' WHERE name = 'Valerie Choi' AND campaign_id = ?", (lodge,))
    with pytest.raises(ChangeError, match="gone"):
        recruit(conn, lodge)


def test_recruit_then_cast_into_story(conn, campaign_id):
    """The seeded Adeline (met) is at the Town Square on a note, joins, is cast mid-story and dies with the group."""
    apply(conn, campaign_id, {"op": "resident_whereabouts", "resident": "Adeline Winter", "place": "CAR-002", "until_minutes": 900,
                              "reason": "meeting the group"})
    recruit(conn, campaign_id, "Adeline Winter")
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    apply(conn, campaign_id, {"op": "cast_character", "character": "Adeline Winter", "casting": "support"})
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    kill(conn, campaign_id, character="Adeline Winter", witnesses=[], cause="CAUSE-SECRET-3")
    conclude(conn, campaign_id)
    assert sorted(p["name"] for p in posters(conn)) == ["Adeline Winter", "Jonah Reyes", "Mara Vance"]


def test_cast_refuses_an_absent_resident(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    apply(conn, campaign_id, {"op": "introduce_resident", "resident": "CAR-NPC-003"})   # met, but at home at the Lodge
    with pytest.raises(ChangeError, match="Valerie Choi is at Dyer's Lodge, not here"):
        begin(conn, campaign_id, cast=[{"character": "Mara Vance", "casting": "lead"}, {"character": "Valerie Choi", "casting": "support"}])
    begin(conn, campaign_id)
    with pytest.raises(ChangeError, match="not here"):
        apply(conn, campaign_id, {"op": "cast_character", "character": "Valerie Choi", "casting": "witness"})
    with pytest.raises(ChangeError, match="meet them first"):
        with transaction(conn):
            conn.execute("UPDATE npc_profiles SET met = 0 WHERE ref_id = 'CAR-NPC-003'")
        apply(conn, campaign_id, {"op": "cast_character", "character": "Valerie Choi", "casting": "witness"})
    apply(conn, campaign_id, {"op": "cast_character", "character": "Jonah Reyes", "casting": "lead"})   # non-residents unchanged


def test_dismiss_sends_them_home_until_morning(conn, lodge):
    from app.carousel import residents
    apply(conn, lodge, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    recruit(conn, lodge)
    stand(conn, lodge, "CAR-002", minutes=13 * 60)   # 21:00 on day 1
    v = row(conn, lodge, "Valerie Choi")["id"]
    apply(conn, lodge, {"op": "dismiss_resident", "resident": "Valerie Choi", "reason": "someone has to warn the camp"})
    assert row(conn, lodge, "Valerie Choi")["in_party"] == 0
    note = dict(conn.execute("SELECT * FROM resident_whereabouts WHERE character_id = ?", (v,)).fetchone())
    assert (note["place_id"], note["until_minutes"]) == ("CAR-047", 22 * 60)   # the next 06:00
    assert residents.where(conn, lodge, v)["why"] == "note"
    ev = conn.execute("SELECT summary FROM events WHERE campaign_id = ? AND type = 'resident_dismissed'", (lodge,)).fetchone()[0]
    assert ev == "Valerie Choi heads back to camp"
    with pytest.raises(ChangeError, match="isn't with the group"):
        apply(conn, lodge, {"op": "dismiss_resident", "resident": "Valerie Choi", "reason": "again"})


def test_dismiss_to_another_place(conn, lodge):
    apply(conn, lodge, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    recruit(conn, lodge)
    apply(conn, lodge, {"op": "dismiss_resident", "resident": "CAR-NPC-003", "where": "CAR-006", "reason": "minding the Diner"})
    note = conn.execute("SELECT place_id FROM resident_whereabouts WHERE campaign_id = ?", (lodge,)).fetchone()[0]
    assert note == "CAR-006"
    assert conn.execute("SELECT summary FROM events WHERE type = 'resident_dismissed'").fetchone()[0] == "Valerie Choi heads to Diner"
