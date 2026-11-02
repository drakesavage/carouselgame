import pytest


@pytest.mark.parametrize("minutes, expected", [
    (0, {"day": 1, "slot": "morning", "time": "08:00"}),
    (840, {"day": 1, "slot": "night", "time": "22:00"}),
    (960, {"day": 2, "slot": "night", "time": "00:00"}),
])
def test_campaign_summary_carries_the_clock(client, conn, campaign_id, minutes, expected):
    conn.execute("UPDATE campaigns SET game_time_minutes = ? WHERE id = ?", (minutes, campaign_id))
    conn.commit()
    assert client.get(f"/api/campaigns/{campaign_id}").json()["clock"] == expected
