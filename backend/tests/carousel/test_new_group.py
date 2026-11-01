import pytest

from app.campaigns import new_group
from app.carousel import opening
from app.db import transaction
from tests.carousel.test_fate_end import jonah_dies
from tests.carousel.test_fate_ops import kill
from tests.carousel.test_rewards import char, conclude, running  # noqa: F401  (fixture)


@pytest.fixture
def wipe(conn, campaign_id, running):
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)


def camp(conn, cid):
    return dict(conn.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone())


def test_new_group_only_after_a_wipe(conn, campaign_id):
    with pytest.raises(ValueError, match="missing-poster board"):
        new_group(conn, campaign_id, "Nora Quill")
    assert camp(conn, campaign_id)["group_no"] == 1


def test_new_group_refused_while_a_story_runs(conn, campaign_id, running, pc):
    with transaction(conn):
        conn.execute("UPDATE characters SET persistent_fate = 'missing_postered' WHERE id = ?", (pc,))
    with pytest.raises(ValueError, match="story"):
        new_group(conn, campaign_id, "Nora Quill")


def test_new_group_keeps_the_town(conn, campaign_id, wipe, pc, npc):
    before = camp(conn, campaign_id)
    posters = conn.execute("SELECT COUNT(*) FROM missing_posters").fetchone()[0]
    adeline = char(conn, "adeline")
    nid = new_group(conn, campaign_id, "Nora Quill")
    after = camp(conn, campaign_id)
    assert (after["group_no"], after["active_character_id"], after["opening_stage"], after["node_id"]) == (2, nid, "car", "CAR-082")
    assert after["game_time_minutes"] == before["game_time_minutes"] and after["lure_kind"] is None
    assert [r[0] for r in conn.execute("SELECT place_id FROM known_places WHERE campaign_id = ?", (campaign_id,))] == ["CAR-082"]
    assert conn.execute("SELECT COUNT(*) FROM omen_states WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM missing_posters").fetchone()[0] == posters == 2
    assert conn.execute("SELECT COUNT(*) FROM stories WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 1
    assert char(conn, "adeline") == adeline and char(conn, "mara")["status"] == "missing"
    n = dict(conn.execute("SELECT * FROM characters WHERE id = ?", (nid,)).fetchone())
    assert (n["name"], n["kind"], n["in_party"], n["persistent_fate"]) == ("Nora Quill", "pc", 1, "ongoing")
    assert [r[0] for r in conn.execute("SELECT id FROM characters WHERE campaign_id = ? AND in_party = 1", (campaign_id,))] == [nid]
    assert conn.execute("SELECT label FROM save_points WHERE campaign_id = ? ORDER BY id DESC LIMIT 1", (campaign_id,)).fetchone()[0] == \
        "A new group arrives"


def test_new_group_slug_never_collides(conn, campaign_id, wipe):
    nid = new_group(conn, campaign_id, "Mara")                     # the seed's lost Mara already has the slug "mara"
    assert conn.execute("SELECT slug FROM characters WHERE id = ?", (nid,)).fetchone()[0] == "mara-2"


def test_new_groups_opening_omen_needs_its_own_story(conn, campaign_id, wipe):
    new_group(conn, campaign_id, "Nora Quill")
    with transaction(conn):
        conn.execute("UPDATE campaigns SET opening_stage = 'opening_omen' WHERE id = ?", (campaign_id,))
    assert any("no story has started" in b for b in opening.blockers(conn, campaign_id))


def test_stories_carry_their_group(conn, campaign_id, running):
    assert conn.execute("SELECT group_no FROM stories WHERE id = ?", (running,)).fetchone()[0] == 1
    with transaction(conn):
        conn.execute("UPDATE stories SET status = 'concluded' WHERE id = ?", (running,))
        conn.execute("UPDATE campaigns SET group_no = 3 WHERE id = ?", (campaign_id,))
    from tests.carousel.test_story_ops import begin
    sid = begin(conn, campaign_id)["story_id"]
    assert conn.execute("SELECT group_no FROM stories WHERE id = ?", (sid,)).fetchone()[0] == 3


def test_new_group_api(client, conn, campaign_id, running):
    url = f"/api/campaigns/{campaign_id}/new-group"
    r = client.post(url, json={"character": "Nora Quill"})
    assert r.status_code == 409 and "missing-poster board" in r.text
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    r = client.post(url, json={"character": "Nora Quill"})
    assert r.status_code == 200, r.text
    nid = r.json()["character_id"]
    assert client.get(f"/api/campaigns/{campaign_id}").json()["active_character_id"] == nid
