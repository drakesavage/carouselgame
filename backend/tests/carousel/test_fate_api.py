from tests.carousel.test_fate_end import jonah_dies
from tests.carousel.test_fate_ops import kill
from tests.carousel.test_rescue import to_diner
from tests.carousel.test_rewards import conclude, running  # noqa: F401  (fixture)


def test_fate_endpoint_shows_axes_never_rescue(client, conn, campaign_id, running, pc):
    kill(conn, campaign_id)
    r = client.get(f"/api/campaigns/{campaign_id}/fate")
    assert r.status_code == 200
    body = r.json()
    assert body["deathwatch"] == {"pace": "full"} and body["wiped"] is False
    mara = next(c for c in body["cast"] if c["id"] == pc)
    assert mara == {"id": pc, "name": "Mara Vance", "physical_condition": "dead", "wallpaper_status": "dead",
                    "participation": "observer", "persistent_fate": "ongoing"}
    assert "rescue" not in r.text and "CAUSE-SECRET" not in r.text


def test_character_cards_carry_the_axes_but_not_rescue(client, conn, campaign_id, running):
    kill(conn, campaign_id)
    party = client.get(f"/api/campaigns/{campaign_id}").json()["party"]
    mara = next(c for c in party if c["slug"] == "mara")
    assert (mara["physical_condition"], mara["participation"]) == ("dead", "observer") and "rescue_eligibility" not in mara


def test_pace_needs_deathwatch(client, conn, campaign_id, running):
    url = f"/api/campaigns/{campaign_id}/deathwatch/pace"
    assert client.post(url, json={"pace": "condensed"}).status_code == 409
    kill(conn, campaign_id)
    assert client.post(url, json={"pace": "condensed"}).status_code == 200
    assert conn.execute("SELECT deathwatch_pace FROM stories WHERE id = ?", (running,)).fetchone()[0] == "condensed"
    assert client.get(f"/api/campaigns/{campaign_id}/fate").json()["deathwatch"] == {"pace": "condensed"}
    assert conn.execute("SELECT visibility FROM events WHERE type = 'deathwatch_pace'").fetchone()[0] == "player"


def test_poster_board_needs_a_diner_visit(client, conn, campaign_id, running):
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    url = f"/api/campaigns/{campaign_id}/posters"
    assert client.get(url).json() == []                      # the seed only knows of the Diner ("start"), it hasn't been
    to_diner(conn, campaign_id)
    cards = client.get(url).json()
    assert [c["name"] for c in cards] == ["Mara Vance", "Jonah Reyes"]
    assert set(cards[0]) == {"id", "name", "plot_armor", "archetype", "last_seen", "posted_minutes", "held_by", "found"}
    assert (cards[0]["plot_armor"], cards[0]["archetype"], cards[0]["last_seen"], cards[0]["held_by"], cards[0]["found"]) == \
        (11, "Final Girl", "Town Square", None, False)
    text = client.get(url).text
    assert "CAUSE-SECRET" not in text and "possible" not in text


def test_wiped_flag(client, conn, campaign_id, running):
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    assert client.get(f"/api/campaigns/{campaign_id}/fate").json()["wiped"] is False
    conclude(conn, campaign_id)
    body = client.get(f"/api/campaigns/{campaign_id}/fate").json()
    assert body["wiped"] is True and body["deathwatch"] is None and body["cast"] == []
    assert client.get(f"/api/campaigns/{campaign_id}").json()["fate"]["wiped"] is True
