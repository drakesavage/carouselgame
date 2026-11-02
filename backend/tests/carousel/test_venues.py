import copy

from app.carousel import venues
from app.carousel.importer import run_import
from app.db import connect, init_db, now_iso, transaction
from app.memory.snapshots import create_save_point, restore_save_point

GATE, BARN, CELLAR, HIDDEN = (f"SM-T01:{n}" for n in ("n01_farm_gate", "n02_barn", "n03_cellar_door", "n04_MINI-HIDDEN-ROOM"))


def overlay(conn, cid, kind, a, b, label=None, reason="test", lifted=0, story_id=None):
    with transaction(conn):
        conn.execute("""INSERT INTO venue_overlays (campaign_id, story_id, venue_id, kind, a, b, label, reason, lifted, created_minutes)
                        VALUES (?,?,?,?,?,?,?,?,?,0)""", (cid, story_id, "SM-T01", kind, a, b, label, reason, lifted))


def test_neighbours_follow_the_links(conn, campaign_id):
    got = venues.neighbours(conn, campaign_id, CELLAR)
    assert [n["zone_id"] for n in got] == [BARN, HIDDEN]
    assert all(n["blocked_reason"] is None for n in got)
    assert [n["label"] for n in got] == ["barn", "MINI-HIDDEN-ROOM"]


def test_a_block_overlay_marks_the_link_blocked(conn, campaign_id):
    overlay(conn, campaign_id, "block", BARN, CELLAR, reason="the door has swollen shut")
    got = {n["zone_id"]: n["blocked_reason"] for n in venues.neighbours(conn, campaign_id, BARN)}
    assert got == {GATE: None, CELLAR: "the door has swollen shut"}


def test_a_lifted_block_no_longer_blocks(conn, campaign_id):
    overlay(conn, campaign_id, "block", BARN, CELLAR, lifted=1)
    assert all(n["blocked_reason"] is None for n in venues.neighbours(conn, campaign_id, BARN))


def test_an_added_zone_is_a_neighbour_and_resolves(conn, campaign_id):
    overlay(conn, campaign_id, "add_zone", "SM-T01:story-1-1", CELLAR, label="A second cellar")
    assert "SM-T01:story-1-1" in [n["zone_id"] for n in venues.neighbours(conn, campaign_id, CELLAR)]
    assert [n["zone_id"] for n in venues.neighbours(conn, campaign_id, "SM-T01:story-1-1")] == [CELLAR]
    assert venues.zone(conn, "SM-T01:story-1-1")["label"] == "A second cellar"


def test_story_zone_lookup_is_scoped_to_campaign(conn, campaign_id):
    overlay(conn, campaign_id, "add_zone", "SM-T01:story-1-1", CELLAR, label="PRIVATE-OTHER-CAMPAIGN")
    assert venues.zone(conn, "SM-T01:story-1-1", campaign_id)["label"] == "PRIVATE-OTHER-CAMPAIGN"
    assert venues.zone(conn, "SM-T01:story-1-1", campaign_id + 1) is None


def test_venue_at_and_visible_zones(conn, campaign_id):
    assert venues.venue_at(conn, "CAR-086")["id"] == "SM-T01"
    assert venues.venue_at(conn, "CAR-016") is None   # a story-instance venue is not entered from the street
    assert venues.venue_at(conn, "CAR-002") is None
    with transaction(conn):
        conn.execute("UPDATE campaigns SET venue_id = 'SM-T01', zone_id = ? WHERE id = ?", (BARN, campaign_id))
        conn.execute("INSERT INTO zone_visits (campaign_id, zone_id, first_minutes) VALUES (?,?,0)", (campaign_id, GATE))
        conn.execute("INSERT INTO zone_visits (campaign_id, zone_id, first_minutes) VALUES (?,?,0)", (campaign_id, BARN))
    assert set(venues.visible_zones(conn, campaign_id)) == {GATE, BARN, CELLAR}
    assert venues.zone_omens(conn, CELLAR) == ["OM-005"]
    assert venues.zone_omens(conn, GATE) == []   # OM-001's seed node needs manual review


