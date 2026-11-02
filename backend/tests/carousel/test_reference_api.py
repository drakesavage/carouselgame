import json

import pytest
from fastapi.testclient import TestClient

from app.carousel.importer import run_import
from app.config import Settings
from app.main import create_app
from tests.carousel.conftest import write_mini_source


@pytest.fixture
def ref_settings(tmp_path):
    s = Settings(data_dir=tmp_path / "data", source_dir=write_mini_source(tmp_path / "src"), gm_provider="mock", portrait_engine="comfy")
    s.ensure_dirs()
    return s


@pytest.fixture
def client_ref(ref_settings):
    assert run_import(ref_settings).ok
    with TestClient(create_app(ref_settings, bootstrap=False, local_hosts={"testclient"})) as tc:
        yield tc


def gm(settings):
    return {"X-GM-Token": settings.gm_token()}


def test_status_has_counts_and_hashes_but_no_content(client_ref):
    body = client_ref.get("/api/reference/status").json()
    assert body["imported"] is True and body["counts"]["places"] == 9 and body["counts"]["npc_templates"] == 2
    assert set(body["sources"]) >= {"gazetteer", "omens", "npcs"} and all(len(v) == 12 for v in body["sources"].values())
    text = json.dumps(body)
    assert "MINI-SEALED-TRUTH" not in text and "MINI-NPC-SECRET" not in text and "gm_json" not in text


def test_status_before_any_import(ref_settings):
    with TestClient(create_app(ref_settings, bootstrap=False, local_hosts={"testclient"})) as tc:
        assert tc.get("/api/reference/status").json() == {"imported": False}
        assert tc.get("/api/gm/reference/places", headers=gm(ref_settings)).status_code == 404


def test_gm_reference_requires_token(client_ref, ref_settings):
    assert client_ref.get("/api/gm/reference/omens").status_code == 403
    assert client_ref.get("/api/gm/reference/omens", headers={"X-GM-Token": "wrong"}).status_code == 403
    omens = client_ref.get("/api/gm/reference/omens", headers=gm(ref_settings)).json()
    assert [o["id"] for o in omens] == ["OM-001", "OM-002", "OM-003", "OM-005", "OM-009", "OM-010", "OM-011", "OM-012"]   # every record type, hubs included


def test_asset_audit_remains_director_only(client_ref, ref_settings):
    assert client_ref.get("/api/gm/assets/audit").status_code == 403
    body = client_ref.get("/api/gm/assets/audit", headers=gm(ref_settings)).json()
    assert body["human_approval_inferred"] is False
    assert body["illustrated_submaps_installed"] is False
    assert body["relationships"] and body["findings"]


def test_gm_reference_one_record_includes_gm_material(client_ref, ref_settings):
    npc = client_ref.get("/api/gm/reference/npcs/CAR-NPC-001", headers=gm(ref_settings)).json()
    assert npc["gm"]["gm_only"]["canonical_secret"] == "MINI-NPC-SECRET"
    omen = client_ref.get("/api/gm/reference/omens/OM-001", headers=gm(ref_settings)).json()
    assert omen["sealed"]["house_story"]["secret_truth"] == "MINI-SEALED-TRUTH"
    assert client_ref.get("/api/gm/reference/places/CAR-404", headers=gm(ref_settings)).status_code == 404
    assert client_ref.get("/api/gm/reference/nonsense", headers=gm(ref_settings)).status_code == 404


def test_concurrent_gm_reads_succeed(client_ref, ref_settings):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as pool:
        codes = list(pool.map(lambda _: client_ref.get("/api/gm/reference/places", headers=gm(ref_settings)).status_code, range(64)))
    assert codes == [200] * 64


def test_tiles_served_read_only(client_ref):
    assert client_ref.get("/tiles/tile_manifest.json").json() == {"levels": []}
    # encoded so the client doesn't collapse the dot segments before the request reaches the tile mount
    for escape in ("/tiles/%2e%2e/%2e%2e/omen/Carousel_Omen_Engine_Data_v0.2.json", "/tiles/..%5c..%5comen%5cCarousel_Omen_Engine_Data_v0.2.json"):
        r = client_ref.get(escape)
        assert r.status_code == 404 and "registry" not in r.text, escape
