import json

import pytest

from app.db import now_iso, transaction
from app.rules.engine import ChangeError
from tests.carousel.test_fate_end import jonah_dies, posters
from tests.carousel.test_fate_ops import give, kill
from tests.carousel.test_rewards import char, conclude, running  # noqa: F401  (fixture)
from tests.carousel.test_story_ops import begin, events
from tests.conftest import apply

COMMANDO = "hb.ticket.aspect.soldier.commando"


@pytest.fixture
def wiped(conn, campaign_id, running):
    """Mara and Jonah die in OM-001; Nora, a new player character, stands in the Town Square holding a Rescue ticket."""
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    with transaction(conn):
        nora = conn.execute("""INSERT INTO characters (campaign_id, slug, name, kind, roster_category, status, in_party, party_order, provenance)
                               VALUES (?, 'nora', 'Nora Quill', 'pc', 'player_character', 'active', 1, 0, 'test')""", (campaign_id,)).lastrowid
        conn.execute("UPDATE campaigns SET active_character_id = ? WHERE id = ?", (nora, campaign_id))
    give(conn, campaign_id, nora, "trope:2")
    return {p["name"].split()[0].lower(): p["id"] for p in posters(conn)} | {"nora": nora}


def to_diner(conn, cid):
    apply(conn, cid, {"op": "travel", "to": "CAR-006", "reason": "the board"})


def take(conn, cid, poster, who="Nora"):
    return apply(conn, cid, {"op": "take_poster", "poster": poster, "character": who})


def rescue_begin(conn, cid, posters_, ticket="trope:2"):
    return begin(conn, cid, cast=[{"character": "Nora Quill", "casting": "lead"}],
                 rescue={"character": "Nora", "ticket": ticket, "posters": posters_})


def test_take_poster_only_at_the_diner(conn, campaign_id, wiped):
    with pytest.raises(ChangeError, match="Diner"):
        take(conn, campaign_id, wiped["mara"])
    to_diner(conn, campaign_id)
    take(conn, campaign_id, wiped["mara"])
    assert conn.execute("SELECT held_by_character_id FROM missing_posters WHERE id = ?", (wiped["mara"],)).fetchone()[0] == wiped["nora"]
    [ev] = events(conn, campaign_id, "poster_taken")
    assert ev["summary"] == "Nora Quill takes down Mara Vance's poster" and ev["visibility"] == "player"
    with pytest.raises(ChangeError, match="not on the board"):
        take(conn, campaign_id, wiped["mara"])
    with pytest.raises(ChangeError, match="can't take"):
        take(conn, campaign_id, wiped["jonah"], who="Mara")                  # the missing can't take posters


def test_rescue_needs_a_rescue_ticket_and_the_poster(conn, campaign_id, wiped):
    to_diner(conn, campaign_id)
    with pytest.raises(ChangeError, match="isn't holding"):
        rescue_begin(conn, campaign_id, [wiped["mara"]])                     # not taken yet
    take(conn, campaign_id, wiped["mara"])
    give(conn, campaign_id, wiped["nora"], "trope:3")
    with pytest.raises(ChangeError, match="Rescue"):
        rescue_begin(conn, campaign_id, [wiped["mara"]], ticket="trope:3")
    with transaction(conn):
        conn.execute("UPDATE missing_posters SET story_lock = 'MINI-OM-003' WHERE id = ?", (wiped["mara"],))
    with pytest.raises(ChangeError, match="another story"):
        rescue_begin(conn, campaign_id, [wiped["mara"]])
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1


def test_rescue_success_brings_them_back(conn, campaign_id, wiped):
    to_diner(conn, campaign_id)
    take(conn, campaign_id, wiped["mara"])
    sid = rescue_begin(conn, campaign_id, [wiped["mara"]])["story_id"]
    s = dict(conn.execute("SELECT rescue_json FROM stories WHERE id = ?", (sid,)).fetchone())
    assert json.loads(s["rescue_json"]) == {"character_id": wiped["nora"], "ticket_ref": "trope:2", "poster_ids": [wiped["mara"]]}
    assert char(conn, "mara")["rescue_eligibility"] == "active"
    assert conn.execute("SELECT uses_left FROM character_tickets WHERE character_id = ? AND ticket_ref = 'trope:2'", (wiped["nora"],)).fetchone()[0] == 0
    assert events(conn, campaign_id, "story_rescue")[0]["summary"] == "This story is a Rescue"
    assert str(wiped["mara"]) in events(conn, campaign_id, "story_rescue_detail")[0]["payload_json"]
    apply(conn, campaign_id, {"op": "advance_phase", "to": "Finale", "trigger": "the barn burns"})
    conclude(conn, campaign_id, "full_success", awards=[{"character": "Nora", "performance": 1, "difficulty": 1}])
    m = char(conn, "mara")
    assert (m["rescue_eligibility"], m["persistent_fate"], m["physical_condition"], m["wallpaper_status"], m["participation"], m["status"]) == \
        ("completed", "restored", "alive", "active", "full", "active")
    p = dict(conn.execute("SELECT * FROM missing_posters WHERE id = ?", (wiped["mara"],)).fetchone())
    assert p["rescued_minutes"] is not None and p["held_by_character_id"] is None
    assert events(conn, campaign_id, "character_found")[0]["summary"] == "Mara Vance is found"
    assert conn.execute("SELECT COUNT(*) FROM death_events WHERE character_id = (SELECT id FROM characters WHERE slug = 'mara')").fetchone()[0] == 1
    assert char(conn, "jonah")["rescue_eligibility"] == "possible"           # nobody carried Jonah's poster


def test_failed_rescue_is_final(conn, campaign_id, wiped):
    to_diner(conn, campaign_id)
    take(conn, campaign_id, wiped["mara"])
    rescue_begin(conn, campaign_id, [wiped["mara"]])
    conclude(conn, campaign_id, "defeat", awards=[{"character": "Nora", "performance": 0, "difficulty": 0}])
    assert char(conn, "mara")["rescue_eligibility"] == "failed"
    assert char(conn, "mara")["persistent_fate"] == "missing_postered"
    take(conn, campaign_id, wiped["mara"])                                   # the poster is back on the board
    with transaction(conn):
        conn.execute("UPDATE character_tickets SET uses_left = 1 WHERE ticket_ref = 'trope:2'")
    with pytest.raises(ChangeError, match="failed"):
        rescue_begin(conn, campaign_id, [wiped["mara"]])


def test_rescue_takeover_blocks_other_revisions(conn, campaign_id, wiped):
    to_diner(conn, campaign_id)
    take(conn, campaign_id, wiped["mara"])
    give(conn, campaign_id, wiped["nora"], COMMANDO)
    rescue_begin(conn, campaign_id, [wiped["mara"]])
    with pytest.raises(ChangeError, match="Takeover"):
        apply(conn, campaign_id, {"op": "revise_screenplay", "character": "Nora", "ticket": COMMANDO, "base_version": 0, "scope": "scene",
                                  "operations": [{"family": "shift_pressure", "detail": "x"}], "public_summary": "x", "reason": "x"})


def test_rescue_posters_stay_gm_only(client, conn, campaign_id, wiped):
    from tests.test_visibility import _player_urls
    to_diner(conn, campaign_id)
    take(conn, campaign_id, wiped["mara"])
    rescue_begin(conn, campaign_id, [wiped["mara"]])
    for url in _player_urls(client, campaign_id):
        text = client.get(url).text
        for s in ("rescue_json", "rescue_eligibility", "poster_ids", "CAUSE-SECRET"):
            assert s not in text, f"{s} leaked via {url}"
