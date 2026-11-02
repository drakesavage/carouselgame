import json

import pytest

from app.db import transaction
from app.rules.engine import ChangeError
from tests.carousel.test_story_ops import begin, events, seen
from tests.conftest import apply

AWARDS = [{"character": "Mara", "performance": 2, "difficulty": 1}, {"character": "Jonah", "performance": 0, "difficulty": 0}]


def conclude(conn, cid, ending="defeat", awards=None):
    return apply(conn, cid, {"op": "conclude_story", "ending": ending, "reason": "the farm keeps them",
                             "awards": AWARDS if awards is None else awards})


def zero(who):
    return [{"character": w, "performance": 0, "difficulty": 0} for w in who]


def char(conn, slug):
    return dict(conn.execute("SELECT * FROM characters WHERE slug = ?", (slug,)).fetchone())


@pytest.fixture
def running(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    return begin(conn, campaign_id)["story_id"]


def run_story(conn, cid, awards):
    begin(conn, cid)
    conclude(conn, cid, awards=awards)


def test_victory_needs_the_finale(conn, campaign_id, running):
    for ending in ("full_success", "costly_success"):
        with pytest.raises(ChangeError, match="Finale"):
            conclude(conn, campaign_id, ending)
    apply(conn, campaign_id, {"op": "advance_phase", "to": "Finale", "trigger": "the barn burns"})
    r = conclude(conn, campaign_id, "full_success")
    s = dict(conn.execute("SELECT * FROM stories WHERE id = ?", (running,)).fetchone())
    assert (s["status"], s["phase"], s["ending"], s["novelty"]) == ("concluded", "The End", "full_success", 2)
    assert s["ended_minutes"] == 600 and s["ending_reason"] == "the farm keeps them" and r["ending"] == "full_success"


def test_first_run_stars_make_a_stat_point(conn, campaign_id, running, pc, companion):
    conclude(conn, campaign_id)
    m, j = char(conn, "mara"), char(conn, "jonah")
    assert (m["stars"], m["pending_stat_points"]) == (0, 1)
    assert (j["stars"], j["pending_stat_points"], j["starless_streak"]) == (2, 0, 0)
    [earned] = events(conn, campaign_id, "stat_point_earned")
    assert "Mara" in earned["summary"]
    [done] = events(conn, campaign_id, "story_concluded")
    p = json.loads(done["payload_json"])
    assert p["ending"] == "defeat" and sorted(p["cast"]) == sorted([pc, companion]) and p["stars"] == {str(pc): 5, str(companion): 2}
    assert done["visibility"] == "player" and "MINI-SEALED-TRUTH" not in json.dumps(done)
    assert events(conn, campaign_id, "story_phase")[-1]["summary"].endswith("→ The End")


def test_story_tickets_recharge_and_story_modifiers_end(conn, campaign_id, running, pc):
    with transaction(conn):
        conn.execute("UPDATE character_tickets SET uses_left = 0 WHERE character_id = ? AND ticket_ref = 'hb.ticket.base.final-girl'", (pc,))
    apply(conn, campaign_id, {"op": "add_modifier", "character": "Mara", "target": "grit", "op_kind": "add", "value": 1, "source": "adrenaline", "scope": "story"})
    apply(conn, campaign_id, {"op": "add_modifier", "character": "Mara", "target": "grit", "op_kind": "add", "value": 1, "source": "a scar", "scope": "until_removed"})
    conclude(conn, campaign_id)
    assert conn.execute("SELECT uses_left FROM character_tickets WHERE character_id = ? AND ticket_ref = 'hb.ticket.base.final-girl'", (pc,)).fetchone()[0] == 1
    assert [r[0] for r in conn.execute("SELECT scope FROM stat_modifiers WHERE character_id = ?", (pc,))] == ["until_removed"]


def test_replay_novelty_is_one(conn, campaign_id, running):
    conclude(conn, campaign_id)
    begin(conn, campaign_id)
    conclude(conn, campaign_id, awards=zero(["Mara", "Jonah"]))
    assert [r[0] for r in conn.execute("SELECT novelty FROM stories ORDER BY id")] == [2, 1]
    assert char(conn, "jonah")["stars"] == 3


def test_three_starless_stories_decay_a_star(conn, campaign_id, running):
    conclude(conn, campaign_id, awards=zero(["Mara", "Jonah"]))          # novelty 2: both gain 2
    run_story(conn, campaign_id, zero(["Mara", "Jonah"]))               # novelty 1: both at 3
    assert char(conn, "mara")["stars"] == 3
    for n in (1, 2):
        run_story(conn, campaign_id, zero(["Mara", "Jonah"]))           # novelty 0: no gain
        assert (char(conn, "mara")["stars"], char(conn, "mara")["starless_streak"]) == (3, n)
    run_story(conn, campaign_id, zero(["Mara", "Jonah"]))
    assert (char(conn, "mara")["stars"], char(conn, "mara")["starless_streak"]) == (2, 0)
    assert len(events(conn, campaign_id, "stars_decayed")) == 2          # Mara and Jonah


def test_missing_pc_award_is_refused(conn, campaign_id, running):
    with pytest.raises(ChangeError, match="Jonah"):
        conclude(conn, campaign_id, awards=[AWARDS[0]])
    with pytest.raises(ChangeError):
        conclude(conn, campaign_id, awards=[{**AWARDS[0], "performance": 3}, AWARDS[1]])
    assert conn.execute("SELECT status FROM stories").fetchone()[0] == "active"


def test_npc_cast_earn_nothing(conn, campaign_id, running, npc):
    from tests.carousel.test_story_ops import ADELINE_HERE
    apply(conn, campaign_id, ADELINE_HERE)
    apply(conn, campaign_id, {"op": "cast_character", "character": "Adeline", "casting": "witness"})
    with pytest.raises(ChangeError, match="earn no stars"):
        conclude(conn, campaign_id, awards=[*AWARDS, {"character": "Adeline", "performance": 1, "difficulty": 1}])
    conclude(conn, campaign_id)
    assert char(conn, "adeline")["stars"] == 0


def test_spend_stat_point(conn, campaign_id, running):
    with pytest.raises(ChangeError, match="no stat points"):
        apply(conn, campaign_id, {"op": "spend_stat_point", "character": "Mara", "stat": "grit"})
    conclude(conn, campaign_id)
    from app.carousel.sheet import sheet
    pa = sheet(conn, char(conn, "mara")["id"])["plot_armor"]["base"]
    apply(conn, campaign_id, {"op": "spend_stat_point", "character": "Mara", "stat": "grit"})
    m = char(conn, "mara")
    assert (m["grit"], m["pending_stat_points"]) == (3, 0)
    assert sheet(conn, m["id"])["plot_armor"]["base"] == pa + 1
    assert events(conn, campaign_id, "stat_point_spent")


def test_marks_unlock_once_and_one_per_story(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "award_mark", "character": "Mara", "track": "advanced.adventurer", "feat": "kept the supplies"})
    with pytest.raises(ChangeError, match="already"):
        apply(conn, campaign_id, {"op": "award_mark", "character": "Mara", "track": "advanced.adventurer", "feat": "again"})
    with pytest.raises(ChangeError, match="advanced"):
        apply(conn, campaign_id, {"op": "award_mark", "character": "Mara", "track": "base.film-buff", "feat": "x"})
    conclude(conn, campaign_id)
    for _ in range(9):
        run_story(conn, campaign_id, zero(["Mara", "Jonah"]))
        before = (char(conn, "mara")["stars"], char(conn, "mara")["pending_stat_points"])
        apply(conn, campaign_id, {"op": "award_mark", "character": "Mara", "track": "advanced.adventurer", "feat": "led the way"})
        assert (char(conn, "mara")["stars"], char(conn, "mara")["pending_stat_points"]) == before   # marks are never stars
    row = conn.execute("SELECT marks, unlocked FROM advanced_marks").fetchone()
    assert tuple(row) == (10, 1)
    assert len(events(conn, campaign_id, "advanced_role_unlocked")) == 1
    assert conn.execute("SELECT COUNT(*) FROM character_tickets WHERE ticket_ref = 'hb.ticket.advanced.adventurer'").fetchone()[0] == 0


