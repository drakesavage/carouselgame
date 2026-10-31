from app.carousel.interruptions import ROAM_RADIUS, Interruption, decide, roam_chance, truncate
from app.carousel.travel import plan_route
from app.db import transaction
from app.memory.snapshots import create_save_point, restore_save_point


def seen(conn, cid, *omens):
    with transaction(conn):
        for o in omens:
            conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage) VALUES (?, ?, 'visible')", (cid, o))


def test_fixed_omen_on_the_way_stops_the_route(conn, campaign_id):
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    assert decide(conn, campaign_id, r, 0) == Interruption("OM-003", "CAR-006", "fixed")


def test_fixed_takes_precedence_over_roaming(conn, campaign_id):
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    assert all(decide(conn, campaign_id, r, s).kind == "fixed" for s in range(50))


def test_a_seen_fixed_omen_no_longer_stops(conn, campaign_id):
    seen(conn, campaign_id, "OM-003", "OM-002")
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    assert decide(conn, campaign_id, r, 0) is None


def test_never_at_the_start_node(conn, campaign_id):
    seen(conn, campaign_id, "OM-002")
    r = plan_route(conn, "CAR-006", "CAR-002", set())     # starts on the Diner, OM-003's venue
    assert decide(conn, campaign_id, r, 0) is None


def test_roaming_is_deterministic_and_near_its_chance(conn, campaign_id):
    seen(conn, campaign_id, "OM-003", "OM-001")
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    chance = roam_chance(r)
    assert chance == min(0.5, 19 * 0.02) and ROAM_RADIUS == 120
    results = [decide(conn, campaign_id, r, s) for s in range(200)]
    assert results == [decide(conn, campaign_id, r, s) for s in range(200)]
    hits = [x for x in results if x]
    assert abs(len(hits) / 200 - chance) <= 0.1
    assert {(h.omen_id, h.node, h.kind) for h in hits} == {("OM-002", "CAR-002", "roaming")}


def test_truncate_recomputes_time_and_geometry(conn):
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    t = truncate(r, "CAR-002")
    assert t.nodes == ["CAR-047", "J-1", "CAR-002"] and t.edges == ["EDGE-1", "EDGE-2"]
    assert t.ticks == 15 and t.minutes == 45 and t.geometry_px == plan_route(conn, "CAR-047", "CAR-002", set()).geometry_px


def test_interruption_is_reproducible_after_restore(conn, campaign_id):
    seen(conn, campaign_id, "OM-003", "OM-001")
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    with transaction(conn):
        sp = create_save_point(conn, campaign_id, "before the walk")

    def walk():
        with transaction(conn):
            conn.execute("UPDATE campaigns SET travel_seq = travel_seq + 1 WHERE id = ?", (campaign_id,))
            seq = conn.execute("SELECT travel_seq FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()[0]
        return seq, decide(conn, campaign_id, r, seq)

    first = [walk() for _ in range(5)]
    restore_save_point(conn, campaign_id, sp)
    assert [walk() for _ in range(5)] == first
