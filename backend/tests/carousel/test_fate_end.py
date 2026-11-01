import pytest

from app.rules.engine import ChangeError
from tests.carousel.test_fate_ops import kill
from tests.carousel.test_rewards import char, conclude, running  # noqa: F401  (fixture)
from tests.carousel.test_story_ops import on_turn, turn
from tests.conftest import apply


def jonah_dies(conn, cid):
    kill(conn, cid, character="Jonah Reyes", witnesses=[], cause="CAUSE-SECRET-2")


def posters(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM missing_posters ORDER BY id")]


def test_one_survivor_restores_everyone(conn, campaign_id, running, pc):
    apply(conn, campaign_id, {"op": "add_status", "character": "Mara", "name": "Hobbled", "polarity": "debuff"})
    apply(conn, campaign_id, {"op": "add_status", "character": "Mara", "name": "Lucky", "polarity": "buff"})
    kill(conn, campaign_id)
    conclude(conn, campaign_id)
    m, j = char(conn, "mara"), char(conn, "jonah")
    assert (m["physical_condition"], m["wallpaper_status"], m["participation"], m["persistent_fate"], m["rights_basis"]) == \
        ("alive", "active", "full", "restored", None)
    assert j["persistent_fate"] == "ongoing" and j["participation"] == "full"
    assert [r[0] for r in conn.execute("SELECT name FROM status_effects WHERE character_id = ?", (pc,))] == ["Lucky"]
    assert conn.execute("SELECT COUNT(*) FROM death_events WHERE character_id = ?", (pc,)).fetchone()[0] == 1
    assert posters(conn) == []
    from tests.carousel.test_story_ops import events
    [ev] = events(conn, campaign_id, "fate_restored")
    assert "Mara Vance" in ev["summary"] and "Hobbled" in ev["payload_json"] and ev["visibility"] == "player"


def test_restored_group_stays_where_it_is(conn, campaign_id, running):
    kill(conn, campaign_id)
    conclude(conn, campaign_id)
    assert conn.execute("SELECT node_id FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()[0] == "CAR-002"


def test_whole_group_dead_makes_posters(conn, campaign_id, running, pc, companion):
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    p = posters(conn)
    assert [x["character_id"] for x in p] == [pc, companion]
    assert (p[0]["name"], p[0]["plot_armor"], p[0]["archetype"], p[0]["last_seen"], p[0]["group_no"], p[0]["story_lock"]) == \
        ("Mara Vance", 11, "Final Girl", "Town Square", 1, "MINI-OM-001")
    assert p[0]["death_event_id"] and p[0]["held_by_character_id"] is None and p[0]["posted_minutes"] == 600
    for slug in ("mara", "jonah"):
        c = char(conn, slug)
        assert (c["persistent_fate"], c["rescue_eligibility"], c["status"], c["in_party"], c["assignment"]) == \
            ("missing_postered", "possible", "missing", 0, None)
    from tests.carousel.test_story_ops import events
    assert [e["summary"] for e in events(conn, campaign_id, "poster_created")] == \
        ["A missing poster goes up: Mara Vance", "A missing poster goes up: Jonah Reyes"]
    assert "CAUSE-SECRET" not in str(events(conn, campaign_id, "poster_created"))


def test_victory_after_a_wipe_still_posters(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "advance_phase", "to": "Finale", "trigger": "the barn burns"})
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id, "full_success")
    assert len(posters(conn)) == 2 and char(conn, "mara")["persistent_fate"] == "missing_postered"


def test_uncertain_fate_blocks_the_end(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "set_fate", "character": "Jonah", "physical": "uncertain", "reason": "he went under the water"})
    with pytest.raises(ChangeError, match="decide Jonah Reyes's fate first"):
        conclude(conn, campaign_id)
    assert conn.execute("SELECT status FROM stories WHERE id = ?", (running,)).fetchone()[0] == "active"


def test_permanently_lost_is_never_restored(conn, campaign_id, running, companion):
    on_turn(conn, campaign_id, turn(conn, campaign_id), {"op": "warn_irreversible", "rule": "leave_town", "warning": "The road ends."})
    on_turn(conn, campaign_id, turn(conn, campaign_id), {"op": "permanent_loss", "character": "Jonah", "rule": "leave_town", "reason": "past the sign"})
    conclude(conn, campaign_id)
    j = char(conn, "jonah")
    assert (j["persistent_fate"], j["status"], j["participation"]) == ("permanently_lost", "lost", "none")
    assert char(conn, "mara")["persistent_fate"] == "ongoing" and posters(conn) == []


def test_lost_survivor_does_not_save_the_dead(conn, campaign_id, running, pc):
    on_turn(conn, campaign_id, turn(conn, campaign_id), {"op": "warn_irreversible", "rule": "leave_town", "warning": "The road ends."})
    on_turn(conn, campaign_id, turn(conn, campaign_id), {"op": "permanent_loss", "character": "Jonah", "rule": "leave_town", "reason": "past the sign"})
    kill(conn, campaign_id)
    conclude(conn, campaign_id)
    assert [p["character_id"] for p in posters(conn)] == [pc]           # Jonah is gone, not a survivor: Mara is postered


def test_end_events_follow_the_bible_order(conn, campaign_id, running):
    kill(conn, campaign_id)
    conclude(conn, campaign_id)
    ids = {t: conn.execute("SELECT MIN(id) FROM events WHERE type = ?", (t,)).fetchone()[0]
           for t in ("story_concluded", "fate_restored", "stat_point_earned")}
    assert ids["story_concluded"] < ids["fate_restored"] < ids["stat_point_earned"]
