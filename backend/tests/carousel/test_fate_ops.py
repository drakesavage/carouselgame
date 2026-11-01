import json

import pytest

from app.db import transaction
from app.rules.engine import ChangeError
from tests.carousel.test_rewards import char, running  # noqa: F401  (fixture)
from tests.carousel.test_story_ops import events, on_turn, turn
from tests.conftest import apply

DIES = {"op": "set_fate", "character": "Mara Vance", "physical": "dead", "wallpaper": "dead", "cause": "CAUSE-SECRET-LIMA-7",
        "last_action": "held the barn door", "earned_effects": ["pursuer delayed 3 beats"], "witnesses": ["Jonah Reyes"], "reason": "the scarecrow"}


def deaths(conn, who):
    return conn.execute("SELECT COUNT(*) FROM death_events WHERE character_id = ?", (who,)).fetchone()[0]


def kill(conn, cid, **over):
    return on_turn(conn, cid, turn(conn, cid), {**DIES, **over})


def give(conn, cid, who, ref, uses=1):
    with transaction(conn):
        conn.execute("INSERT INTO character_tickets (campaign_id, character_id, ticket_ref, equipped, uses_left, uses_max, source) VALUES (?,?,?,1,?,?,'test')",
                     (cid, who, ref, uses, uses))


def test_death_writes_one_record_and_hides_the_cause(conn, campaign_id, running, pc, companion):
    kill(conn, campaign_id)
    d = dict(conn.execute("SELECT * FROM death_events WHERE character_id = ?", (pc,)).fetchone())
    assert d["phase"] == "Party" and d["node_id"] == "CAR-002" and d["story_id"] == running and d["turn_id"]
    assert json.loads(d["items_json"]) == ["Flashlight", "Healing Potion"] and json.loads(d["witnesses_json"]) == [companion]
    assert json.loads(d["earned_effects_json"]) == ["pursuer delayed 3 beats"] and d["last_action"] == "held the barn door"
    [died] = events(conn, campaign_id, "character_died")
    assert died["visibility"] == "player" and died["summary"] == "Mara Vance dies (Party)" and "CAUSE-SECRET" not in json.dumps(died)
    [rec] = events(conn, campaign_id, "death_record")
    assert rec["visibility"] == "gm" and "CAUSE-SECRET-LIMA-7" in rec["payload_json"] and "pursuer delayed" in rec["payload_json"]
    m = char(conn, "mara")
    assert (m["physical_condition"], m["wallpaper_status"], m["participation"]) == ("dead", "dead", "observer")


def test_a_death_needs_a_cause_and_a_last_action(conn, campaign_id, running, pc):
    for missing in ("cause", "last_action"):
        with pytest.raises(ChangeError, match=missing):
            kill(conn, campaign_id, **{missing: None})
    assert deaths(conn, pc) == 0


def test_dying_twice_is_refused_and_writes_nothing(conn, campaign_id, running, pc):
    kill(conn, campaign_id)
    with pytest.raises(ChangeError, match="already dead"):
        kill(conn, campaign_id)
    assert deaths(conn, pc) == 1 and len(events(conn, campaign_id, "character_died")) == 1


def test_no_resurrection_mid_story(conn, campaign_id, running, pc):
    kill(conn, campaign_id)
    with pytest.raises(ChangeError, match="no resurrection"):
        apply(conn, campaign_id, {"op": "set_fate", "character": "Mara", "physical": "alive", "reason": "she gets up"})
    assert char(conn, "mara")["physical_condition"] == "dead"


def test_cinematic_dead_is_not_a_death(conn, campaign_id, running, pc):
    apply(conn, campaign_id, {"op": "set_fate", "character": "Mara", "wallpaper": "dead", "reason": "the camera thinks she's gone"})
    assert deaths(conn, pc) == 0 and not events(conn, campaign_id, "character_died")
    [ev] = events(conn, campaign_id, "wallpaper_changed")
    assert ev["visibility"] == "player"
    m = char(conn, "mara")
    assert (m["physical_condition"], m["wallpaper_status"], m["participation"]) == ("alive", "dead", "limited")


def test_injury_keeps_full_rights(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "set_fate", "character": "Jonah", "physical": "injured", "reason": "a nail through the boot"})
    j = char(conn, "jonah")
    assert (j["physical_condition"], j["participation"]) == ("injured", "full")


