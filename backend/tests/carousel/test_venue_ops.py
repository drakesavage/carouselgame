import json

import pytest

from app.db import transaction
from app.rules.engine import ChangeError
from tests.conftest import apply

GATE, BARN, CELLAR, HIDDEN = (f"SM-T01:{n}" for n in ("n01_farm_gate", "n02_barn", "n03_cellar_door", "n04_MINI-HIDDEN-ROOM"))


def at_farm(conn, cid):
    with transaction(conn):
        conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage) VALUES (?, 'OM-001', 'visible')", (cid,))
    apply(conn, cid, {"op": "travel", "to": "CAR-086"})


def zone_of(conn, cid):
    return conn.execute("SELECT zone_id FROM campaigns WHERE id = ?", (cid,)).fetchone()[0]


def clock(conn, cid):
    return conn.execute("SELECT game_time_minutes FROM campaigns WHERE id = ?", (cid,)).fetchone()[0]


def test_move_zone_follows_links(conn, campaign_id):
    at_farm(conn, campaign_id)
    with pytest.raises(ChangeError, match="no way from farm gate to cellar door"):
        apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert zone_of(conn, campaign_id) == GATE
    r = apply(conn, campaign_id, {"op": "move_zone", "to": "barn"})   # an exact label works too
    assert r["zone_id"] == BARN and zone_of(conn, campaign_id) == BARN
    apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert zone_of(conn, campaign_id) == CELLAR
    visits = {r[0] for r in conn.execute("SELECT zone_id FROM zone_visits WHERE campaign_id = ?", (campaign_id,))}
    assert visits == {GATE, BARN, CELLAR}
    ev = conn.execute("SELECT summary, visibility FROM events WHERE campaign_id = ? AND type = 'zone_entered' ORDER BY id DESC",
                      (campaign_id,)).fetchone()
    assert tuple(ev) == ("The group moves to cellar door", "player")


def test_move_zone_refusals(conn, campaign_id):
    with pytest.raises(ChangeError, match="not in a venue"):
        apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    at_farm(conn, campaign_id)
    with pytest.raises(ChangeError, match="unknown zone"):
        apply(conn, campaign_id, {"op": "move_zone", "to": "the attic"})
    with pytest.raises(ChangeError, match="unknown zone"):
        apply(conn, campaign_id, {"op": "move_zone", "to": "SM-T02:n01_platform"})   # another venue's zone


def test_move_zone_refused_in_the_car(conn, campaign_id):
    at_farm(conn, campaign_id)
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage = 'car' WHERE id = ?", (campaign_id,))
    with pytest.raises(ChangeError, match="car park"):
        apply(conn, campaign_id, {"op": "move_zone", "to": BARN})


def test_entering_omen_zone_sights_only(conn, campaign_id):
    at_farm(conn, campaign_id)
    apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    before = clock(conn, campaign_id)
    r = apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert r["sighted"] == ["OM-005"]
    assert conn.execute("SELECT stage FROM omen_states WHERE campaign_id = ? AND omen_id = 'OM-005'", (campaign_id,)).fetchone()[0] == "visible"
    assert conn.execute("SELECT COUNT(*) FROM stories WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0
    assert clock(conn, campaign_id) == before
    ev = conn.execute("SELECT summary, payload_json FROM events WHERE campaign_id = ? AND type = 'omen_sighted' ORDER BY id DESC",
                      (campaign_id,)).fetchone()
    payload = json.loads(ev["payload_json"])
    assert payload["omen_id"] == "OM-005" and payload["zone"] == "cellar door" and payload["node"] == "CAR-086"
    assert "A cellar door breathing" in ev["summary"]


def test_reentering_sights_nothing_new(conn, campaign_id):
    at_farm(conn, campaign_id)
    for z in (BARN, CELLAR, BARN):
        apply(conn, campaign_id, {"op": "move_zone", "to": z})
    assert apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})["sighted"] == []


def test_a_block_keeps_the_group_where_it_is(conn, campaign_id):
    at_farm(conn, campaign_id)
    apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    with transaction(conn):
        conn.execute("""INSERT INTO venue_overlays (campaign_id, venue_id, kind, a, b, reason, created_minutes)
                        VALUES (?, 'SM-T01', 'block', ?, ?, 'the door has swollen shut', 0)""", (campaign_id, BARN, CELLAR))
    with pytest.raises(ChangeError, match="cellar door is blocked: the door has swollen shut"):
        apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert zone_of(conn, campaign_id) == BARN


# ---- Task 5: story overlays ----

from tests.carousel.test_story_ops import begin  # noqa: E402

ENDINGS = {e: {"predicate": f"MINI {e}"} for e in ("full_success", "costly_success", "survive_without_victory", "defeat")}


def story_at_cellar(conn, cid):
    """The group at the cellar door with OM-005's story begun on a typed turn."""
    at_farm(conn, cid)
    for z in (BARN, CELLAR):
        apply(conn, cid, {"op": "move_zone", "to": z})
    return begin(conn, cid, omen="OM-005", action="open:cellar_door", endings=ENDINGS)["story_id"]


def conclude(conn, cid):
    return apply(conn, cid, {"op": "conclude_story", "ending": "defeat", "reason": "the cellar keeps them",
                             "awards": [{"character": c, "performance": 0, "difficulty": 0} for c in ("Mara Vance", "Jonah Reyes")]})


