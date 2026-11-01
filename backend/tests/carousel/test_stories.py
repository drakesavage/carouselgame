import sqlite3

import pytest

from app.carousel.importer import run_import
from app.carousel.stories import DOSSIER_ENDING, ENDINGS, PHASES, active_story, endings_from, novelty, seal, ticket_can_revise
from app.db import connect, init_db, now_iso, transaction
from app.gm.orchestrator import story_active
from app.memory.snapshots import create_save_point, restore_save_point
from tests.carousel.mini_data import FULL_DOSSIER


def cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def add_story(conn, cid, omen="OM-001", lock="MINI-OM-001", status="active", title="Mini OM-001"):
    return conn.execute("INSERT INTO stories (campaign_id, omen_id, story_lock, title, status, phase) VALUES (?,?,?,?,?,?)",
                        (cid, omen, lock, title, status, "The End" if status == "concluded" else "Party")).lastrowid


def test_schema_has_story_tables(conn):
    assert {"id", "campaign_id", "omen_id", "story_lock", "title", "status", "phase", "pressure", "camera", "version", "choice_action",
            "started_minutes", "ended_minutes", "ending", "ending_reason", "novelty", "started_turn_id", "ended_turn_id"} <= cols(conn, "stories")
    assert {"story_id", "sealed_json", "sealed_sha256", "screenplay_json", "endings_json"} <= cols(conn, "story_packets")
    assert {"story_id", "character_id", "casting", "joined_phase", "screen_moments"} <= cols(conn, "story_cast")
    assert {"id", "story_id", "version", "character_id", "ticket_ref", "scope", "operations_json", "before_json", "after_json",
            "public_summary", "reason", "turn_id"} <= cols(conn, "story_revisions")
    assert {"campaign_id", "story_lock", "element", "source"} <= cols(conn, "story_spoilers")
    assert {"stars", "pending_stat_points", "starless_streak"} <= cols(conn, "characters")
    assert {"character_id", "track_role_id", "marks", "unlocked"} <= cols(conn, "advanced_marks")
    assert {"story_id", "character_id", "track_role_id", "feat"} <= cols(conn, "mark_awards")


def test_one_active_story_per_campaign(conn, campaign_id):
    with transaction(conn):
        add_story(conn, campaign_id)
        add_story(conn, campaign_id, omen="OM-003", lock="MINI-OM-003", status="concluded")   # concluded ones don't count
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            add_story(conn, campaign_id, omen="OM-003", lock="MINI-OM-003")


def test_constants():
    assert PHASES == ("Omen", "Choice", "Party", "First Blood", "Rebirth", "Second Blood", "Finale", "The End")
    assert ENDINGS == ("full_success", "costly_success", "survive_without_victory", "defeat")
    assert DOSSIER_ENDING["survival_only"] == "survive_without_victory" and DOSSIER_ENDING["failure"] == "defeat"


def test_seal_is_stable_and_order_free():
    j, h = seal(FULL_DOSSIER)
    assert seal(FULL_DOSSIER) == (j, h) and len(h) == 64
    assert seal(dict(reversed(list(FULL_DOSSIER.items()))))[1] == h
    assert seal({**FULL_DOSSIER, "title": "other"})[1] != h
    assert "MINI-SEALED-TRUTH" in j


def test_endings_from_maps_names():
    e = endings_from(FULL_DOSSIER)
    assert set(e) == set(ENDINGS)
    assert e["survive_without_victory"]["predicate"] == "Someone escapes the farm."
    assert e["defeat"]["predicate"] == "The farm keeps everyone."


def test_novelty_two_one_zero_and_spoilers(conn, campaign_id):
    assert novelty(conn, campaign_id, "MINI-OM-001") == 2
    with transaction(conn):
        add_story(conn, campaign_id, status="concluded")
    assert novelty(conn, campaign_id, "MINI-OM-001") == 1
    with transaction(conn):
        add_story(conn, campaign_id, status="concluded")
    assert novelty(conn, campaign_id, "MINI-OM-001") == 0
    with transaction(conn):
        conn.execute("INSERT INTO story_spoilers (campaign_id, story_lock, element, source) VALUES (?, 'MINI-OM-003', 'killer', 'wiki')", (campaign_id,))
    assert novelty(conn, campaign_id, "MINI-OM-003") == 1
    assert novelty(conn, campaign_id, "MINI-OM-001") == 0   # never below 0


def test_ticket_can_revise(conn, campaign_id):
    assert ticket_can_revise(conn, campaign_id, "hb.ticket.aspect.soldier.commando")
    assert not ticket_can_revise(conn, campaign_id, "hb.ticket.base.final-girl")
    assert not ticket_can_revise(conn, campaign_id, "trope:1")
    with transaction(conn):
        conn.execute("INSERT INTO rulings (campaign_id, topic, ruling, ticket_ref, review, created_at) VALUES (?,?,?,?,?,?)",
                     (campaign_id, "trope 1", "pending ruling", "trope:1", None, now_iso()))
    assert not ticket_can_revise(conn, campaign_id, "trope:1")
    with transaction(conn):
        conn.execute("INSERT INTO rulings (campaign_id, topic, ruling, ticket_ref, review, created_at) VALUES (?,?,?,?,?,?)",
                     (campaign_id, "trope 1", "it may revise the next scene", "trope:1", "approved", now_iso()))
    assert ticket_can_revise(conn, campaign_id, "trope:1")
    assert not ticket_can_revise(conn, campaign_id, "hb.ticket.no-such")


