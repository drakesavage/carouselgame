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
