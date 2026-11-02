"""Plan 7 Task 5: coming back to camp briefs the GM on what changed while the group was away."""
import re

from app.carousel import residents
from app.db import transaction
from app.memory.context import build_context
from tests.carousel.test_rewards import conclude
from tests.carousel.test_story_ops import begin
from tests.conftest import apply

COPY = re.compile(r"The group is back at camp after (\d+)h(\d\d)\. Since they left: (.+); new posters: (.+)\. Residents around now: (.+)\. "
                  r"You may add colour \(who noticed, rumours, who asks about the story\), recorded with record_fact or remember\. "
                  r"Nothing happened that the records don't show\.")


def camp(conn, cid):
    return dict(conn.execute("SELECT left_camp_minutes, left_camp_event_id, game_time_minutes, node_id FROM campaigns WHERE id = ?", (cid,)).fetchone())


def go(conn, cid, to):
    return apply(conn, cid, {"op": "travel", "to": to})


def test_leaving_camp_starts_the_clock(conn, campaign_id):
    go(conn, campaign_id, "CAR-047")
    assert camp(conn, campaign_id)["left_camp_minutes"] is None     # arriving isn't leaving
    before = camp(conn, campaign_id)
    last = conn.execute("SELECT MAX(id) FROM events WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]
    go(conn, campaign_id, "CAR-086")
    after = camp(conn, campaign_id)
    assert (after["left_camp_minutes"], after["left_camp_event_id"]) == (before["game_time_minutes"], last)
    assert residents.camp_return(conn, campaign_id) is None          # away, not back


def test_return_section_lists_what_happened(conn, campaign_id):
    go(conn, campaign_id, "CAR-047")
    go(conn, campaign_id, "CAR-086")
    left = camp(conn, campaign_id)["left_camp_minutes"]
    begin(conn, campaign_id)   # arriving at the farm sighted OM-001
    conclude(conn, campaign_id, ending="survive_without_victory")
    r = go(conn, campaign_id, "CAR-047")
    ev = conn.execute("SELECT payload_json FROM events WHERE campaign_id = ? AND type = 'travel' ORDER BY id DESC LIMIT 1", (campaign_id,)).fetchone()[0]
    assert '"camp_return": true' in ev and r["arrived"] == "CAR-047"
    back = residents.camp_return(conn, campaign_id)
    title = conn.execute("SELECT title FROM stories WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]
    assert back["stories"] == [{"title": title, "ending": "survive_without_victory", "cast_names": ["Mara Vance", "Jonah Reyes"]}]
    assert back["away_minutes"] == camp(conn, campaign_id)["game_time_minutes"] - left and back["posters"] == []
    text = build_context(conn, campaign_id, "[arrival] We arrive at Dyer's Lodge.")["text"]
    section = text.split("## CAMP RETURN\n", 1)[1].split("\n## ", 1)[0]
    m = COPY.fullmatch(section.strip())
    assert m, section
    assert f"{title} (survive_without_victory; cast: Mara Vance, Jonah Reyes)" in m.group(3) and m.group(4) == "none"
    assert int(m.group(1)) * 60 + int(m.group(2)) == back["away_minutes"]


def test_return_section_shows_once(client, conn, campaign_id):
    go(conn, campaign_id, "CAR-047")
    go(conn, campaign_id, "CAR-086")
    import uuid
    r = client.post(f"/api/campaigns/{campaign_id}/travel", json={"to": "CAR-047", "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    shown = conn.execute("SELECT context_text FROM turn_diagnostics WHERE campaign_id = ? ORDER BY turn_id DESC LIMIT 1", (campaign_id,)).fetchone()[0]
    assert "## CAMP RETURN" in shown and "Since they left: no stories" in shown
    assert camp(conn, campaign_id)["left_camp_minutes"] is None
    client.post(f"/api/campaigns/{campaign_id}/turns", json={"text": "I sit by the fire", "idempotency_key": uuid.uuid4().hex})
    again = conn.execute("SELECT context_text FROM turn_diagnostics WHERE campaign_id = ? ORDER BY turn_id DESC LIMIT 1", (campaign_id,)).fetchone()[0]
    assert "## CAMP RETURN" not in again


def test_no_section_when_never_left(conn, campaign_id):
    go(conn, campaign_id, "CAR-047")
    assert residents.camp_return(conn, campaign_id) is None
    assert "## CAMP RETURN" not in build_context(conn, campaign_id, "hello")["text"]


def test_no_section_after_travelling_on(conn, campaign_id):
    """Back at camp, then off again before any turn: no stale return when they next arrive somewhere else."""
    go(conn, campaign_id, "CAR-047")
    go(conn, campaign_id, "CAR-086")
    go(conn, campaign_id, "CAR-047")
    go(conn, campaign_id, "CAR-002")
    assert residents.camp_return(conn, campaign_id) is None
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id = 'CAR-047' WHERE id = ?", (campaign_id,))   # placed at camp without an arrival
    assert residents.camp_return(conn, campaign_id) is None
