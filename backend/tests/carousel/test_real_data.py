"""A smoke run on the real data: a fresh campaign over a copy of the installed data/reference.db, with the mock GM (no
model calls), through the opening, travel to the safe places, every enterable venue, every playable Omen, a day of
resident schedules and the player-visibility crawl. Skipped when E:\\Carousel or data/reference.db is missing."""
import collections
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.campaigns import create
from app.carousel import opening, residents, venues
from app.carousel.refdata import open_campaign
from app.carousel.sources import SOURCE_FILES
from app.config import Settings
from app.db import init_db
from app.memory.context import build_context
from tests.carousel.conftest import REAL_SOURCE
from tests.conftest import apply

REAL_DB = Settings().reference_db
SAFE_PLACES = ("CAR-046", "CAR-047", "CAR-002", "CAR-006")
BOWLING_ALLEY = "AUD-LOC-BOWLING-ALLEY"
BRIEFING_BUDGET = 8000   # Plan 7: the whole briefing with all 70 residents in the campaign


@pytest.fixture
def real(tmp_path):
    if not (REAL_SOURCE / SOURCE_FILES["gazetteer"]).exists() or not REAL_DB.exists():
        pytest.skip("Carousel source packages or data/reference.db not present")
    s = Settings(data_dir=tmp_path / "data", gm_provider="mock")
    s.ensure_dirs()
    shutil.copy(REAL_DB, s.reference_db)
    conn = open_campaign(s)
    init_db(conn)
    cid = create(conn, name="Smoke run", character="Mara")
    yield s, conn, cid
    conn.close()


def here(conn, cid):
    return dict(conn.execute("SELECT node_id, venue_id, zone_id, opening_stage FROM campaigns WHERE id = ?", (cid,)).fetchone())


def travel_to(conn, cid, place, tries=8):
    """Travel until arrival: an Omen on the way stops the group short, and is seen by then."""
    for _ in range(tries):
        r = apply(conn, cid, {"op": "travel", "to": place})
        if r["arrived"] == place:
            return r
    raise AssertionError(f"never reached {place}")


def through_the_opening(conn, cid):
    from tests.carousel.test_silas import say
    apply(conn, cid, {"op": "set_lure", "kind": "event", "belief": "A free weekend at the county fair", "truth": "The fair is bait."})
    for name in ("Jonah", "Priya", "Theo"):
        apply(conn, cid, {"op": "npc", "name": name, "in_party": True, "description": "A friend in the car."})
    say(conn, cid, "player", "Are we there yet, seriously?")
    apply(conn, cid, {"op": "advance_opening", "to": "silas"})
    role = conn.execute("SELECT r.id FROM ref.roles r JOIN ref.start_profiles p ON p.role_id = r.id WHERE r.kind = 'base' ORDER BY r.id").fetchone()[0]
    for who in ("Mara", "Jonah", "Priya", "Theo"):
        apply(conn, cid, {"op": "silas_assigns", "character": who, "role": role, "reason": "how they talked in the car",
                          "evidence": ["Are we there yet, seriously?"], "contextual_ticket": "hb.ticket.starter.shared.quick-thinker",
                          "contextual_reason": "quick on the uptake"})
    apply(conn, cid, {"op": "advance_opening", "to": "opening_omen"})
    apply(conn, cid, {"op": "advance_opening", "to": "escort", "reason": "the player walked past every Omen"})
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-001", "in_party": True})   # the veteran who walks them in
    travel_to(conn, cid, opening.LODGE)
    apply(conn, cid, {"op": "pay_off_lure", "how": "The fair was a flyer Silas printed."})
    apply(conn, cid, {"op": "advance_opening", "to": "done"})


def test_the_opening_runs_on_the_real_town(real):
    _, conn, cid = real
    unmet = conn.execute("""SELECT COUNT(*) FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                            WHERE c.campaign_id = ? AND p.met = 0""", (cid,)).fetchone()[0]
    assert unmet == 70
    assert here(conn, cid)["node_id"] == opening.ARRIVAL_LOT and here(conn, cid)["opening_stage"] == "car"
    through_the_opening(conn, cid)
    h = here(conn, cid)
    assert (h["node_id"], h["opening_stage"]) == (opening.LODGE, "done")


def test_travel_reaches_the_safe_places_and_the_bowling_alley(real):
    _, conn, cid = real
    through_the_opening(conn, cid)
    for place in (*SAFE_PLACES, BOWLING_ALLEY):
        travel_to(conn, cid, place)
        h = here(conn, cid)
        v = venues.venue_at(conn, place)
        assert h["node_id"] == place
        assert (h["venue_id"], h["zone_id"]) == ((v["id"], v["entry_zone"]) if v else (None, None)), place


