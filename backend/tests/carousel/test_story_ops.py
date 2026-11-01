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
