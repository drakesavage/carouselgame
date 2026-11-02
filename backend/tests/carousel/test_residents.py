"""Plan 7 Task 1: every campaign holds all the Camp Dyer residents, unmet and unseen until introduced."""
import json

import pytest

from app.campaigns import create, new_group
from app.carousel import residents
from app.db import transaction
from app.rules.engine import ChangeError
from tests.conftest import apply


@pytest.fixture
def cid(conn):
    return create(conn, name="Residents", character="Mara")


def resident(conn, cid, rid):
    r = conn.execute("""SELECT c.*, p.met, p.personality, p.ref_id FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                        WHERE c.campaign_id = ? AND p.ref_id = ?""", (cid, rid)).fetchone()
    return dict(r) if r else None


def test_create_imports_every_resident_unmet(conn, cid):
    rows = conn.execute("""SELECT c.name, c.kind, c.roster_category, c.status, c.in_party, c.bio, c.provenance, p.met, p.ref_id
                           FROM characters c JOIN npc_profiles p ON p.character_id = c.id WHERE c.campaign_id = ? ORDER BY p.ref_id""",
                        (cid,)).fetchall()
    assert [tuple(r) for r in rows] == [
        ("Adeline Winter", "npc", "allied_npcs", "active", 0, None, "resident CAR-NPC-001", 0, "CAR-NPC-001"),
        ("Valerie Choi", "npc", "allied_npcs", "active", 0, "Wiry, in a patched field jacket.", "resident CAR-NPC-003", 0, "CAR-NPC-003")]
    sheets = {r[0]: r[1] for r in conn.execute("SELECT subject, content FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet'", (cid,))}
    assert "MINI-NPC-SECRET-2" in sheets["valerie-choi"] and "MINI-NPC-SECRET" in sheets["adeline-winter"]
    [public] = conn.execute("SELECT content FROM gm_secrets WHERE campaign_id = ? AND kind = 'resident_public' AND subject = 'valerie-choi'",
                            (cid,)).fetchall()
    assert json.loads(public[0]) == ["Knows the MINI-INTRO-LINE trails past the boathouse."]


def test_ensure_is_idempotent_and_adds_new_templates(conn, cid):
    with transaction(conn):
        assert residents.ensure(conn, cid) == 0
    ref_path = conn.execute("PRAGMA database_list").fetchall()
    path = next(r[2] for r in ref_path if r[1] == "ref")
    import sqlite3
    ref = sqlite3.connect(path)
    ref.execute("""INSERT INTO npc_templates (id, name, provenance, canon_appearance, status, camp_role, usual_location, player_json, gm_json, raw_json)
                   VALUES ('CAR-NPC-077', 'New Arrival', 'original_adaptation', 0, 'alive', 'Cook', 'Kitchen', '{}', '{}', '{}')""")
    ref.commit()
    try:
        with transaction(conn):
            assert residents.ensure(conn, cid) == 1
        assert resident(conn, cid, "CAR-NPC-077")["met"] == 0
    finally:
        ref.execute("DELETE FROM npc_templates WHERE id = 'CAR-NPC-077'")
        ref.commit()
        ref.close()


def test_unmet_residents_stay_out_of_player_views(client, conn):
    cid = create(conn, name="Unseen", character="Mara")
    for url in (f"/api/campaigns/{cid}", f"/api/campaigns/{cid}/characters", f"/api/campaigns/{cid}/npcs", "/api/campaigns",
                f"/api/campaigns/{cid}/journal", f"/api/campaigns/{cid}/search?q=Valerie Adeline"):
        text = client.get(url).text
        for s in ("Valerie", "Adeline", "MINI-INTRO-LINE", "Wiry, in a patched"):
            assert s not in text, f"{s} leaked via {url}"
    vid = resident(conn, cid, "CAR-NPC-003")["id"]
    assert client.get(f"/api/campaigns/{cid}/characters/{vid}").status_code == 404
    assert client.get(f"/api/campaigns/{cid}/characters/{vid}/dossier").status_code == 404


