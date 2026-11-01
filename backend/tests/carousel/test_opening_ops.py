import json

import pytest

from app.campaigns import create
from app.carousel import opening
from app.rules.engine import ChangeError
from tests.conftest import apply

LURE = {"op": "set_lure", "kind": "event", "belief": "A free weekend at the county fair", "truth": "LURE-TRUTH: the fair is the town's bait"}


@pytest.fixture
def cid(conn):
    return create(conn, name="Opening", character="Mara")


def friend(conn, cid, name):
    return apply(conn, cid, {"op": "npc", "name": name, "in_party": True, "description": "A friend in the car."})


def events(conn, cid, type_):
    return [dict(r) for r in conn.execute("SELECT summary, payload_json, visibility FROM events WHERE campaign_id = ? AND type = ?", (cid, type_))]


# ---------------------------------------------------------------- Task 2: lure, friends, residents

def test_the_lure_is_set_once(conn, cid):
    apply(conn, cid, LURE)
    row = conn.execute("SELECT lure_kind, lure_belief FROM campaigns WHERE id = ?", (cid,)).fetchone()
    assert tuple(row) == ("event", "A free weekend at the county fair")
    with pytest.raises(ChangeError, match="already set"):
        apply(conn, cid, LURE)


def test_a_person_lure_needs_a_real_resident(conn, cid):
    with pytest.raises(ChangeError, match="resident"):
        apply(conn, cid, {**LURE, "kind": "person"})
    with pytest.raises(ChangeError, match="CAR-NPC-999"):
        apply(conn, cid, {**LURE, "kind": "person", "resident": "CAR-NPC-999"})
    apply(conn, cid, {**LURE, "kind": "person", "resident": "CAR-NPC-003"})
    assert conn.execute("SELECT lure_resident FROM campaigns WHERE id = ?", (cid,)).fetchone()[0] == "CAR-NPC-003"


def test_the_lure_truth_stays_gm_only(conn, cid):
    apply(conn, cid, LURE)
    [s] = conn.execute("SELECT content FROM gm_secrets WHERE campaign_id = ? AND kind = 'lure'", (cid,)).fetchall()
    assert s[0] == LURE["truth"]
    assert not any("LURE-TRUTH" in json.dumps(tuple(e)) for e in conn.execute("SELECT summary, payload_json FROM events").fetchall())
    [ev] = events(conn, cid, "opening_lure")
    assert ev["visibility"] == "player" and LURE["belief"] in ev["summary"]
    fact = conn.execute("SELECT fact, visibility FROM lore_facts WHERE campaign_id = ? AND subject = 'Why we came'", (cid,)).fetchone()
    assert tuple(fact) == (LURE["belief"], "player")


def test_the_lure_is_set_in_the_car_only(conn, cid):
    conn.execute("UPDATE campaigns SET opening_stage = 'silas' WHERE id = ?", (cid,))
    with pytest.raises(ChangeError, match="car"):
        apply(conn, cid, LURE)


def test_dropping_a_friend(conn, cid):
    friend(conn, cid, "Jonah")
    friend(conn, cid, "Priya")
    apply(conn, cid, {"op": "drop_friend", "character": "Jonah", "reason": "the player wants a smaller car"})
    assert [f["name"] for f in opening.friends(conn, cid)] == ["Priya"]
    row = conn.execute("SELECT in_party, status, roster_category FROM characters WHERE name = 'Jonah'").fetchone()
    assert tuple(row) == (0, "dormant", "cut_from_opening")
    assert len(events(conn, cid, "opening_friend_dropped")) == 1
    with pytest.raises(ChangeError):
        apply(conn, cid, {"op": "drop_friend", "character": "Mara", "reason": "not a friend"})


def test_introducing_a_resident(conn, cid):
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003", "in_party": True})
    c = conn.execute("""SELECT c.name, c.bio, c.roster_category, c.in_party, c.mettle, p.personality, p.ref_id FROM characters c
                        JOIN npc_profiles p ON p.character_id = c.id WHERE p.ref_id = 'CAR-NPC-003'""").fetchone()
    assert tuple(c) == ("Valerie Choi", "Wiry, in a patched field jacket.", "core_party", 1, None, "Dry, watchful, quick to laugh.", "CAR-NPC-003")
    [sheet] = conn.execute("SELECT content FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet'", (cid,)).fetchall()
    assert "MINI-NPC-SECRET-2" in sheet[0]
    [ev] = events(conn, cid, "npc_met")
    assert ev["visibility"] == "player" and "Valerie Choi" in ev["summary"] and "MINI-NPC-SECRET" not in json.dumps(ev)
    assert opening.friends(conn, cid) == []   # a resident is not a friend from the car
    with pytest.raises(ChangeError, match="already"):
        apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    with pytest.raises(ChangeError, match="CAR-NPC-999"):
        apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-999"})


def test_the_lure_pays_off_at_the_lodge_during_the_escort_only(conn, cid):
    apply(conn, cid, LURE)
    conn.execute("UPDATE campaigns SET opening_stage = 'escort' WHERE id = ?", (cid,))
    with pytest.raises(ChangeError, match="Lodge"):
        apply(conn, cid, {"op": "pay_off_lure", "how": "The fair was never real."})
    conn.execute("UPDATE campaigns SET opening_stage = 'car', node_id = 'CAR-047' WHERE id = ?", (cid,))
    with pytest.raises(ChangeError, match="escort"):
        apply(conn, cid, {"op": "pay_off_lure", "how": "The fair was never real."})
    conn.execute("UPDATE campaigns SET opening_stage = 'escort' WHERE id = ?", (cid,))
    apply(conn, cid, {"op": "pay_off_lure", "how": "The fair was never real."})
    assert conn.execute("SELECT lure_paid_off FROM campaigns WHERE id = ?", (cid,)).fetchone()[0] == "The fair was never real."
    [ev] = events(conn, cid, "opening_lure_paid_off")
    assert ev["visibility"] == "player"


