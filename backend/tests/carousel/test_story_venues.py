import pytest

from app.memory.context import build_context
from app.rules.engine import ChangeError
from tests.carousel.test_story_briefing import section
from tests.carousel.test_story_ops import begin, seen
from tests.carousel.test_venue_ops import at_farm, conclude, zone_of
from tests.conftest import apply

CORN, HATCH, LADDER, ROOM = "SM-T04", "SM-T04:n01_hatch", "SM-T04:n02_ladder", "SM-T04:n03_STORYROOM-QUEBEC-16"
FARM_GATE = "SM-T01:n01_farm_gate"


def enter(conn, cid, venue=CORN):
    return apply(conn, cid, {"op": "enter_story_venue", "venue": venue, "reason": "MINI-ENTRY-REASON the hatch gives way"})


def leave(conn, cid):
    return apply(conn, cid, {"op": "leave_story_venue"})


def venue_of(conn, cid):
    return conn.execute("SELECT venue_id FROM campaigns WHERE id = ?", (cid,)).fetchone()[0]


def in_story(conn, cid):
    at_farm(conn, cid)   # OM-001 seen, the group at the farm gate
    begin(conn, cid)


def test_enter_needs_a_running_story(conn, campaign_id):
    with pytest.raises(ChangeError, match="only during a story"):
        enter(conn, campaign_id)


def test_enter_refuses_a_town_venue(conn, campaign_id):
    in_story(conn, campaign_id)
    with pytest.raises(ChangeError, match="not a story venue"):
        enter(conn, campaign_id, "SM-T01")


def test_enter_refuses_a_story_venue_unrelated_to_the_story_and_the_place(conn, campaign_id):
    in_story(conn, campaign_id)
    with pytest.raises(ChangeError, match="nothing to do with"):
        enter(conn, campaign_id, "SM-T02")   # holds no Omen of this story; its member is the old diner


def test_a_story_venue_at_the_groups_place_can_be_entered(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    apply(conn, campaign_id, {"op": "travel", "to": "CAR-017"})   # the old diner, by its alias
    enter(conn, campaign_id, "SM-T02")
    assert zone_of(conn, campaign_id) == "SM-T02:n01_platform"


def test_a_wiped_group_cannot_enter(conn, campaign_id):
    in_story(conn, campaign_id)
    conn.execute("UPDATE characters SET status = 'dead' WHERE id = (SELECT active_character_id FROM campaigns WHERE id = ?)",
                 (campaign_id,))   # the seed records no groups: the player character is the group
    with pytest.raises(ChangeError, match="nobody"):
        enter(conn, campaign_id)


def test_entering_puts_the_group_at_the_entry(client, conn, campaign_id):
    in_story(conn, campaign_id)
    enter(conn, campaign_id)
    assert (venue_of(conn, campaign_id), zone_of(conn, campaign_id)) == (CORN, HATCH)
    v = client.get(f"/api/campaigns/{campaign_id}/venue").json()
    assert v["title"] == "Under the cornfield" and v["zone"]["label"] == "hatch" and v["exit"] == "ladder"
    shown = conn.execute("SELECT summary || payload_json FROM events WHERE campaign_id = ? AND visibility = 'player' "
                         "AND type = 'story_venue_entered'", (campaign_id,)).fetchall()
    assert [r[0].startswith("The group goes into Under the cornfield.") for r in shown] == [True]
    assert not any("MINI-ENTRY-REASON" in r[0] for r in shown)


def test_travel_waits_while_move_zone_works(conn, campaign_id):
    in_story(conn, campaign_id)
    enter(conn, campaign_id)
    with pytest.raises(ChangeError, match="inside Under the cornfield: leave by its exit \(ladder\) or end the story first"):
        apply(conn, campaign_id, {"op": "travel", "to": "CAR-002"})
    apply(conn, campaign_id, {"op": "move_zone", "to": ROOM})
    assert zone_of(conn, campaign_id) == ROOM


def test_leave_only_by_the_exit_and_the_town_venue_comes_back(conn, campaign_id):
    in_story(conn, campaign_id)
    enter(conn, campaign_id)
    with pytest.raises(ChangeError, match="ladder"):
        leave(conn, campaign_id)
    for z in (ROOM, LADDER):
        apply(conn, campaign_id, {"op": "move_zone", "to": z})
    leave(conn, campaign_id)
    assert (venue_of(conn, campaign_id), zone_of(conn, campaign_id)) == ("SM-T01", FARM_GATE)


def test_the_end_of_the_story_brings_the_group_out(conn, campaign_id):
    in_story(conn, campaign_id)
    enter(conn, campaign_id)
    apply(conn, campaign_id, {"op": "move_zone", "to": ROOM})
    conclude(conn, campaign_id)
    assert (venue_of(conn, campaign_id), zone_of(conn, campaign_id)) == ("SM-T01", FARM_GATE)


def test_the_briefing_says_travel_is_closed(conn, campaign_id):
    in_story(conn, campaign_id)
    enter(conn, campaign_id)
    v = section(build_context(conn, campaign_id, "We look around.")["text"], "VENUE")
    assert "Inside Under the cornfield (story venue; travel is closed until the group leaves by ladder or the story ends)." in v
