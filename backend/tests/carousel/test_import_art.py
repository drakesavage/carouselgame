from app.carousel.refdata import place_image
from app.carousel.refdb import count


def test_real_location_art(ref_real):
    assert count(ref_real, "place_images") == 184               # 201 index rows; 17 are provisional PROV-LOC-* art
    assert count(ref_real, "import_report", "category = 'art_unmatched'") == 17
    rel = ref_real.execute("SELECT rel_path FROM place_images WHERE place_id = 'CAR-002' AND kind = 'identity'").fetchone()[0]
    assert rel.endswith(".png") and not rel.startswith("assets/")   # relative to Location_Art_Production/assets (the /location-art mount)


def test_place_image_on_a_campaign_connection(conn):
    assert place_image(conn, "CAR-016") == "identity_images/CAR-016_identity_v001.png"
    assert place_image(conn, "CAR-404") is None


def test_unmatched_art_is_reported(conn):
    got = conn.execute("SELECT item_id FROM ref.import_report WHERE category = 'art_unmatched'").fetchall()
    assert [r[0] for r in got] == ["PROV-LOC-001"]
    assert conn.execute("SELECT 1 FROM ref.place_images WHERE place_id = 'PROV-LOC-001'").fetchone() is None