def test_restore_rolls_back_venue_state(conn, campaign_id):
    with transaction(conn):
        sp = create_save_point(conn, campaign_id, "before")
        conn.execute("UPDATE campaigns SET venue_id = 'SM-T01', zone_id = ? WHERE id = ?", (BARN, campaign_id))
        conn.execute("INSERT INTO zone_visits (campaign_id, zone_id, first_minutes) VALUES (?,?,0)", (campaign_id, BARN))
    overlay(conn, campaign_id, "block", BARN, CELLAR)
    restore_save_point(conn, campaign_id, sp)
    assert tuple(conn.execute("SELECT venue_id, zone_id FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()) == (None, None)
    assert conn.execute("SELECT COUNT(*) FROM zone_visits WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM venue_overlays WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0


def test_reimport_protects_venue_and_zone(tmp_path):
    from app.config import Settings
    from tests.carousel.conftest import write_mini_source
    from tests.carousel.mini_data import VENUES
    renamed = copy.deepcopy(VENUES)
    farm = renamed["maps"][0]
    farm["graph"]["nodes"][1]["id"] = "n02_big_barn"
    for e in farm["graph"]["edges"]:
        e["from"], e["to"] = (x.replace("n02_barn", "n02_big_barn") for x in (e["from"], e["to"]))
    farm["portals"][1]["depart_from_node"] = "n02_big_barn"
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src", {"venues": renamed}), gm_provider="mock")
    s.ensure_dirs()
    c = connect(s.campaign_db)
    init_db(c)
    c.execute("""INSERT INTO campaigns (id, name, game_time_minutes, node_id, venue_id, zone_id, created_at, updated_at)
                 VALUES (1, 'T', 0, 'CAR-086', 'SM-T01', ?, ?, ?)""", (BARN, now_iso(), now_iso()))
    c.execute("INSERT INTO zone_visits (campaign_id, zone_id, first_minutes) VALUES (1, 'SM-T01:story-3-1', 0)")   # story rooms always resolve
    c.commit()
    c.close()
    r = run_import(s)
    assert not r.ok and r.missing == {"zone": [BARN]}


# ---- Task 3: entering and leaving with travel ----

from tests.conftest import apply  # noqa: E402


def where(conn, cid):
    return tuple(conn.execute("SELECT node_id, venue_id, zone_id FROM campaigns WHERE id = ?", (cid,)).fetchone())


def visits(conn, cid):
    return [r[0] for r in conn.execute("SELECT zone_id FROM zone_visits WHERE campaign_id = ? ORDER BY zone_id", (cid,))]


def test_travel_to_a_venue_place_enters_its_entry_zone(conn, campaign_id):
    r = apply(conn, campaign_id, {"op": "travel", "to": "CAR-086"})
    assert where(conn, campaign_id) == ("CAR-086", "SM-T01", GATE)
    assert visits(conn, campaign_id) == [GATE]
    assert r["interrupted"]["omen_id"] == "OM-001"   # the farm's own Omen still stops the group as before
    assert r["venue"] == {"venue_id": "SM-T01", "title": "Farm grounds", "zone_label": "farm gate"}
    payload = conn.execute("SELECT payload_json FROM events WHERE campaign_id = ? AND type = 'travel'", (campaign_id,)).fetchone()[0]
    assert '"zone_label": "farm gate"' in payload and "MINI-HIDDEN-ROOM" not in payload
    assert conn.execute("SELECT 1 FROM omen_states WHERE campaign_id = ? AND omen_id = 'OM-005'", (campaign_id,)).fetchone() is None


def test_leaving_only_by_the_entry_or_exit(conn, campaign_id):
    import pytest
    from app.rules.engine import ChangeError
    apply(conn, campaign_id, {"op": "travel", "to": "CAR-086"})
    with transaction(conn):
        conn.execute("UPDATE campaigns SET zone_id = ? WHERE id = ?", (CELLAR, campaign_id))
    with pytest.raises(ChangeError, match="leave by farm gate or barn"):
        apply(conn, campaign_id, {"op": "travel", "to": "CAR-002"})
    assert where(conn, campaign_id) == ("CAR-086", "SM-T01", CELLAR)
    with transaction(conn):
        conn.execute("UPDATE campaigns SET zone_id = ? WHERE id = ?", (BARN, campaign_id))
    apply(conn, campaign_id, {"op": "travel", "to": "CAR-002"})
    assert where(conn, campaign_id) == ("CAR-002", None, None)


def test_a_story_instance_venue_is_not_entered(conn, campaign_id):
    apply(conn, campaign_id, {"op": "travel", "to": "CAR-016"})
    assert where(conn, campaign_id) == ("CAR-016", None, None)


def test_a_campaign_started_at_a_venue_place_is_inside_it(conn):
    from app import campaigns
    cid = campaigns.create(conn, name="Farmhands", character="Ada", location="Farm", opening=False)
    assert where(conn, cid) == ("CAR-086", "SM-T01", GATE)


def test_arrival_narration_names_the_entry_zone(conn, campaign_id):
    from app.map_api import arrival
    with transaction(conn):
        conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage) VALUES (?, 'OM-001', 'visible')", (campaign_id,))
    r = apply(conn, campaign_id, {"op": "travel", "to": "CAR-086"})
    text, kind = arrival(conn, campaign_id, r, "CAR-086")
    assert kind == "arrival" and "farm gate" in text
