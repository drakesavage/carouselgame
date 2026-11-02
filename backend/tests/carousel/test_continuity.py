import json

import pytest

from app.carousel.stories import ENDINGS, seal
from app.db import transaction
from app.rules.engine import ChangeError
from tests.carousel.test_story_ops import begin, seen
from tests.fixtures.carousel_seed import seed_carousel


def require_predecessor(conn):
    row = conn.execute("SELECT sealed_json FROM ref.omen_beats WHERE omen_id='OM-001'").fetchone()
    dossier = json.loads(row[0])
    dossier["continuity"]["predecessor_omen_id"] = "OM-003"
    dossier["continuity"]["predecessor_policy"] = "require_actual_concluded_run"
    conn.execute("UPDATE ref.omen_beats SET sealed_json=? WHERE omen_id='OM-001'", (json.dumps(dossier),))


def recorded_run(conn, cid, ending="defeat"):
    text, sha = seal({"private_truth": "PREDECESSOR-PRIVATE"})
    sid = conn.execute("""INSERT INTO stories (campaign_id,omen_id,story_lock,title,status,phase,ending,ending_reason,ended_minutes)
        VALUES (?, 'OM-003','MINI-OM-003','Prior story','concluded','The End',?,'PRIVATE-OUTCOME-REASON',590)""", (cid, ending)).lastrowid
    conn.execute("INSERT INTO story_packets VALUES (?,?,?,?,?)", (sid, text, sha, "{}", "{}"))
    return sid, sha


def test_sequel_refuses_missing_history_without_starting_or_revealing_it(conn, campaign_id):
    require_predecessor(conn)
    seen(conn, campaign_id, "OM-001")
    other = seed_carousel(conn)
    recorded_run(conn, other)
    with pytest.raises(ChangeError, match="recorded conclusion"):
        begin(conn, campaign_id)
    assert not conn.execute("SELECT 1 FROM stories WHERE campaign_id=?", (campaign_id,)).fetchone()


@pytest.mark.parametrize("ending", ENDINGS)
def test_sequel_seals_actual_ending_without_resetting_campaign_or_leaking_it(conn, campaign_id, ending):
    require_predecessor(conn)
    seen(conn, campaign_id, "OM-001")
    sid, sha = recorded_run(conn, campaign_id, ending)
    before = tuple(conn.execute("SELECT node_id,zone_id,game_time_minutes FROM campaigns WHERE id=?", (campaign_id,)).fetchone())
    result = begin(conn, campaign_id)
    packet = json.loads(conn.execute("SELECT sealed_json FROM story_packets WHERE story_id=?", (result["story_id"],)).fetchone()[0])
    assert packet["continuity_context"]["ending"] == ending
    assert packet["continuity_context"]["predecessor_story_id"] == sid
    assert packet["continuity_context"]["sealed_sha256"] == sha
    assert tuple(conn.execute("SELECT node_id,zone_id,game_time_minutes FROM campaigns WHERE id=?", (campaign_id,)).fetchone()) == before
    public = json.dumps([tuple(r) for r in conn.execute("SELECT summary,payload_json FROM events WHERE campaign_id=? AND visibility='player'", (campaign_id,))])
    assert "PRIVATE-OUTCOME-REASON" not in public and "PREDECESSOR-PRIVATE" not in public


def test_sequel_refuses_damaged_predecessor_packet(conn, campaign_id):
    require_predecessor(conn)
    seen(conn, campaign_id, "OM-001")
    sid, _ = recorded_run(conn, campaign_id)
    with transaction(conn):
        conn.execute("UPDATE story_packets SET sealed_sha256='bad' WHERE story_id=?", (sid,))
    with pytest.raises(ChangeError, match="failed verification"):
        begin(conn, campaign_id)
