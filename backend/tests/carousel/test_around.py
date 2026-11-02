"""Plan 7 Task 3: who's around the group, the AROUND briefing section, and last seen."""
import re

from app.campaigns import create
from app.carousel import residents
from app.db import transaction
from app.memory.context import build_context
from tests.conftest import apply

COMMON, KITCHEN, STEPS = "SM-028:n02_shared_living_common_ro", "SM-028:n03_kitchen_and_pantry", "SM-028:n01_lodge_front_steps"


def stand(conn, cid, node, zone=None, minutes=60):
    """Put the group at a node (and a zone of its venue) at a game minute, after the opening."""
    venue = zone.split(":")[0] if zone else None
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id = ?, venue_id = ?, zone_id = ?, game_time_minutes = ?, opening_stage = 'done' WHERE id = ?",
                     (node, venue, zone, minutes, cid))


def sections(ctx):
    return dict(re.findall(r"^## (.+?)\n(.*?)(?=^## |\Z)", ctx["text"], re.S | re.M))


def test_around_matches_place_and_zone(real_campaign):
    conn, cid = real_campaign
    stand(conn, cid, "CAR-047", COMMON)
    near = {a["name"]: a for a in residents.around(conn, cid)}
    assert near["Adeline Winter"]["why"] == "home" and near["Adeline Winter"]["met"] is False
    stand(conn, cid, "CAR-047", KITCHEN)
    near = {a["name"] for a in residents.around(conn, cid)}
    assert "Adeline Winter" not in near and near   # the kitchen has its own residents
    stand(conn, cid, "CAR-002", "SM-001:n01_square_central_approach")
    assert residents.around(conn, cid) == []      # 09:00: everyone is at camp


def test_a_shared_venue_matches_by_zone(real_campaign):
    """The Town Square and the Diner are one venue (SM-001): arriving at the Diner puts the group in the square zone,
    where residents hanging out at the Town Square stand."""
    conn, cid = real_campaign
    square, diner = "SM-001:n01_square_central_approach", "SM-001:n02_diner_front_and_interio"
    apply(conn, cid, {"op": "resident_whereabouts", "resident": "CAR-NPC-001", "place": "CAR-002", "zone": square, "until_minutes": 600, "reason": "x"})
    stand(conn, cid, "CAR-006", square)
    assert [a["name"] for a in residents.around(conn, cid)] == ["Adeline Winter"]
    stand(conn, cid, "CAR-006", diner)
    assert residents.around(conn, cid) == []


def test_around_without_zones_and_party_left_out(conn):
    cid = create(conn, name="Around", character="Mara", opening=False, location="Dyer's Lodge")
    stand(conn, cid, "CAR-047")
    assert sorted(a["name"] for a in residents.around(conn, cid)) == ["Adeline Winter", "Valerie Choi"]
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003", "in_party": True})
    assert [a["name"] for a in residents.around(conn, cid)] == ["Adeline Winter"]


def test_around_section_and_gm_hook(real_campaign):
    conn, cid = real_campaign
    stand(conn, cid, "CAR-047", COMMON)
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-001"})
    s = sections(build_context(conn, cid, "I look around the common room"))
    around = s["AROUND"]
    assert "Adeline Winter (met) — Camp leader; newcomer orientation and arbitration" in around
    assert re.search(r"Not yet met: CAR-NPC-\d{3} — .+; .+\. Introduce with introduce_resident if they come into the scene\.", around)
    hook = "She wants newcomers to survive"
    assert hook in s["GM-ONLY SECRETS"]
    assert all(hook not in body for name, body in s.items() if name != "GM-ONLY SECRETS")
    secret = "attempts to leave Carousel are far more complicated"   # Adeline's canonical secret: never in the around hooks
    assert secret not in s["GM-ONLY SECRETS"].split("Residents around")[-1]


def test_briefing_stays_small_with_70_residents(real_campaign):
    conn, cid = real_campaign
    stand(conn, cid, "CAR-047", COMMON)
    ctx = build_context(conn, cid, "I look around")
    s = sections(ctx)
    lines = s["AROUND"].strip().splitlines()
    assert len(lines) <= 13 and lines[-1].startswith("…and ") and "more around" in lines[-1]
    unmet = [r[0] for r in conn.execute("""SELECT c.name FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                                           WHERE c.campaign_id = ? AND p.met = 0""", (cid,))]
    assert len(unmet) == 70
    for name in ("PRESENT", "PARTY"):
        assert not [n for n in unmet if n in s[name]], name
    assert len(s["AROUND"]) < 2600
    assert len(ctx["text"]) < 8000, len(ctx["text"])   # about 5000 with 16 residents around the common room
    assert len(s.get("GM-ONLY SECRETS", "")) < 2600


def test_unmet_named_by_the_player_stays_out_of_present(real_campaign):
    conn, cid = real_campaign
    stand(conn, cid, "CAR-047", COMMON)
    s = sections(build_context(conn, cid, "I ask Adeline Winter where to sleep"))
    assert "Adeline Winter" not in s["PRESENT"]
    assert "Adeline" not in s.get("GM-ONLY SECRETS", "").split("Residents around")[0]


def test_last_seen_updates_on_arrival(real_campaign):
    conn, cid = real_campaign
    stand(conn, cid, "CAR-046", "SM-027:n01_camp_main_road_entrance")
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-001"})
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-054"})   # Lantern Team, lives on the Lodge porch
    apply(conn, cid, {"op": "travel", "to": "CAR-047"})

    def seen(rid):
        return conn.execute("SELECT last_seen FROM npc_profiles WHERE ref_id = ? AND character_id IN (SELECT id FROM characters WHERE campaign_id = ?)",
                            (rid, cid)).fetchone()[0]
    assert seen("CAR-NPC-054") == "Dyer’s Lodge, lodge front steps at day 1 morning"
    assert seen("CAR-NPC-001") is None
    apply(conn, cid, {"op": "move_zone", "to": COMMON})
    assert seen("CAR-NPC-001") == "Dyer’s Lodge, shared living/common room at day 1 morning"