def test_story_active_follows_the_table(conn, campaign_id):
    assert active_story(conn, campaign_id) is None and story_active(conn, campaign_id) is False
    with transaction(conn):
        sid = add_story(conn, campaign_id)
    assert active_story(conn, campaign_id)["id"] == sid and story_active(conn, campaign_id) is True
    with transaction(conn):
        conn.execute("UPDATE stories SET status = 'concluded' WHERE id = ?", (sid,))
    assert story_active(conn, campaign_id) is False


def test_restore_rolls_back_story_state(conn, campaign_id, pc):
    def counts():
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stories", "story_packets", "story_cast", "story_revisions", "story_spoilers", "advanced_marks", "mark_awards")}
    with transaction(conn):
        sp = create_save_point(conn, campaign_id, "before the story")
    before = counts()
    with transaction(conn):
        sid = add_story(conn, campaign_id)
        conn.execute("INSERT INTO story_packets (story_id, sealed_json, sealed_sha256, screenplay_json, endings_json) VALUES (?, '{}', 'x', '{}', '{}')", (sid,))
        conn.execute("INSERT INTO story_cast (story_id, character_id, casting, joined_phase) VALUES (?, ?, 'lead', 'Party')", (sid, pc))
        conn.execute("""INSERT INTO story_revisions (story_id, version, character_id, ticket_ref, scope, operations_json, before_json, after_json,
                                                     public_summary, reason) VALUES (?, 1, ?, 'hb.ticket.aspect.soldier.commando', 'arc', '[]', '{}', '{}', 's', 'r')""",
                     (sid, pc))
        conn.execute("INSERT INTO story_spoilers (campaign_id, story_lock, element, source) VALUES (?, 'MINI-OM-001', 'killer', 'wiki')", (campaign_id,))
        conn.execute("INSERT INTO advanced_marks (character_id, track_role_id, marks) VALUES (?, 'advanced.adventurer', 3)", (pc,))
        conn.execute("INSERT INTO mark_awards (story_id, character_id, track_role_id, feat) VALUES (?, ?, 'advanced.adventurer', 'led')", (sid, pc))
        conn.execute("UPDATE characters SET stars = 4, pending_stat_points = 1 WHERE id = ?", (pc,))
    restore_save_point(conn, campaign_id, sp)
    assert counts() == before
    assert tuple(conn.execute("SELECT stars, pending_stat_points FROM characters WHERE id = ?", (pc,)).fetchone()) == (0, 0)


def test_restore_brings_back_a_story(conn, campaign_id, pc):
    with transaction(conn):
        sid = add_story(conn, campaign_id)
        conn.execute("INSERT INTO story_packets (story_id, sealed_json, sealed_sha256, screenplay_json, endings_json) VALUES (?, '{}', 'x', '{}', '{}')", (sid,))
        conn.execute("INSERT INTO story_cast (story_id, character_id, casting, joined_phase) VALUES (?, ?, 'lead', 'Party')", (sid, pc))
        sp = create_save_point(conn, campaign_id, "mid-story")
        conn.execute("DELETE FROM story_cast")
        conn.execute("DELETE FROM story_packets")
        conn.execute("UPDATE stories SET status = 'concluded'")
    restore_save_point(conn, campaign_id, sp)
    assert active_story(conn, campaign_id)["id"] == sid
    assert conn.execute("SELECT COUNT(*) FROM story_packets WHERE story_id = ?", (sid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM story_cast WHERE story_id = ?", (sid,)).fetchone()[0] == 1


def test_reimport_protects_story_omens(tmp_path):
    from app.config import Settings
    from tests.carousel.conftest import write_mini_source
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src"), gm_provider="mock")
    s.ensure_dirs()
    c = connect(s.campaign_db)
    init_db(c)
    c.execute("INSERT INTO campaigns (id, name, game_time_minutes, created_at, updated_at) VALUES (1, 'T', 0, ?, ?)", (now_iso(), now_iso()))
    add_story(c, 1, omen="OM-404", lock="GONE", status="concluded")
    c.commit()
    c.close()
    r = run_import(s)
    assert not r.ok and r.missing == {"story_omen": ["OM-404"]}


def test_deleting_a_campaign_removes_its_stories_and_marks(conn, settings, campaign_id, pc):
    from app.campaigns import delete
    from tests.carousel.test_rewards import AWARDS
    from tests.carousel.test_revisions import COMMANDO, revision
    from tests.carousel.test_story_ops import begin, seen
    from tests.conftest import apply
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    apply(conn, campaign_id, {"op": "grant_ticket", "character": "Jonah", "ticket": COMMANDO, "source": "test"})
    apply(conn, campaign_id, {"op": "equip_ticket", "character": "Jonah", "ticket": COMMANDO})
    apply(conn, campaign_id, revision())
    apply(conn, campaign_id, {"op": "award_mark", "character": "Mara", "track": "advanced.adventurer", "feat": "f"})
    apply(conn, campaign_id, {"op": "conclude_story", "ending": "defeat", "reason": "r", "awards": AWARDS})
    apply(conn, campaign_id, {"op": "confirm_spoiler", "story_lock": "MINI-OM-001", "element": "e", "source": "s"})
    with transaction(conn):
        conn.execute("UPDATE turns SET status = 'completed'")
    delete(conn, settings, campaign_id, "Test Town")
    for t in ("stories", "story_packets", "story_cast", "story_revisions", "story_spoilers", "advanced_marks", "mark_awards"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t
