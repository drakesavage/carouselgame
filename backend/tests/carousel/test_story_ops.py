import json

import pytest

from app.db import now_iso, transaction
from app.rules.engine import ChangeError, apply_change
from tests.conftest import apply


def events(conn, cid, type_):
    return [dict(r) for r in conn.execute("SELECT summary, payload_json, visibility FROM events WHERE campaign_id = ? AND type = ? ORDER BY id",
                                          (cid, type_))]


def stage(conn, cid, omen):
    r = conn.execute("SELECT stage FROM omen_states WHERE campaign_id = ? AND omen_id = ?", (cid, omen)).fetchone()
    return r[0] if r else None


# ---------------------------------------------------------------- Task 2: sighting and examining

def test_sight_omen_makes_it_visible(conn, campaign_id):
    r = apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-003", "how": "the diner door is open"})
    assert stage(conn, campaign_id, "OM-003") == "visible"
    [ev] = events(conn, campaign_id, "omen_sighted")
    assert ev["visibility"] == "player" and "A chair knocked over" in ev["summary"]
    assert "A chair knocked over" in r["summary"]


def test_sighting_twice_is_refused(conn, campaign_id):
    apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-003", "how": "seen"})
    with pytest.raises(ChangeError, match="already seen"):
        apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-003", "how": "seen again"})
    assert len(events(conn, campaign_id, "omen_sighted")) == 1


def test_hubs_can_be_sighted_unknown_records_cannot(conn, campaign_id):
    apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-009", "how": "flyers on the board"})
    assert stage(conn, campaign_id, "OM-009") == "visible"
    with pytest.raises(ChangeError, match="no Omen"):
        apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-404", "how": "x"})


def test_examine_shows_the_premise_never_the_truth(conn, campaign_id):
    apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-001", "how": "from the road"})
    r = apply(conn, campaign_id, {"op": "examine_omen", "omen": "OM-001"})
    assert stage(conn, campaign_id, "OM-001") == "examined"
    assert r["premise"] == "Something is wrong at the farm."
    [ev] = events(conn, campaign_id, "omen_examined")
    assert ev["visibility"] == "player" and "Something is wrong at the farm." in ev["summary"]
    assert "MINI-SEALED-TRUTH" not in json.dumps(r) and "MINI-SEALED-TRUTH" not in json.dumps(ev)


def test_examining_an_unseen_omen_is_refused(conn, campaign_id):
    with pytest.raises(ChangeError, match="not seen"):
        apply(conn, campaign_id, {"op": "examine_omen", "omen": "OM-003"})
    assert stage(conn, campaign_id, "OM-003") is None


# ---------------------------------------------------------------- Task 3: beginning a story

STORY_TABLES = ("stories", "story_packets", "story_cast", "events")


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in STORY_TABLES}


def turn(conn, cid, kind=None):
    with transaction(conn):
        return conn.execute("INSERT INTO turns (campaign_id, idempotency_key, status, player_input, kind, created_at) VALUES (?,?,?,?,?,?)",
                            (cid, f"k-{now_iso()}-{kind}-{conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0]}", "processing",
                             "We go into the field.", kind, now_iso())).lastrowid


def on_turn(conn, cid, turn_id, change):
    with transaction(conn):
        return apply_change(conn, cid, turn_id, change)


ADELINE_HERE = {"op": "resident_whereabouts", "resident": "Adeline Winter", "place": "CAR-002", "until_minutes": 900, "reason": "in town"}


def begin(conn, cid, **over):
    change = {"op": "begin_story", "omen": "OM-001", "action": "enter:farm", "reason": "Mara walks into the field",
              "cast": [{"character": "Mara Vance", "casting": "lead"}, {"character": "Jonah Reyes", "casting": "support"}], **over}
    return on_turn(conn, cid, over.pop("turn_id", None) or turn(conn, cid), {k: v for k, v in change.items() if k != "turn_id"})


def seen(conn, cid, *omens):
    for o in omens:
        apply(conn, cid, {"op": "sight_omen", "omen": o, "how": "seen"})


