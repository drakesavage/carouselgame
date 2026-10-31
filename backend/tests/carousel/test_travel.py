from app.carousel.coords import to_pixel
from app.carousel.travel import MINUTES_PER_TICK, missing_flags, nearest_node, plan_route
from tests.carousel.mini_data import COORDS


def coords_of(node):
    return COORDS[node]


def test_cheapest_unflagged_route(conn):
    r = plan_route(conn, "CAR-047", "CAR-006", set())
    assert r.nodes == ["CAR-047", "J-1", "CAR-002", "CAR-006"] and r.ticks == 19 and r.minutes == 57
    assert r.edges == ["EDGE-1", "EDGE-2", "EDGE-3"]
    assert r.geometry_px[0] == to_pixel(*coords_of("CAR-047"))
    assert r.geometry_px[-1] == to_pixel(*coords_of("CAR-006"))
    assert MINUTES_PER_TICK == 3


def test_geometry_follows_the_direction_of_travel(conn):
    r = plan_route(conn, "CAR-006", "CAR-047", set())
    assert r.geometry_px[0] == to_pixel(*coords_of("CAR-006")) and r.geometry_px[-1] == to_pixel(*coords_of("CAR-047"))
    assert len(r.geometry_px) == 4   # shared vertices between consecutive edges are not repeated


def test_flagged_edges_and_nodes_need_flags(conn):
    assert plan_route(conn, "CAR-002", "CAR-086", set()).ticks == 17                 # via J-1
    assert plan_route(conn, "CAR-002", "CAR-086", {"shortcut_story"}).ticks == 2
    assert plan_route(conn, "CAR-086", "CAR-002", {"shortcut_story"}).ticks == 17    # directed: no way back on it
    assert plan_route(conn, "CAR-002", "CAR-099", set()) is None                     # destination node requires akers_story
    assert missing_flags(conn, "CAR-002", "CAR-099", set()) == ["akers_story"]
    assert plan_route(conn, "CAR-002", "CAR-099", {"akers_story"}).nodes[-1] == "CAR-099"


def test_alias_destination(conn):
    assert plan_route(conn, "CAR-002", "CAR-017", set()).nodes == ["CAR-002", "CAR-016"]


def test_unknown_place_has_no_route(conn):
    assert plan_route(conn, "CAR-002", "CAR-404", set()) is None
    assert missing_flags(conn, "CAR-002", "CAR-404", set()) == []


def test_nearest_node_snaps_within_25_units(conn):
    assert nearest_node(conn, 482, 458) == "CAR-006"
    assert nearest_node(conn, 560, 445) == "J-1"
    assert nearest_node(conn, 200, 100) is None


def test_real_graph_lodge_to_square(real_ref_conn):
    r = plan_route(real_ref_conn, "CAR-047", "CAR-002", set())
    assert abs(r.ticks - 14.8) < 0.01 and r.minutes == 44
