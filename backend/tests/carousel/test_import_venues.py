import pytest

from app.carousel import refdb
from app.carousel.import_places import resolve_place
from app.carousel.importer import IMPORTERS
from app.carousel.refdb import count
from app.carousel.sources import load_sources


@pytest.fixture
def ref_mini(mini_source, tmp_path):
    conn = refdb.connect(tmp_path / "reference.db")
    refdb.init(conn)
    files = load_sources(mini_source)
    for fn in IMPORTERS:
        fn(conn, files)
    conn.commit()
    yield conn
    conn.close()


def reports(ref, item_id=None):
    return [r[0] for r in ref.execute("SELECT message FROM import_report WHERE category = 'venues' AND (? IS NULL OR item_id = ?)",
                                      (item_id, item_id))]


def test_enterable_venue_has_entry_and_exit(ref_mini):
    v = ref_mini.execute("SELECT * FROM venues WHERE id = 'SM-T01'").fetchone()
    assert (v["enterable"], v["entry_zone"], v["exit_zone"]) == (1, "SM-T01:n01_farm_gate", "SM-T01:n02_barn")
    assert count(ref_mini, "venue_zones", "venue_id = 'SM-T01'") == 4
    assert ref_mini.execute("SELECT venue_id FROM venue_places WHERE place_id = 'CAR-086'").fetchone()[0] == "SM-T01"


def test_shortcut_is_skipped_and_reported(ref_mini):
    links = {(r["a"], r["b"]) for r in ref_mini.execute("SELECT a, b FROM venue_links WHERE venue_id = 'SM-T01'")}
    assert len(links) == 3
    assert ("SM-T01:n01_farm_gate", "SM-T01:n04_MINI-HIDDEN-ROOM") not in links
    assert all(a < b for a, b in links)
    assert any("1 proposed optional shortcut" in m for m in reports(ref_mini))


def test_zone_placement_rule(ref_mini):
    placed = dict(ref_mini.execute("SELECT omen_id, placed FROM venue_omens WHERE venue_id = 'SM-T01'").fetchall())
    assert placed == {"OM-005": 1, "OM-001": 0}
    z = ref_mini.execute("SELECT zone_id, choice_action FROM venue_omens WHERE omen_id = 'OM-005'").fetchone()
    assert tuple(z) == ("SM-T01:n03_cellar_door", "open:cellar_door")


def test_not_enterable_venues_are_reported(ref_mini):
    got = dict(ref_mini.execute("SELECT id, enterable FROM venues WHERE id IN ('SM-T02', 'SM-T03')").fetchall())
    assert got == {"SM-T02": 0, "SM-T03": 0}
    assert any("story_instance" in m for m in reports(ref_mini, "SM-T02"))
    assert any("no member place on the travel graph" in m for m in reports(ref_mini, "SM-T03"))


def test_real_venue_counts(ref_real):
    assert count(ref_real, "venues") == 108
    assert count(ref_real, "venue_zones") == 802  # Five corrected floor graphs; old IDs retained.
    assert count(ref_real, "venue_links") == 659
    assert ref_real.execute("SELECT COUNT(*) FROM venue_links WHERE source_lock IS NOT NULL").fetchone()[0] == 38


def test_real_venue_places_resolve(ref_real):
    for (pid,) in ref_real.execute("SELECT place_id FROM venue_places"):
        assert resolve_place(ref_real, pid) is not None, pid


def test_real_placed_omens_are_fixed_and_in_a_member_place(ref_real):
    rows = ref_real.execute("""SELECT vo.omen_id, o.spawn_mode, o.place_id, vo.venue_id FROM venue_omens vo
                               JOIN omens o ON o.id = vo.omen_id WHERE vo.placed = 1""").fetchall()
    assert rows
    for omen_id, mode, place, venue in rows:
        members = {resolve_place(ref_real, r[0]) for r in ref_real.execute("SELECT place_id FROM venue_places WHERE venue_id = ?", (venue,))}
        assert mode == "fixed" and place in members, omen_id


def test_real_operator_only_choice_is_retained(ref_real):
    expected = "take_box_to_geist_house_and_set_up_seance_deck"
    assert ref_real.execute("SELECT choice_trigger FROM omens WHERE id = 'OM-110'").fetchone()[0] == expected
    assert ref_real.execute("SELECT choice_action FROM venue_omens WHERE omen_id = 'OM-110'").fetchone()[0] == expected
    assert not any("gives Choice" in m for m in reports(ref_real, "OM-110"))