def test_every_enterable_venue_is_entered_at_its_entry_and_has_no_orphan_rooms(real):
    _, conn, cid = real
    entered = 0
    for v in conn.execute("SELECT * FROM ref.venues WHERE enterable = 1 ORDER BY id").fetchall():
        node = conn.execute("""SELECT g.id FROM ref.graph_nodes g JOIN ref.venue_places vp
                               ON COALESCE((SELECT target_id FROM ref.place_aliases WHERE alias_id = vp.place_id), vp.place_id) = g.location_id
                               WHERE vp.venue_id = ? ORDER BY g.id LIMIT 1""", (v["id"],)).fetchone()
        assert node, f"{v['id']} is enterable with no member node"
        at = venues.enter(conn, cid, node[0])
        if at["venue_id"] == v["id"]:   # a node shared by two venues enters the first; the other is still checked for orphans
            assert at["zone_id"] == v["entry_zone"]
            entered += 1
        zones = {r[0] for r in conn.execute("SELECT id FROM ref.venue_zones WHERE venue_id = ? AND area_type != 'retired_reference'", (v["id"],))}
        links = collections.defaultdict(set)
        for a, b in conn.execute("SELECT a, b FROM ref.venue_links WHERE venue_id = ?", (v["id"],)):
            links[a].add(b)
            links[b].add(a)
        seen, todo = {v["entry_zone"]}, [v["entry_zone"]]
        while todo:
            for n in links[todo.pop()] - seen:
                seen.add(n)
                todo.append(n)
        assert not zones - seen, f"{v['id']}: rooms out of reach of {v['entry_zone']}: {sorted(zones - seen)}"
    assert entered


def omen_buckets(conn) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"engine": [], "gm_lead": [], "story_venue": [], "track_b": []}
    for o in conn.execute("""SELECT o.id, o.spawn_mode, p.north,
                                    EXISTS (SELECT 1 FROM ref.venue_omens v WHERE v.omen_id = o.id AND v.placed = 1) AS zone_placed,
                                    EXISTS (SELECT 1 FROM ref.venue_omens v JOIN ref.venues w ON w.id = v.venue_id
                                            WHERE v.omen_id = o.id AND w.layer IN ('story_instance', 'extradimensional')) AS in_story_venue
                             FROM ref.omens o LEFT JOIN ref.places p ON p.id = o.place_id
                             WHERE o.record_type = 'Playable Omen' ORDER BY o.id"""):
        placed = o["north"] is not None
        if o["zone_placed"] or (placed and o["spawn_mode"] in ("fixed", "roaming")):
            out["engine"].append(o["id"])        # interruptions.py or a venue zone sights it
        elif placed and o["spawn_mode"] in ("item", "special"):
            out["gm_lead"].append(o["id"])       # memory/context.py OMEN LEADS
        elif o["in_story_venue"]:
            out["story_venue"].append(o["id"])   # reached through its story venue (rules/story_venue_ops.py)
        elif not placed:
            out["track_b"].append(o["id"])       # waits for a parcel in E:\Carousel
    return out


def test_every_playable_omen_reaches_play_or_waits_on_track_b(real):
    _, conn, cid = real
    b = omen_buckets(conn)
    print("\nOmen buckets: " + ", ".join(f"{k} {len(v)}" for k, v in b.items()) + f"\nTrack B (no parcel): {', '.join(b['track_b'])}")
    total = conn.execute("SELECT COUNT(*) FROM ref.omens WHERE record_type = 'Playable Omen'").fetchone()[0]
    assert sum(len(v) for v in b.values()) == total == 93   # every Omen in exactly one bucket


def test_residents_keep_a_place_through_a_day_and_the_briefing_stays_small(real):
    _, conn, cid = real
    through_the_opening(conn, cid)
    ids = [r[0] for r in conn.execute("""SELECT c.id FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                                         WHERE c.campaign_id = ? AND p.ref_id LIKE 'CAR-NPC-%'""", (cid,))]
    assert len(ids) == 70
    for step in range(4):
        apply(conn, cid, {"op": "advance_time", "minutes": 360, "reason": "a quiet stretch at camp"})
        lost = [i for i in ids if not (residents.where(conn, cid, i) or {}).get("place_id")]
        assert not lost, f"step {step}: {len(lost)} residents have no place"
        size = len(build_context(conn, cid, "I look around")["text"])
        assert size < BRIEFING_BUDGET, f"step {step}: briefing {size} characters"


def test_player_endpoints_answer_and_hold_back_resident_secrets(real):
    from app.gm.orchestrator import TurnService
    from app.gm.providers import MockProvider
    from app.main import create_app
    from tests.test_visibility import _player_urls
    s, conn, cid = real
    through_the_opening(conn, cid)
    secrets = [json.loads(g)["gm_only"]["canonical_secret"] for (g,) in
               conn.execute("SELECT gm_json FROM ref.npc_templates ORDER BY id LIMIT 5")]
    app = create_app(s, bootstrap=False, local_hosts={"testclient"})
    app.state.crawler.turns = TurnService(s, MockProvider, run_async=False)
    with TestClient(app) as client:
        for url in _player_urls(client, cid):
            r = client.get(url)
            assert r.status_code == 200, url
            leaked = [x for x in secrets if x[:60] in r.text]
            assert not leaked, f"{url} shows a resident's GM-only secret"