def test_block_and_reopen_a_passage(conn, campaign_id):
    sid = story_at_cellar(conn, campaign_id)
    apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    apply(conn, campaign_id, {"op": "block_passage", "a": BARN, "b": CELLAR, "reason": "the cellar door slams and holds"})
    row = conn.execute("SELECT story_id, kind FROM venue_overlays WHERE campaign_id = ?", (campaign_id,)).fetchone()
    assert tuple(row) == (sid, "block")
    with pytest.raises(ChangeError, match="blocked: the cellar door slams and holds"):
        apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    with pytest.raises(ChangeError, match="already blocked"):
        apply(conn, campaign_id, {"op": "block_passage", "a": CELLAR, "b": BARN, "reason": "again"})
    apply(conn, campaign_id, {"op": "open_passage", "a": CELLAR, "b": BARN, "reason": "the latch gives"})
    apply(conn, campaign_id, {"op": "move_zone", "to": CELLAR})
    assert zone_of(conn, campaign_id) == CELLAR
    ev = conn.execute("SELECT summary, visibility FROM events WHERE campaign_id = ? AND type = 'passage_blocked'", (campaign_id,)).fetchone()
    assert ev["visibility"] == "player" and "barn" in ev["summary"] and "cellar door" in ev["summary"]


def test_passage_ops_need_a_real_link(conn, campaign_id):
    story_at_cellar(conn, campaign_id)
    with pytest.raises(ChangeError, match="no passage"):
        apply(conn, campaign_id, {"op": "block_passage", "a": GATE, "b": CELLAR, "reason": "x"})
    with pytest.raises(ChangeError, match="not blocked"):
        apply(conn, campaign_id, {"op": "open_passage", "a": BARN, "b": CELLAR, "reason": "x"})


def test_add_zone_is_reachable(conn, campaign_id):
    sid = story_at_cellar(conn, campaign_id)
    r = apply(conn, campaign_id, {"op": "add_zone", "label": "A second cellar", "connect_to": CELLAR, "reason": "the stairs go on"})
    assert r["zone_id"] == f"SM-T01:story-{sid}-1"
    apply(conn, campaign_id, {"op": "move_zone", "to": "A second cellar"})
    assert zone_of(conn, campaign_id) == r["zone_id"]
    ev = conn.execute("SELECT summary FROM events WHERE campaign_id = ? AND type = 'zone_appeared'", (campaign_id,)).fetchone()
    assert "A second cellar" in ev["summary"]


def test_overlays_need_an_active_story(conn, campaign_id):
    at_farm(conn, campaign_id)
    for change in ({"op": "block_passage", "a": GATE, "b": BARN, "reason": "x"},
                   {"op": "open_passage", "a": GATE, "b": BARN, "reason": "x"},
                   {"op": "add_zone", "label": "Pit", "connect_to": GATE, "reason": "x"}):
        with pytest.raises(ChangeError, match="a venue changes only inside a story"):
            apply(conn, campaign_id, change)
    assert conn.execute("SELECT COUNT(*) FROM venue_overlays").fetchone()[0] == 0


def test_lifting_overlays_returns_the_group(conn, campaign_id):
    story_at_cellar(conn, campaign_id)
    baseline = conn.execute("SELECT * FROM ref.venue_links ORDER BY a, b").fetchall()
    added = apply(conn, campaign_id, {"op": "add_zone", "label": "A second cellar", "connect_to": CELLAR, "reason": "the stairs go on"})
    apply(conn, campaign_id, {"op": "block_passage", "a": BARN, "b": CELLAR, "reason": "the door holds"})
    apply(conn, campaign_id, {"op": "move_zone", "to": added["zone_id"]})
    conclude(conn, campaign_id)
    assert zone_of(conn, campaign_id) == CELLAR
    assert conn.execute("SELECT COUNT(*) FROM venue_overlays WHERE campaign_id = ? AND lifted = 0", (campaign_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM venue_overlays WHERE campaign_id = ? AND lifted = 1", (campaign_id,)).fetchone()[0] == 2
    ev = conn.execute("SELECT summary FROM events WHERE campaign_id = ? AND type = 'zone_vanished'", (campaign_id,)).fetchone()
    assert ev["summary"] == "A second cellar is gone; the group is back in cellar door"
    assert conn.execute("SELECT * FROM ref.venue_links ORDER BY a, b").fetchall() == baseline
    apply(conn, campaign_id, {"op": "move_zone", "to": BARN})   # the block went with the story


def test_overlay_events_never_name_an_unreached_room(conn, campaign_id):
    story_at_cellar(conn, campaign_id)
    for z in (BARN, GATE):
        apply(conn, campaign_id, {"op": "move_zone", "to": z})   # the hidden room is no longer one link away
    apply(conn, campaign_id, {"op": "block_passage", "a": CELLAR, "b": HIDDEN, "reason": "rubble"})
    apply(conn, campaign_id, {"op": "open_passage", "a": CELLAR, "b": HIDDEN, "reason": "cleared"})
    apply(conn, campaign_id, {"op": "add_zone", "label": "A crawlspace", "connect_to": HIDDEN, "reason": "a hole"})
    rows = conn.execute("""SELECT summary, payload_json FROM events WHERE campaign_id = ? AND visibility = 'player'
                           AND type IN ('passage_blocked', 'passage_opened', 'zone_appeared')""", (campaign_id,)).fetchall()
    assert len(rows) == 3
    for summary, payload in rows:
        assert "MINI-HIDDEN-ROOM" not in summary and "MINI-HIDDEN-ROOM" not in payload
