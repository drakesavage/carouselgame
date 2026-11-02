"""Plan 8 Task 4: a place dossier for known places only; unseen Omens, unmet residents and spoiler text stay out."""
from app.db import transaction
from tests.carousel.test_map_api import seen, travel
from tests.carousel.test_rewards import conclude, running  # noqa: F401  (fixture)
from tests.conftest import apply


def dossier(client, cid, pid):
    return client.get(f"/api/campaigns/{cid}/places/{pid}")


def know(conn, cid, pid):
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO known_places (campaign_id, place_id, how, known_at_minutes) VALUES (?,?,?,?)", (cid, pid, "told", 600))


def test_unknown_and_made_up_places_look_the_same(client, campaign_id):
    unknown, made_up = dossier(client, campaign_id, "CAR-099"), dossier(client, campaign_id, "CAR-NOPE")
    assert unknown.status_code == made_up.status_code == 404
    assert unknown.json() == made_up.json() == {"detail": "unknown place"}
    assert "UNKNOWNPLACE" not in unknown.text


def test_a_known_place_counts_visits(client, conn, campaign_id):
    seen(conn, campaign_id, "OM-001", "OM-002", "OM-003")
    d = dossier(client, campaign_id, "CAR-047").json()
    assert (d["name"], d["category"], d["known"]["how"], d["safe"], d["visits"]["count"]) == ("Dyer's Lodge", "Lodge", "start", True, 0)
    assert travel(client, campaign_id, to="CAR-047").status_code == 200
    assert travel(client, campaign_id, to="CAR-002").status_code == 200
    d = dossier(client, campaign_id, "CAR-047").json()
    assert d["visits"]["count"] == 1 and d["visits"]["last_minutes"] is not None


def test_spoiler_places_never_serve_their_description(client, conn, campaign_id):
    assert dossier(client, campaign_id, "CAR-002").json()["description"] == "A square of shops round a bandstand."
    know(conn, campaign_id, "CAR-099")
    r = dossier(client, campaign_id, "CAR-099")
    assert r.status_code == 200 and r.json()["description"] is None and "SPOILERPLACE" not in r.text


def test_only_sighted_omens_are_listed(client, conn, campaign_id):
    assert dossier(client, campaign_id, "CAR-006").json()["omens"] == []
    apply(conn, campaign_id, {"op": "sight_omen", "omen": "OM-003", "how": "seen"})
    omens = dossier(client, campaign_id, "CAR-006").json()["omens"]
    assert [(o["id"], o["description"], o["stage"]) for o in omens] == [("OM-003", "A chair knocked over", "visible")]
    assert dossier(client, campaign_id, "CAR-002").json()["omens"] == []   # OM-002 roams the square, unseen


def test_unmet_residents_stay_out_of_people(client, conn, campaign_id):
    def last_seen_here():
        with transaction(conn):
            conn.execute("""UPDATE npc_profiles SET last_seen = 'Town Square at day 1 evening'
                            WHERE character_id IN (SELECT id FROM characters WHERE campaign_id = ? AND name = 'Valerie Choi')""", (campaign_id,))
    last_seen_here()
    r = dossier(client, campaign_id, "CAR-002")
    assert r.json()["people"] == [] and "Valerie" not in r.text
    apply(conn, campaign_id, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    last_seen_here()
    assert dossier(client, campaign_id, "CAR-002").json()["people"] == ["Valerie Choi"]


def test_concluded_stories_show_title_and_ending_only(client, conn, campaign_id, running):
    know(conn, campaign_id, "CAR-086")
    assert dossier(client, campaign_id, "CAR-086").json()["stories"] == []
    conclude(conn, campaign_id)
    r = dossier(client, campaign_id, "CAR-086")
    assert [(s["title"], s["ending"]) for s in r.json()["stories"]] == [("Mini OM-001", "defeat")]
    for secret in ("MINI-SEALED-TRUTH", "MINI-OBLIGATION", "MINI-COUNTERPLAY", "MINI-CLUE"):
        assert secret not in r.text


def test_an_alias_opens_its_place(client, conn, campaign_id):
    assert dossier(client, campaign_id, "CAR-017").status_code == 404   # the old diner isn't known yet
    know(conn, campaign_id, "CAR-016")
    assert dossier(client, campaign_id, "CAR-017").json()["id"] == "CAR-016"
