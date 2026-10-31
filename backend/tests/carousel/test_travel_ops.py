import pytest

from app.db import transaction
from app.rules.engine import ChangeError
from tests.carousel.mini_data import COORDS
from tests.conftest import apply


def seen(conn, cid, *omens):
    with transaction(conn):
        for o in omens:
            conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage) VALUES (?, ?, 'visible')", (cid, o))


def state(conn, cid):
    c = conn.execute("SELECT node_id, game_time_minutes, travel_seq, current_location, safe_room FROM campaigns WHERE id = ?", (cid,)).fetchone()
    events = conn.execute("SELECT COUNT(*) FROM events WHERE campaign_id = ?", (cid,)).fetchone()[0]
    return (*tuple(c), events)


def known(conn, cid):
    return {r[0] for r in conn.execute("SELECT place_id FROM known_places WHERE campaign_id = ?", (cid,))}


def stand(conn, cid, node):
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id = ? WHERE id = ?", (node, cid))


def test_travel_square_to_lodge(conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-002", "OM-003")
    with transaction(conn):
        conn.execute("UPDATE campaigns SET safe_room = 0 WHERE id = ?", (campaign_id,))
    r = apply(conn, campaign_id, {"op": "travel", "to": "CAR-047", "reason": "head home"})
    node, minutes, seq, where, safe, _ = state(conn, campaign_id)
    assert (node, minutes, seq, where, safe) == ("CAR-047", 600 + 45, 1, "Dyer's Lodge", 1)
    assert r["arrived"] == "CAR-047" and r["interrupted"] is None and r["minutes"] == 45 and r["geometry_px"]
    assert "CAR-047" in known(conn, campaign_id)
    ev = conn.execute("SELECT payload_json, visibility FROM events WHERE campaign_id = ? AND type = 'travel'", (campaign_id,)).fetchone()
    assert ev["visibility"] == "player" and '"route_nodes": ["CAR-002", "J-1", "CAR-047"]' in ev["payload_json"]


def test_arrival_and_sighting_coincide_at_the_farm(conn, campaign_id):
    seen(conn, campaign_id, "OM-002", "OM-003")
    r = apply(conn, campaign_id, {"op": "travel", "to": "CAR-086"})
    assert r["arrived"] == "CAR-086" and r["interrupted"]["omen_id"] == "OM-001"
    assert r["interrupted"]["description"] == "A scarecrow facing the wrong way"
    assert {"CAR-086"} <= known(conn, campaign_id)
    assert state(conn, campaign_id)[3] == "Farm" and state(conn, campaign_id)[4] == 0


def test_fixed_omen_interrupts_lodge_to_diner(conn, campaign_id):
    seen(conn, campaign_id, "OM-001")       # OM-002 (roaming) stays unseen: the fixed Omen takes precedence
    stand(conn, campaign_id, "CAR-047")
    r = apply(conn, campaign_id, {"op": "travel", "to": "CAR-006"})
    assert r["interrupted"]["omen_id"] == "OM-003" and r["arrived"] == "CAR-006"
    row = conn.execute("SELECT stage, seen_at_node FROM omen_states WHERE campaign_id = ? AND omen_id = 'OM-003'", (campaign_id,)).fetchone()
    assert tuple(row) == ("visible", "CAR-006")
    assert conn.execute("SELECT COUNT(*) FROM events WHERE campaign_id = ? AND type = 'omen_sighted'", (campaign_id,)).fetchone()[0] == 1


def test_roaming_interruption_stops_short(conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-003")
    stand(conn, campaign_id, "CAR-047")
    from app.carousel.interruptions import decide
    from app.carousel.travel import plan_route
    route = plan_route(conn, "CAR-047", "CAR-006", set())
    seq = next(s for s in range(1, 200) if decide(conn, campaign_id, route, s))   # the first travel number that meets OM-002
    with transaction(conn):
        conn.execute("UPDATE campaigns SET travel_seq = ? WHERE id = ?", (seq - 1, campaign_id))
    r = apply(conn, campaign_id, {"op": "travel", "to": "CAR-006"})
    assert r["interrupted"]["omen_id"] == "OM-002" and r["arrived"] == "CAR-002" and r["minutes"] == 45


@pytest.mark.parametrize("change, reason", [({"to": "CAR-002"}, "already here"),
                                            ({"to": "CAR-099"}, "no route without: akers_story"),
                                            ({"to": "CAR-404"}, "no route"),
                                            ({"north": 100, "west": 900}, "no road there")])
def test_refusals_change_nothing(conn, campaign_id, change, reason):
    before = state(conn, campaign_id)
    with pytest.raises(ChangeError, match=reason):
        apply(conn, campaign_id, {"op": "travel", **change})
    assert state(conn, campaign_id) == before


def test_travel_by_coordinates_snaps_to_a_node(conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-002", "OM-003")
    n, w = COORDS["CAR-006"]
    assert apply(conn, campaign_id, {"op": "travel", "north": n + 3, "west": w - 2})["arrived"] == "CAR-006"


def test_junction_location_names_the_nearest_known_place(conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-002", "OM-003")
    apply(conn, campaign_id, {"op": "travel", "north": COORDS["J-1"][0], "west": COORDS["J-1"][1]})
    assert state(conn, campaign_id)[0] == "J-1" and state(conn, campaign_id)[3] == "on the road near Town Square"


def test_story_flag_opens_the_secret_place(conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-002", "OM-003")
    apply(conn, campaign_id, {"op": "set_flag", "flag": "akers_story", "reason": "the Akers story opened the gate"})
    ev = conn.execute("SELECT visibility FROM events WHERE campaign_id = ? AND type = 'flag_set'", (campaign_id,)).fetchone()
    assert ev["visibility"] == "gm"
    assert apply(conn, campaign_id, {"op": "travel", "to": "CAR-099"})["arrived"] == "CAR-099"
    apply(conn, campaign_id, {"op": "clear_flag", "flag": "akers_story", "reason": "the gate shut"})
    assert conn.execute("SELECT COUNT(*) FROM campaign_flags WHERE campaign_id = ?", (campaign_id,)).fetchone()[0] == 0


def test_learn_place(conn, campaign_id):
    apply(conn, campaign_id, {"op": "learn_place", "place": "CAR-017", "how": "Adeline mentioned the old diner"})
    assert "CAR-016" in known(conn, campaign_id)
    with pytest.raises(ChangeError, match="unknown place"):
        apply(conn, campaign_id, {"op": "learn_place", "place": "CAR-404", "how": "rumour"})


def test_prompt_tells_the_gm_to_travel():
    from app.gm import prompts
    text = "\n".join(str(v) for v in vars(prompts).values() if isinstance(v, str))
    assert "use apply_changes op travel when the group goes somewhere" in text