def test_introduce_meets_the_existing_row(conn, cid):
    before = resident(conn, cid, "CAR-NPC-003")
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003", "in_party": True})
    after = resident(conn, cid, "CAR-NPC-003")
    assert (after["id"], after["met"], after["in_party"]) == (before["id"], 1, 1)
    assert conn.execute("SELECT COUNT(*) FROM npc_profiles WHERE ref_id = 'CAR-NPC-003'").fetchone()[0] == 1
    [ev] = conn.execute("SELECT summary, visibility FROM events WHERE campaign_id = ? AND type = 'npc_met'", (cid,)).fetchall()
    assert tuple(ev) == ("Met Valerie Choi, Scout", "player")
    facts = conn.execute("SELECT subject, fact, visibility FROM lore_facts WHERE campaign_id = ? AND subject = 'Valerie Choi'", (cid,)).fetchall()
    assert [tuple(f) for f in facts] == [("Valerie Choi", "Knows the MINI-INTRO-LINE trails past the boathouse.", "player")]
    with pytest.raises(ChangeError, match="already met"):
        apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-003"})


def test_introduce_imports_the_residents_when_the_campaign_lacks_them(conn, campaign_id):
    """The seeded test campaign has a hand-made Adeline (met) and no Valerie: introducing Valerie brings her in."""
    apply(conn, campaign_id, {"op": "introduce_resident", "resident": "CAR-NPC-003"})
    assert resident(conn, campaign_id, "CAR-NPC-003")["met"] == 1
    assert resident(conn, campaign_id, "CAR-NPC-001")["slug"] == "adeline"   # the seed's own, not a second Adeline
    with pytest.raises(ChangeError, match="already met"):
        apply(conn, campaign_id, {"op": "introduce_resident", "resident": "CAR-NPC-001"})


def test_relationships_import_gm_only(conn, cid):
    a, v = resident(conn, cid, "CAR-NPC-001")["id"], resident(conn, cid, "CAR-NPC-003")["id"]
    rels = conn.execute("SELECT a_character_id, b_character_id, kind, visibility FROM relationships WHERE campaign_id = ?", (cid,)).fetchall()
    assert [tuple(r) for r in rels] == [(a, v, "camp_leadership", "gm")]
    apply(conn, cid, {"op": "introduce_resident", "resident": "CAR-NPC-001"})
    assert conn.execute("SELECT visibility FROM relationships WHERE campaign_id = ?", (cid,)).fetchone()[0] == "gm"


def test_new_group_unmeets_everyone(conn, campaign_id):
    from tests.carousel.test_fate_end import jonah_dies
    from tests.carousel.test_fate_ops import kill
    from tests.carousel.test_rewards import conclude
    from tests.carousel.test_story_ops import begin, seen
    apply(conn, campaign_id, {"op": "introduce_resident", "resident": "CAR-NPC-003", "in_party": True})
    with transaction(conn):
        conn.execute("""INSERT INTO memories (campaign_id, layer, subject, character_id, content, created_at)
                        SELECT ?, 'npc', 'valerie-choi', character_id, 'Valerie remembers the farm.', 'now' FROM npc_profiles WHERE ref_id = 'CAR-NPC-003'""",
                     (campaign_id,))
    seen(conn, campaign_id, "OM-001")
    begin(conn, campaign_id)
    kill(conn, campaign_id)
    jonah_dies(conn, campaign_id)
    conclude(conn, campaign_id)
    new_group(conn, campaign_id, "Nora Quill")
    for rid in ("CAR-NPC-001", "CAR-NPC-003"):
        r = resident(conn, campaign_id, rid)
        assert (r["met"], r["in_party"]) == (0, 0), rid
    assert conn.execute("SELECT content FROM memories WHERE subject = 'valerie-choi'").fetchone()[0] == "Valerie remembers the farm."


def test_real_data_imports_70(real_campaign):
    conn, cid = real_campaign
    got = conn.execute("""SELECT t.provenance, COUNT(*) FROM characters c JOIN npc_profiles p ON p.character_id = c.id
                          JOIN ref.npc_templates t ON t.id = p.ref_id WHERE c.campaign_id = ? AND p.met = 0 GROUP BY t.provenance""",
                       (cid,)).fetchall()
    assert dict(got) == {"canon_expanded": 23, "original_adaptation": 47}
    assert conn.execute("SELECT COUNT(*) FROM gm_secrets WHERE campaign_id = ? AND kind = 'companion_sheet'", (cid,)).fetchone()[0] == 70
    assert conn.execute("SELECT COUNT(*) FROM relationships WHERE campaign_id = ? AND visibility != 'gm'", (cid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM relationships WHERE campaign_id = ?", (cid,)).fetchone()[0] > 0
