from app.memory.context import build_context
from tests.carousel.test_story_briefing import section
from tests.conftest import apply

LEADS = "OMEN LEADS"


def brief(conn, cid):
    return build_context(conn, cid, "We look around.")["text"]


def at_square(conn, cid):
    assert conn.execute("SELECT node_id FROM campaigns WHERE id = ?", (cid,)).fetchone()[0] == "CAR-002"   # where the seed starts


def test_item_and_special_omens_are_leads_at_their_place(conn, campaign_id):
    at_square(conn, campaign_id)
    leads = section(brief(conn, campaign_id), LEADS)
    assert "OM-011 [item] A music box nobody wound" in leads and "object can travel" in leads
    assert "OM-012 [special] The band plays one song too many" in leads and "SPAWN-PAPA-15" in leads


def test_no_leads_away_from_their_place(conn, campaign_id):
    apply(conn, campaign_id, {"op": "travel", "to": "CAR-047"})
    assert LEADS not in brief(conn, campaign_id)


def test_a_sighted_lead_drops_out(conn, campaign_id):
    at_square(conn, campaign_id)
    apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-011", "how": "The music box starts on its own."})
    leads = section(brief(conn, campaign_id), LEADS)
    assert "OM-011" not in leads and "OM-012" in leads


def test_leads_carry_no_sealed_material(conn, campaign_id):
    at_square(conn, campaign_id)
    leads = section(brief(conn, campaign_id), LEADS)
    assert "MINI-SEALED-TRUTH" not in leads and "Mini OM-011" not in leads and "MINI-OM-012" not in leads
