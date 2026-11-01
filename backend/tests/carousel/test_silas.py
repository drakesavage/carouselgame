import json

import pytest

from app.campaigns import create
from app.db import now_iso
from app.rules.engine import ChangeError
from tests.carousel.test_opening_ops import friend
from tests.carousel.test_story_ops import on_turn, turn
from tests.conftest import apply

QUOTE = "I'll check the map first"
DEAL = {"op": "silas_assigns", "character": "Mara", "role": "base.final-girl", "reason": "she plans before she moves",
        "evidence": [QUOTE], "contextual_ticket": "hb.ticket.starter.shared.quick-thinker", "contextual_reason": "she improvises fast"}


def say(conn, cid, role, content):
    conn.execute("INSERT INTO messages (campaign_id, role, content, created_at) VALUES (?,?,?,?)", (cid, role, content, now_iso()))


@pytest.fixture
def cid(conn):
    c = create(conn, name="Opening", character="Mara")
    friend(conn, c, "Jonah")
    say(conn, c, "player", "Hold on. I’ll  check the MAP first, okay?")
    say(conn, c, "gm", 'Jonah laughs. "She always says I\'ll check the map first."')
    conn.execute("UPDATE campaigns SET opening_stage = 'silas' WHERE id = ?", (c,))
    return c


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("characters", "character_tickets", "events")} | {
        "dealt": conn.execute("SELECT COUNT(*) FROM characters WHERE role_id IS NOT NULL OR mettle IS NOT NULL OR role_evidence_json IS NOT NULL").fetchone()[0]}


def test_silas_deals_the_archetype_and_two_tickets(conn, cid):
    apply(conn, cid, DEAL)
    c = conn.execute("SELECT id, role_id, mettle, moxie, hustle, savvy, grit, role_evidence_json FROM characters WHERE name = 'Mara'").fetchone()
    assert c["role_id"] == "base.final-girl" and tuple(c)[2:7] == (2, 2, 3, 2, 2)
    assert json.loads(c["role_evidence_json"]) == {"quotes": [QUOTE], "contextual_reason": "she improvises fast"}
    tickets = {r[0]: r[1] for r in conn.execute("SELECT ticket_ref, source FROM character_tickets WHERE character_id = ?", (c["id"],))}
    assert tickets == {"hb.ticket.base.final-girl": "Silas", "hb.ticket.starter.shared.quick-thinker": "Silas"}
    [ev] = conn.execute("SELECT summary, visibility FROM events WHERE campaign_id = ? AND type = 'silas_deals'", (cid,)).fetchall()
    assert ev["visibility"] == "player"
    assert ev["summary"] == ("Silas deals Mara three tickets: Final Girl (she plans before she moves), [HB] Last One Standing, "
                             "[HB] Think on Your Feet")


@pytest.mark.parametrize("change, error", [
    ({"evidence": ["I never said this at all"]}, "never said"),
    ({"contextual_ticket": "hb.ticket.base.final-girl"}, "contextual"),
    ({"role": "base.unknown"}, "base.unknown"),
])
def test_silas_refusals_write_nothing(conn, cid, change, error):
    before = counts(conn)
    with pytest.raises(ChangeError, match=error):
        apply(conn, cid, {**DEAL, **change})
    assert counts(conn) == before


def test_silas_deals_once(conn, cid):
    apply(conn, cid, DEAL)
    before = counts(conn)
    with pytest.raises(ChangeError, match="already"):
        apply(conn, cid, DEAL)
    assert counts(conn) == before


def test_silas_deals_only_to_the_group_from_the_car(conn, cid):
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003", "in_party": True})
    before = counts(conn)
    with pytest.raises(ChangeError, match="Valerie Choi"):
        apply(conn, cid, {**DEAL, "character": "Valerie Choi"})
    assert counts(conn) == before
    apply(conn, cid, {**DEAL, "character": "Jonah"})   # a friend from the car


def test_silas_deals_at_the_silas_stage_only(conn, cid):
    conn.execute("UPDATE campaigns SET opening_stage = 'car' WHERE id = ?", (cid,))
    before = counts(conn)
    with pytest.raises(ChangeError, match="silas stage"):
        apply(conn, cid, DEAL)
    assert counts(conn) == before


def test_before_the_opening_omen_nothing_skips_ahead(conn, cid):
    for stage in ("car", "silas"):
        conn.execute("UPDATE campaigns SET opening_stage = ? WHERE id = ?", (stage, cid))
        with pytest.raises(ChangeError, match="Silas assigns"):
            apply(conn, cid, {"op": "initialize_character", "character": "Jonah", "role": "base.film-buff", "reason": "x"})
        with pytest.raises(ChangeError, match="car park"):
            apply(conn, cid, {"op": "travel", "to": "CAR-002"})
        apply(conn, cid, {"op": "sight_omen", "omen": "OM-003", "how": "x"}) if stage == "car" else None
        with pytest.raises(ChangeError, match="after Silas"):
            on_turn(conn, cid, turn(conn, cid), {"op": "begin_story", "omen": "OM-003", "action": "go in", "reason": "x",
                                                           "cast": [{"character": "Mara", "casting": "lead"}]})
    conn.execute("UPDATE campaigns SET opening_stage = 'opening_omen' WHERE id = ?", (cid,))
    apply(conn, cid, {"op": "initialize_character", "character": "Jonah", "role": "base.film-buff", "reason": "x"})
    apply(conn, cid, {"op": "travel", "to": "CAR-002"})