def test_a_person_lure_pays_off_once_the_resident_is_met(conn, cid):
    apply(conn, cid, {**LURE, "kind": "person", "resident": "CAR-NPC-003"})
    conn.execute("UPDATE campaigns SET opening_stage = 'escort', node_id = 'CAR-047' WHERE id = ?", (cid,))
    with pytest.raises(ChangeError, match="Valerie Choi"):
        apply(conn, cid, {"op": "pay_off_lure", "how": "She was here all along."})
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    apply(conn, cid, {"op": "pay_off_lure", "how": "She was here all along."})


# ---------------------------------------------------------------- Task 4: moving through the opening

def advance(conn, cid, to, **kw):
    return apply(conn, cid, {"op": "advance_opening", "to": to, **kw})


def deal(conn, cid, who, role="base.final-girl"):
    apply(conn, cid, {"op": "silas_assigns", "character": who, "role": role, "reason": "how they talked in the car",
                      "evidence": ["Are we there yet, seriously?"], "contextual_ticket": "hb.ticket.starter.shared.quick-thinker",
                      "contextual_reason": "quick on the uptake"})


def to_silas(conn, cid):
    from tests.carousel.test_silas import say
    apply(conn, cid, LURE)
    for n in ("Jonah", "Priya", "Theo"):
        friend(conn, cid, n)
    say(conn, cid, "player", "Are we there yet, seriously?")
    advance(conn, cid, "silas")


def test_full_opening_walkthrough(conn, cid):
    from app.carousel.stories import active_story
    from tests.carousel.test_story_ops import on_turn, turn
    to_silas(conn, cid)
    for who in ("Mara", "Jonah", "Priya", "Theo"):
        deal(conn, cid, who)
    advance(conn, cid, "opening_omen")
    apply(conn, cid, {"op": "sight_omen", "omen": "OM-001", "how": "a scarecrow across the lot"})
    on_turn(conn, cid, turn(conn, cid), {"op": "begin_story", "omen": "OM-001", "action": "enter:farm", "reason": "Mara goes in",
                                         "cast": [{"character": "Mara", "casting": "lead"}]})
    apply(conn, cid, {"op": "conclude_story", "ending": "survive_without_victory", "reason": "they ran",
                      "awards": [{"character": "Mara", "performance": 1, "difficulty": 1}]})
    assert active_story(conn, cid) is None
    advance(conn, cid, "escort")
    assert "CAR-047" in {r[0] for r in conn.execute("SELECT place_id FROM known_places WHERE campaign_id = ?", (cid,))}
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003", "in_party": True})
    for _ in range(3):
        r = apply(conn, cid, {"op": "travel", "to": "CAR-047"})
        if r["arrived"] == "CAR-047":
            break
    assert r["arrived"] == "CAR-047"
    apply(conn, cid, {"op": "pay_off_lure", "how": "The fair was a flyer Silas printed."})
    advance(conn, cid, "done")
    assert opening.stage(conn, cid) == "done"
    assert conn.execute("SELECT label FROM save_points WHERE campaign_id = ? ORDER BY id DESC", (cid,)).fetchone()[0] == "Arrived at Dyer's Lodge"
    labels = [r[0] for r in conn.execute("SELECT summary FROM events WHERE campaign_id = ? AND type = 'opening_stage' ORDER BY id", (cid,))]
    assert [l.split(" is over")[0] for l in labels] == ["The drive in", "Silas the Showman", "The first Omen", "Dyer's Lodge"]


def test_advance_refusals_name_blockers(conn, cid):
    with pytest.raises(ChangeError, match="can't move to opening_omen: the next stage is silas"):
        advance(conn, cid, "opening_omen")
    apply(conn, cid, LURE)
    friend(conn, cid, "Jonah")
    friend(conn, cid, "Priya")
    with pytest.raises(ChangeError, match="2 friends; the car holds 3"):
        advance(conn, cid, "silas")
    friend(conn, cid, "Theo")
    from tests.carousel.test_silas import say
    say(conn, cid, "player", "Are we there yet, seriously?")
    advance(conn, cid, "silas")
    for who in ("Mara", "Priya", "Theo"):
        deal(conn, cid, who)
    with pytest.raises(ChangeError, match="Jonah has no Archetype yet"):
        advance(conn, cid, "opening_omen")
    deal(conn, cid, "Jonah")
    advance(conn, cid, "opening_omen")
    with pytest.raises(ChangeError, match="no story has started"):
        advance(conn, cid, "escort")
    advance(conn, cid, "escort", reason="the player walked past every Omen")
    [skip] = conn.execute("SELECT visibility FROM events WHERE campaign_id = ? AND type = 'opening_skip_reason'", (cid,)).fetchall()
    assert skip[0] == "gm"
    with pytest.raises(ChangeError, match="isn't at Dyer's Lodge"):
        advance(conn, cid, "done")
    assert opening.stage(conn, cid) == "escort"
