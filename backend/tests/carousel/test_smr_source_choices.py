"""SMR source-data engine checks; these do not certify painted geometry."""
import pytest

from app.db import transaction
from app.rules.engine import ChangeError
from tests.conftest import apply
from tests.carousel.test_story_ops import on_turn, turn


def observe(conn, cid, oid):
    apply(conn, cid, {"op": "sight_omen", "omen": oid, "how": "GM-qualified source cue"})
    return apply(conn, cid, {"op": "examine_omen", "omen": oid})


def choose(conn, cid, oid, action, text):
    tid = turn(conn, cid)
    with transaction(conn):
        conn.execute("UPDATE turns SET player_input=? WHERE id=?", (text, tid))
    return on_turn(conn, cid, tid, {"op": "begin_story", "omen": oid, "action": action,
        "cast": [{"character": "Mara", "casting": "lead"}], "reason": "Explicit player Choice"})


@pytest.mark.parametrize("oid,wrong,right,text", [
    ("OM-019", "inspect:overstuffed_bowling_bag", "open:overstuffed_bowling_bag", "I open the overstuffed bowling bag."),
    ("OM-007", "look_around:look_back_alley", "look_backward:look_back_alley", "I look backward in the alley."),
])
def test_inspection_cannot_commit_source_choice_and_retry_cannot_duplicate(real_campaign, oid, wrong, right, text):
    conn, cid = real_campaign
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done' WHERE id=?", (cid,))
    before = tuple(conn.execute("SELECT node_id,game_time_minutes FROM campaigns WHERE id=?", (cid,)).fetchone())
    observe(conn, cid, oid)
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (cid,)).fetchone()
    assert not conn.execute("SELECT 1 FROM events WHERE campaign_id=? AND type='story_choice'", (cid,)).fetchone()
    with pytest.raises(ChangeError, match="Choice action"):
        choose(conn, cid, oid, wrong, "I inspect the cue without committing its Choice.")
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (cid,)).fetchone()
    result = choose(conn, cid, oid, right, text)
    with pytest.raises(ChangeError, match="another story is running"):
        choose(conn, cid, oid, right, text)
    assert conn.execute("SELECT COUNT(*) FROM stories WHERE campaign_id=?", (cid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM story_packets WHERE story_id=?", (result["story_id"],)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE campaign_id=? AND type='story_choice'", (cid,)).fetchone()[0] == 1
    assert tuple(conn.execute("SELECT node_id,game_time_minutes FROM campaigns WHERE id=?", (cid,)).fetchone()) == before


@pytest.mark.parametrize("first,second,action,text", [
    ("OM-019", "OM-020", "open:overstuffed_bowling_bag", "I open the bowling bag."),
    ("OM-002", "OM-003", "follow_nursery_rhyme:camp_children", "I follow the children singing the nursery rhyme."),
])
def test_related_cues_stay_distinct_without_starting_second_plot(real_campaign, first, second, action, text):
    conn, cid = real_campaign
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done' WHERE id=?", (cid,))
    observe(conn, cid, first)
    begun = choose(conn, cid, first, action, text)
    observe(conn, cid, second)
    next_action = conn.execute("SELECT choice_trigger FROM ref.omens WHERE id=?", (second,)).fetchone()[0]
    record_type = conn.execute("SELECT record_type FROM ref.omens WHERE id=?", (second,)).fetchone()[0]
    refusal = "not a Playable Omen" if record_type == "Related cue / route" else "another story is running"
    with pytest.raises(ChangeError, match=refusal):
        choose(conn, cid, second, next_action, "I explicitly commit the second cue's registered Choice.")
    assert {r[0] for r in conn.execute("SELECT omen_id FROM omen_states WHERE campaign_id=? AND stage='examined'", (cid,))} == {first, second}
    assert [tuple(r) for r in conn.execute("SELECT id,omen_id FROM stories WHERE campaign_id=?", (cid,))] == [(begun["story_id"], first)]
    if first == "OM-002":
        assert {r[0] for r in conn.execute("SELECT story_lock FROM ref.omens WHERE id IN ('OM-002','OM-003')")} == {"SAY_NO_TO_SUMMER"}


def test_non_omen_records_remain_unsightable(real_campaign):
    conn, cid = real_campaign
    ids = [r[0] for r in conn.execute("SELECT id FROM ref.omens WHERE record_type='Not an Omen'")]
    assert ids
    for oid in ids:
        with pytest.raises(ChangeError, match="not an Omen"):
            observe(conn, cid, oid)
    assert not conn.execute("SELECT 1 FROM omen_states WHERE campaign_id=?", (cid,)).fetchone()


def test_all_source_related_cues_can_be_observed_but_cannot_launch_movies(real_campaign):
    conn, cid = real_campaign
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage='done' WHERE id=?", (cid,))
    cues = list(conn.execute("SELECT id,choice_trigger FROM ref.omens WHERE record_type='Related cue / route'"))
    assert len(cues) == 4
    for oid, action in cues:
        observe(conn, cid, oid)
        with pytest.raises(ChangeError, match="not a Playable Omen"):
            choose(conn, cid, oid, action or "inspect:related_cue", "I inspect the related cue.")
    assert conn.execute("SELECT COUNT(*) FROM omen_states WHERE campaign_id=? AND stage='examined'", (cid,)).fetchone()[0] == 4
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (cid,)).fetchone()
    assert not conn.execute("SELECT 1 FROM events WHERE campaign_id=? AND type='story_choice'", (cid,)).fetchone()
