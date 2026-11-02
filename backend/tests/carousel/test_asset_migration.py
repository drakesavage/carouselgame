from app.carousel.migrate_assets import backfill_map_positions
from app.db import transaction


def test_legacy_position_uses_existing_named_location_without_advancing_time(conn, campaign_id):
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id=NULL WHERE id=?", (campaign_id,))
        conn.execute("DELETE FROM known_places WHERE campaign_id=?", (campaign_id,))
    before = conn.execute("SELECT current_location,game_time_minutes FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    assert backfill_map_positions(conn) == [campaign_id]
    after = conn.execute("SELECT current_location,game_time_minutes,node_id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    assert tuple(after[:2]) == tuple(before)
    assert after[2] == "CAR-002"
    assert backfill_map_positions(conn) == []


def test_unknown_or_gated_location_is_not_guessed(conn, campaign_id):
    with transaction(conn):
        conn.execute("UPDATE campaigns SET node_id=NULL,current_location='UNKNOWNPLACE-HOTEL-8' WHERE id=?", (campaign_id,))
    assert backfill_map_positions(conn) == []
    assert conn.execute("SELECT node_id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()[0] is None
