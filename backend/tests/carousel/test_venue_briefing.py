from app.gm.prompts import SYSTEM_PROMPT
from app.memory.context import build_context
from tests.carousel.test_story_briefing import section
from tests.carousel.test_venue_ops import BARN, CELLAR, at_farm
from tests.conftest import apply

GM_ONLY = "GM-ONLY"


def venue_text(conn, cid):
    return section(build_context(conn, cid, "We look around.")["text"], "VENUE")


def test_venue_section_at_the_barn(conn, campaign_id):
    at_farm(conn, campaign_id)
    apply(conn, campaign_id, {"op": "move_zone", "to": BARN})
    v = venue_text(conn, campaign_id)
    assert "Farm grounds — you are at barn (interior)" in v
    ways = v[v.index("Ways on:"):v.index("Zones seen:")]
    assert "cellar door" in ways and "farm gate" in ways
    seen = v[v.index("Zones seen:"):v.index("Entry:")]
    assert "farm gate" in seen and "barn" in seen
    assert "Entry: farm gate. Exit: barn." in v
    assert "MINI-HIDDEN-ROOM" not in v


def test_hidden_room_appears_once_adjacent(conn, campaign_id):
    at_farm(conn, campaign_id)
    for z in (BARN, CELLAR):
        apply(conn, campaign_id, {"op": "move_zone", "to": z})
    assert "MINI-HIDDEN-ROOM" in venue_text(conn, campaign_id)


def test_cues_are_in_the_gm_only_part(conn, campaign_id):
    at_farm(conn, campaign_id)
    v = venue_text(conn, campaign_id)
    shared, gm = v.split(GM_ONLY, 1)
    assert "OM-005" not in shared and "OM-001" not in shared
    assert "OM-005 at cellar door: A cellar door breathing (Choice open:cellar_door)" in gm
    assert "OM-001 at farm gate" in gm and "(GM places it)" in gm


def test_blocks_and_overlays_are_listed(conn, campaign_id):
    from tests.carousel.test_venue_ops import story_at_cellar
    story_at_cellar(conn, campaign_id)
    apply(conn, campaign_id, {"op": "block_passage", "a": BARN, "b": CELLAR, "reason": "the door holds"})
    v = venue_text(conn, campaign_id)
    assert "barn [blocked: the door holds]" in v
    overlays = v[v.index("Overlays from this story:"):]
    assert "the door holds" in overlays.split(GM_ONLY)[0]


def test_no_venue_section_on_the_road(conn, campaign_id):
    assert "## VENUE" not in build_context(conn, campaign_id, "We walk.")["text"]


def test_contract_names_move_zone():
    assert "Inside a venue the group moves only by move_zone along the ways listed" in SYSTEM_PROMPT
