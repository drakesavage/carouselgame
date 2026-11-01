import json

from app.carousel.refdb import count


def playable(ref, where="1=1"):
    return count(ref, "omens", f"record_type = 'Playable Omen' AND {where}")


def test_playable_omen_resolution(ref_real):
    assert playable(ref_real) == 93
    assert playable(ref_real, "anchor_method = 'gazetteer_id'") == 39
    assert playable(ref_real, "anchor_method = 'anchor_name'") == 54
    assert playable(ref_real, "place_id IS NULL") == 0
    assert count(ref_real, "import_report", "category = 'omen_unplaced'") == 0


def test_every_record_type_is_kept(ref_real):
    got = dict(ref_real.execute("SELECT record_type, COUNT(*) FROM omens GROUP BY 1").fetchall())
    assert got == {"Playable Omen": 93, "Hub / generator": 11, "Related cue / route": 4, "Not an Omen": 3}
    assert count(ref_real, "hub_seed_pools") == 11


def test_spawn_modes(ref_real):
    got = dict(ref_real.execute("SELECT spawn_mode, COUNT(*) FROM omens WHERE record_type = 'Playable Omen' GROUP BY 1").fetchall())
    assert got == {"fixed": 49, "roaming": 24, "item": 15, "special": 5}


def test_area_placement(ref_real):
    assert playable(ref_real, "placement = 'area'") == 45
    assert ref_real.execute("SELECT placement FROM omens WHERE id = 'OM-001'").fetchone()[0] == "place"


def test_choice_trigger_and_beats(ref_real):
    trigger, beats = ref_real.execute("SELECT choice_trigger, beats_json FROM omens WHERE id = 'OM-001'").fetchone()
    assert trigger == "cross_threshold:corn_maze_entrance"
    assert set(json.loads(beats)) == {"hook", "escalation", "climax", "ending_requirement"}


def test_no_map_xy(ref_real):
    cols = {r[1] for r in ref_real.execute("PRAGMA table_info(omens)")}
    assert not any("map_x" in c or "map_y" in c for c in cols)
    assert all("Map X / 1000" not in r[0] and "Map Y / 1000" not in r[0] for r in ref_real.execute("SELECT raw_json FROM omens"))


def test_anchor_normalisation(ref_real):
    oid = ref_real.execute("SELECT id FROM omens WHERE raw_json LIKE '%Berryman%Dive%' AND record_type = 'Playable Omen'").fetchone()[0]
    assert ref_real.execute("SELECT place_id FROM omens WHERE id = ?", (oid,)).fetchone()[0] is not None


def test_every_playable_omen_has_beats_and_its_truth_is_sealed(ref_real):
    assert count(ref_real, "omen_beats") == 93
    dossier, sealed = ref_real.execute("SELECT dossier_json, sealed_json FROM omen_beats WHERE omen_id = 'OM-001'").fetchone()
    assert "Benny" in sealed and "Benny" not in dossier          # the killer's identity is GM-only
    assert json.loads(dossier)["player_visible_premise"].startswith("A corn maze")