def test_fate_changes_need_a_running_story_and_a_cast_member(conn, campaign_id):
    with pytest.raises(ChangeError, match="no story is active"):
        kill(conn, campaign_id)
    from tests.carousel.test_story_ops import begin, seen
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    with pytest.raises(ChangeError, match="not in the cast"):
        kill(conn, campaign_id, character="Adeline Winter")
    with pytest.raises(ChangeError, match="physical or wallpaper"):
        apply(conn, campaign_id, {"op": "set_fate", "character": "Mara", "reason": "nothing"})


def test_warning_is_player_visible(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "warn_irreversible", "rule": "leave_town", "warning": "Past the sign, the road doesn't come back."})
    w = dict(conn.execute("SELECT * FROM danger_warnings").fetchone())
    assert (w["rule"], w["story_id"], w["character_id"]) == ("leave_town", running, None)
    [ev] = events(conn, campaign_id, "danger_warning")
    assert ev["visibility"] == "player" and ev["summary"] == "Warning: Past the sign, the road doesn't come back."


def test_permanent_loss_needs_an_earlier_warning(conn, campaign_id, companion):
    loss = {"op": "permanent_loss", "character": "Jonah", "rule": "leave_town", "reason": "he walks past the sign"}
    with pytest.raises(ChangeError, match="warning"):
        on_turn(conn, campaign_id, turn(conn, campaign_id), loss)
    t = turn(conn, campaign_id)
    on_turn(conn, campaign_id, t, {"op": "warn_irreversible", "rule": "leave_town", "warning": "The road doesn't come back.", "character": "Jonah"})
    with pytest.raises(ChangeError, match="earlier turn"):
        on_turn(conn, campaign_id, t, loss)                                 # the same turn as the warning
    with pytest.raises(ChangeError, match="turn"):
        apply(conn, campaign_id, loss)                                      # no turn at all
    on_turn(conn, campaign_id, turn(conn, campaign_id), loss)               # outside a story is fine
    j = char(conn, "jonah")
    assert (j["persistent_fate"], j["rescue_eligibility"], j["participation"], j["status"], j["in_party"]) == \
        ("permanently_lost", "ineligible", "none", "lost", 0)
    assert events(conn, campaign_id, "permanent_loss")[0]["visibility"] == "player"


def test_dead_cannot_use_tropes_except_the_window(conn, campaign_id, running, pc):
    give(conn, campaign_id, pc, "trope:3")
    kill(conn, campaign_id)
    use = {"op": "use_trope", "character": "Mara", "ticket": "hb.ticket.base.final-girl", "effect": "survives"}
    with pytest.raises(ChangeError, match="can't act"):
        apply(conn, campaign_id, use)
    with pytest.raises(ChangeError, match="can't act"):
        apply(conn, campaign_id, {**use, "ticket": "trope:3"})                # no window yet
    apply(conn, campaign_id, {"op": "posthumous_window", "character": "Mara", "ticket": "trope:3", "effect": "one sentence reaches Jonah"})
    assert char(conn, "mara")["participation"] == "posthumous_limited"
    with pytest.raises(ChangeError, match="only"):
        apply(conn, campaign_id, use)                                       # the window is for trope:3 alone
    apply(conn, campaign_id, {**use, "ticket": "trope:3", "effect": "Run, Jonah."})
    m = char(conn, "mara")
    assert (m["participation"], m["rights_basis"]) == ("observer", None)
    with pytest.raises(ChangeError, match="can't act"):
        apply(conn, campaign_id, {**use, "ticket": "trope:3"})


def test_window_needs_a_death_ticket_and_a_dead_body(conn, campaign_id, running, pc):
    give(conn, campaign_id, pc, "trope:2")
    give(conn, campaign_id, pc, "trope:3")
    window = {"op": "posthumous_window", "character": "Mara", "ticket": "trope:3", "effect": "x"}
    with pytest.raises(ChangeError, match="not dead"):
        apply(conn, campaign_id, window)
    kill(conn, campaign_id)
    with pytest.raises(ChangeError, match="after death"):
        apply(conn, campaign_id, {**window, "ticket": "trope:2"})           # Rescue, no Death/sacrifice tag
    with transaction(conn):
        conn.execute("UPDATE character_tickets SET uses_left = 0 WHERE character_id = ? AND ticket_ref = 'trope:3'", (pc,))
    with pytest.raises(ChangeError, match="no uses left"):
        apply(conn, campaign_id, window)