def test_begin_story_on_the_players_choice(conn, campaign_id, pc, companion):
    seen(conn, campaign_id, "OM-001")
    r = begin(conn, campaign_id)
    s = dict(conn.execute("SELECT * FROM stories WHERE id = ?", (r["story_id"],)).fetchone())
    assert (s["status"], s["phase"], s["pressure"], s["camera"], s["version"], s["title"], s["story_lock"], s["choice_action"]) == \
        ("active", "Party", 1, "on", 0, "Mini OM-001", "MINI-OM-001", "enter:farm")
    assert r["phase"] == "Party" and s["started_minutes"] == 600 and s["started_turn_id"]
    p = dict(conn.execute("SELECT * FROM story_packets WHERE story_id = ?", (r["story_id"],)).fetchone())
    from app.carousel.stories import seal
    from tests.carousel.mini_data import FULL_DOSSIER
    assert (p["sealed_json"], p["sealed_sha256"]) == seal(FULL_DOSSIER)
    sp = json.loads(p["screenplay_json"])
    assert sp["version"] == 0 and len(sp["phase_plan"]) == 8 and sp["notes"] == []
    assert set(json.loads(p["endings_json"])) == {"full_success", "costly_success", "survive_without_victory", "defeat"}
    cast = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT character_id, casting, joined_phase FROM story_cast")}
    assert cast == {pc: ("lead", "Party"), companion: ("support", "Party")}
    evs = [dict(e) for e in conn.execute("SELECT type, summary, payload_json, visibility FROM events WHERE type LIKE 'story_%' ORDER BY id")]
    assert [e["type"] for e in evs] == ["story_omen", "story_choice", "story_phase"]
    assert all(e["visibility"] == "player" for e in evs)
    assert "MINI-SEALED-TRUTH" not in json.dumps(evs) and "MINI-OBLIGATION" not in json.dumps(evs)


