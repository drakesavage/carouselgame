from app.campaigns import create
from app.gm.prompts import SYSTEM_PROMPT
from tests.carousel.test_opening_ops import LURE, friend
from tests.carousel.test_story_briefing import brief, section
from tests.conftest import apply

CAR_JOB = ("Choose the lure (set_lure), create the three friends to fit it (npc, in_party), and open on the drive with everyone talking. "
           "Let the player adjust or cut a friend (drop_friend) before the conversation gets going. Give each person natural moments "
           "to show who they are.")


def test_opening_section_in_the_car(conn):
    cid = create(conn, name="Opening", character="Mara")
    apply(conn, cid, LURE)
    friend(conn, cid, "Jonah")
    s = section(brief(conn, cid)["text"], "OPENING")
    assert f"Stage: car — {CAR_JOB}" in s
    assert "Next stage blocked by:" in s and "1 friend; the car holds 3" in s
    assert LURE["belief"] in s and "Jonah: unassigned" in s
    assert "base.final-girl, Final Girl" in s and "Mettle 2, Moxie 2, Hustle 3, Savvy 2, Grit 2" in s
    assert "hb.ticket.starter.shared.quick-thinker, [HB] Think on Your Feet: Once per scene" in s


def test_opening_section_says_when_ready(conn):
    cid = create(conn, name="Opening", character="Mara")
    apply(conn, cid, LURE)
    for n in ("Jonah", "Priya", "Theo"):
        friend(conn, cid, n)
    assert "ready: advance_opening to silas" in section(brief(conn, cid)["text"], "OPENING")


def test_no_silas_table_after_silas(conn):
    cid = create(conn, name="Opening", character="Mara")
    conn.execute("UPDATE campaigns SET opening_stage = 'opening_omen' WHERE id = ?", (cid,))
    s = section(brief(conn, cid)["text"], "OPENING")
    assert "Stage: opening_omen — The group is pulled toward a story almost at once" in s
    assert "base.final-girl" not in s and "quick-thinker" not in s


def test_no_opening_section_when_done_or_absent(conn, campaign_id):
    assert "## OPENING" not in brief(conn, campaign_id)["text"]
    cid = create(conn, name="Opening", character="Mara")
    conn.execute("UPDATE campaigns SET opening_stage = 'done' WHERE id = ?", (cid,))
    assert "## OPENING" not in brief(conn, cid)["text"]


def test_the_lure_truth_is_gm_only_in_the_briefing(conn):
    cid = create(conn, name="Opening", character="Mara")
    apply(conn, cid, LURE)
    t = brief(conn, cid)["text"]
    assert t.count(LURE["truth"]) == 1 and t.index(LURE["truth"]) > t.index("## GM-ONLY SECRETS")


def test_the_contract_has_the_opening_paragraph():
    assert "New game: follow the OPENING section. Never assign an Archetype by menu or ask the player to pick one" in SYSTEM_PROMPT
