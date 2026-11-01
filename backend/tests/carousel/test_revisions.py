import json

import pytest

from app.rules.engine import ChangeError
from tests.carousel.test_story_ops import begin, events, seen
from tests.conftest import apply

COMMANDO = "hb.ticket.aspect.soldier.commando"


@pytest.fixture
def running(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")
    sid = begin(conn, campaign_id)["story_id"]
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah", "ticket": COMMANDO, "source": "test"})
    apply(conn, campaign_id, {"op": "equip_ticket", "character": "Jonah", "ticket": COMMANDO})
    return sid


def revision(**over):
    return {"op": "revise_screenplay", "character": "Jonah", "ticket": COMMANDO, "base_version": 0, "scope": "arc",
            "operations": [{"family": "insert_future_scene", "detail": "REVISION-DETAIL: the farmer's radio works after all"}],
            "costs": [], "new_dangers": [], "preserved_facts": ["the scarecrow is still out there"],
            "public_summary": "Jonah remembers the farmer kept a radio.", "reason": "REVISION-REASON", **over}


def uses(conn, who="jonah", ref=COMMANDO):
    return conn.execute("SELECT t.uses_left FROM character_tickets t JOIN characters c ON c.id = t.character_id WHERE c.slug = ? AND t.ticket_ref = ?",
                        (who, ref)).fetchone()[0]


def n_revisions(conn):
    return conn.execute("SELECT COUNT(*) FROM story_revisions").fetchone()[0]


def test_a_commando_revises_the_arc(conn, campaign_id, running):
    r = apply(conn, campaign_id, revision())
    assert r["version"] == 1
    assert conn.execute("SELECT version FROM stories WHERE id = ?", (running,)).fetchone()[0] == 1
    rev = dict(conn.execute("SELECT * FROM story_revisions").fetchone())
    assert (rev["version"], rev["ticket_ref"], rev["scope"], rev["public_summary"]) == (1, COMMANDO, "arc", "Jonah remembers the farmer kept a radio.")
    assert json.loads(rev["before_json"])["screenplay"]["version"] == 0 and json.loads(rev["after_json"])["screenplay"]["version"] == 1
    sp = json.loads(conn.execute("SELECT screenplay_json FROM story_packets WHERE story_id = ?", (running,)).fetchone()[0])
    assert sp["version"] == 1 and "REVISION-DETAIL" in json.dumps(sp["notes"]) and len(sp["phase_plan"]) == 8
    assert uses(conn) == 0
    [pub] = events(conn, campaign_id, "screenplay_revised")
    assert pub["visibility"] == "player" and "radio" in pub["summary"]
    assert "REVISION-DETAIL" not in json.dumps(pub) and "REVISION-REASON" not in json.dumps(pub)
    [gm] = events(conn, campaign_id, "screenplay_revised_detail")
    assert gm["visibility"] == "gm" and "REVISION-DETAIL" in gm["payload_json"]
    [used] = events(conn, campaign_id, "trope_used")
    assert "revises the screenplay: Jonah remembers" in used["summary"] and "REVISION-DETAIL" not in json.dumps(used)


def test_the_baseline_and_sealed_truth_never_change(conn, campaign_id, running):
    before = conn.execute("SELECT sealed_json, sealed_sha256 FROM story_packets").fetchone()
    apply(conn, campaign_id, revision())
    assert tuple(conn.execute("SELECT sealed_json, sealed_sha256 FROM story_packets").fetchone()) == tuple(before)


def test_ending_conditions_merge_new_endings(conn, campaign_id, running):
    apply(conn, campaign_id, revision(scope="ending_conditions", costs=["the barn is lost"], new_dangers=["fire spreads to the field"],
                                      operations=[{"family": "rewrite_conditional_objective", "detail": "burn it in the barn"}],
                                      new_endings={"full_success": {"predicate": "The scarecrow burns inside the barn."}}))
    e = json.loads(conn.execute("SELECT endings_json FROM story_packets").fetchone()[0])
    assert e["full_success"]["predicate"] == "The scarecrow burns inside the barn." and e["defeat"]["predicate"] == "The farm keeps everyone."


def test_revision_refusals_spend_nothing(conn, campaign_id, running):
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Adeline", "ticket": COMMANDO, "source": "test"})
    apply(conn, campaign_id, {"op": "equip_ticket", "character": "Adeline", "ticket": COMMANDO})

    def refused(match, change):
        before = (uses(conn), uses(conn, "mara", "hb.ticket.base.final-girl"), uses(conn, "adeline"), n_revisions(conn))
        with pytest.raises(ChangeError, match=match):
            apply(conn, campaign_id, change)
        assert (uses(conn), uses(conn, "mara", "hb.ticket.base.final-girl"), uses(conn, "adeline"), n_revisions(conn)) == before

    refused("changed \\(now version 0\\)", revision(base_version=3))
    refused("cannot revise", revision(character="Mara", ticket="hb.ticket.base.final-girl"))
    refused("extraordinary_timeline_branch", revision(operations=[{"family": "extraordinary_timeline_branch", "detail": "x"}]))
    refused("costs", revision(scope="ending_conditions", new_dangers=["d"], new_endings={"full_success": {"predicate": "p"}}))
    refused("new_dangers", revision(scope="ending_conditions", costs=["c"], new_endings={"full_success": {"predicate": "p"}}))
    refused("new_endings", revision(scope="ending_conditions", costs=["c"], new_dangers=["d"]))
    refused("not in the cast", revision(character="Adeline"))
    apply(conn, campaign_id, revision())                     # spends Jonah's only use
    refused("no uses left", revision(base_version=1))
    assert n_revisions(conn) == 1


def test_revisions_need_an_active_story(conn, campaign_id):
    with pytest.raises(ChangeError, match="no story is active"):
        apply(conn, campaign_id, revision())


def test_unknown_ending_names_are_refused(conn, campaign_id, running):
    with pytest.raises(ChangeError):
        apply(conn, campaign_id, revision(scope="ending_conditions", costs=["c"], new_dangers=["d"], new_endings={"total_victory": {"predicate": "p"}}))