def test_select_aspect_needs_plot_armor_21(conn, campaign_id, pc):
    change = {"op": "select_aspect", "character": "Mara", "aspect": "aspect.final-girl.screamer", "feat": "screamed the house down"}
    with pytest.raises(ChangeError, match="21"):
        apply(conn, campaign_id, change)
    with transaction(conn):
        conn.execute("UPDATE characters SET mettle = 4, moxie = 4, hustle = 5, savvy = 4, grit = 4 WHERE id = ?", (pc,))
    apply(conn, campaign_id, change)
    assert char(conn, "mara")["aspect_id"] == "aspect.final-girl.screamer"
    with pytest.raises(ChangeError, match="already"):
        apply(conn, campaign_id, change)
    assert conn.execute("SELECT COUNT(*) FROM character_tickets WHERE character_id = ?", (pc,)).fetchone()[0] == 1   # no ticket granted


def test_aspect_must_match_the_archetype(conn, campaign_id, companion):
    with transaction(conn):
        conn.execute("UPDATE characters SET mettle = 5, moxie = 5, hustle = 5, savvy = 5, grit = 5, role_id = 'base.film-buff' WHERE id = ?", (companion,))
    with pytest.raises(ChangeError, match="Final Girl"):
        apply(conn, campaign_id, {"op": "select_aspect", "character": "Jonah", "aspect": "aspect.final-girl.screamer", "feat": "f"})


def test_confirm_spoiler_lowers_novelty(conn, campaign_id):
    from app.carousel.stories import novelty
    apply(conn, campaign_id, {"op": "confirm_spoiler", "story_lock": "MINI-OM-001", "element": "the killer", "source": "a survivor told them"})
    apply(conn, campaign_id, {"op": "confirm_spoiler", "story_lock": "MINI-OM-001", "element": "the killer", "source": "again"})   # idempotent
    assert novelty(conn, campaign_id, "MINI-OM-001") == 1