def test_begin_story_refusals(conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-009")

    def refused(match, **over):
        before = counts(conn)
        with pytest.raises(ChangeError, match=match):
            begin(conn, campaign_id, **over)
        assert counts(conn) == before

    refused("player's own Choice", turn_id=turn(conn, campaign_id, kind="arrival"))
    before = counts(conn)
    with pytest.raises(ChangeError, match="player's own Choice"):
        on_turn(conn, campaign_id, None, {"op": "begin_story", "omen": "OM-001", "action": "enter:farm", "reason": "r",
                                          "cast": [{"character": "Mara Vance", "casting": "lead"}]})
    assert counts(conn) == before
    refused("not a Playable Omen", omen="OM-009", action="enter:town square")
    refused("not seen", omen="OM-003", action="enter:diner")
    refused("Choice action is enter:farm", action="look:farm")
    refused("player character", cast=[{"character": "Jonah Reyes", "casting": "lead"}])
    begin(conn, campaign_id)
    seen(conn, campaign_id, "OM-003")
    refused("another story is running: Mini OM-001", omen="OM-003", action="enter:diner")


def test_bad_casting_is_refused(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    with pytest.raises(ChangeError):
        begin(conn, campaign_id, cast=[{"character": "Mara Vance", "casting": "hero"}])


def test_dossier_without_outcomes_needs_gm_endings(conn, campaign_id):
    seen(conn, campaign_id, "OM-003")
    with pytest.raises(ChangeError, match="endings"):
        begin(conn, campaign_id, omen="OM-003", action="enter:diner")
    with pytest.raises(ChangeError, match="endings"):
        begin(conn, campaign_id, omen="OM-003", action="enter:diner", endings={"full_success": {"predicate": "x"}})
    four = {e: {"predicate": f"GM-ENDING {e}"} for e in ("full_success", "costly_success", "survive_without_victory", "defeat")}
    r = begin(conn, campaign_id, omen="OM-003", action="enter:diner", endings=four)
    assert json.loads(conn.execute("SELECT endings_json FROM story_packets WHERE story_id = ?", (r["story_id"],)).fetchone()[0]) == four


def test_dossier_endings_cannot_be_overridden(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    four = {e: {"predicate": "easy"} for e in ("full_success", "costly_success", "survive_without_victory", "defeat")}
    with pytest.raises(ChangeError, match="sealed"):
        begin(conn, campaign_id, endings=four)


# ---------------------------------------------------------------- Task 4: Plot Cycle, pressure, camera, casting

def story(conn):
    return dict(conn.execute("SELECT * FROM stories WHERE status = 'active'").fetchone())


@pytest.fixture
def running(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    return begin(conn, campaign_id)["story_id"]


def test_advance_phase_forward_and_skip(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "advance_phase", "to": "First Blood", "trigger": "the first scarecrow moves"})
    assert (story(conn)["phase"], story(conn)["pressure"]) == ("First Blood", 2)
    apply(conn, campaign_id, {"op": "advance_phase", "to": "Finale", "trigger": "the barn burns: everything at once"})
    s = story(conn)
    assert s["phase"] == "Finale" and s["pressure"] >= 5
    evs = events(conn, campaign_id, "story_phase")
    assert evs[-1]["visibility"] == "player" and evs[-1]["summary"] == "Plot Cycle: First Blood → Finale"
    assert "barn burns" not in json.dumps(evs)


def test_advance_phase_refusals(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "advance_phase", "to": "Rebirth", "trigger": "t"})
    for to, match in (("First Blood", "forward"), ("Rebirth", "forward"), ("The End", "conclude"), ("Intermission", "invalid")):
        with pytest.raises(ChangeError, match=match):
            apply(conn, campaign_id, {"op": "advance_phase", "to": to, "trigger": "t"})
    assert story(conn)["phase"] == "Rebirth"


def test_pressure_is_clamped_and_gm_only(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "pressure", "delta": 9, "reason": "the radio dies"})
    assert story(conn)["pressure"] == 6
    apply(conn, campaign_id, {"op": "pressure", "delta": -20, "reason": "dawn"})
    assert story(conn)["pressure"] == 0
    assert {e["visibility"] for e in events(conn, campaign_id, "story_pressure")} == {"gm"}


def test_camera_counts_screen_moments(conn, campaign_id, running, pc, companion):
    apply(conn, campaign_id, {"op": "camera", "on": True, "focus": ["Mara Vance"], "reason": "her move"})
    apply(conn, campaign_id, {"op": "camera", "on": True, "focus": ["Mara Vance", "Jonah"], "reason": "both"})
    moments = dict(conn.execute("SELECT character_id, screen_moments FROM story_cast").fetchall())
    assert moments == {pc: 2, companion: 1}
    apply(conn, campaign_id, {"op": "camera", "on": False, "focus": [], "reason": "cut away"})
    assert story(conn)["camera"] == "off"
    evs = events(conn, campaign_id, "story_camera")
    assert evs[0]["visibility"] == "player" and "Mara" in evs[0]["summary"]
    with pytest.raises(ChangeError, match="not in the cast"):
        apply(conn, campaign_id, {"op": "camera", "on": True, "focus": ["Adeline"], "reason": "x"})


def test_cast_character_mid_story(conn, campaign_id, running, npc, companion):
    apply(conn, campaign_id, {"op": "advance_phase", "to": "First Blood", "trigger": "t"})
    apply(conn, campaign_id, ADELINE_HERE)   # a resident joins a cast only where the group is (Plan 7)
    apply(conn, campaign_id, {"op": "cast_character", "character": "Adeline", "casting": "witness"})
    assert tuple(conn.execute("SELECT casting, joined_phase FROM story_cast WHERE character_id = ?", (npc,)).fetchone()) == ("witness", "First Blood")
    apply(conn, campaign_id, {"op": "cast_character", "character": "Jonah", "casting": "sacrifice"})
    assert tuple(conn.execute("SELECT casting, joined_phase FROM story_cast WHERE character_id = ?", (companion,)).fetchone()) == ("sacrifice", "Party")


def test_story_ops_need_an_active_story(conn, campaign_id):
    for change in ({"op": "advance_phase", "to": "Finale", "trigger": "t"}, {"op": "pressure", "delta": 1, "reason": "r"},
                   {"op": "camera", "on": True, "focus": [], "reason": "r"}, {"op": "cast_character", "character": "Adeline", "casting": "witness"}):
        with pytest.raises(ChangeError, match="no story is active"):
            apply(conn, campaign_id, change)
