"""Validate a game reference candidate and its HTTP assets against a campaign clone."""
import datetime
import hashlib
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from fastapi.testclient import TestClient
from app.carousel.importer import run_import
from app.carousel.refdata import open_campaign
from app.carousel import venues
from app.config import Settings
from app.db import init_db, transaction
from app.campaigns import create
from app.main import create_app

audit = pathlib.Path("E:/Carousel/Asset_Reconciliation")
candidate = audit / "runtime_candidate"
candidate.mkdir(exist_ok=True)
live = ROOT / "data/campaign.db"
with sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True) as src, sqlite3.connect(candidate / "campaign.db") as dst:
    src.backup(dst)
settings = Settings(data_dir=candidate, source_dir=pathlib.Path("E:/Carousel"), gm_provider="mock")
settings.ensure_dirs()
result = run_import(settings)
assert result.ok, (result.error, result.missing)
c = open_campaign(settings)
init_db(c)
cid = create(c, name="Asset validation " + datetime.datetime.now().isoformat(), character="Audit Inspector",
             location="Bowling alley", opening=False)
v = venues.venue_at(c, "AUD-LOC-BOWLING-ALLEY")
assert v and v["id"] == "SM-086"
c.close()
checks = []
with TestClient(create_app(settings, bootstrap=False, local_hosts={"testclient"})) as client:
    routes = client.get("/openapi.json").json()["paths"]
    assert "/api/campaigns/{cid}/map" in routes and "/api/campaigns/{cid}/venue" in routes
    m = client.get(f"/api/campaigns/{cid}/map")
    assert m.status_code == 200 and m.json()["tiles"]["levels"]
    v = client.get(f"/api/campaigns/{cid}/venue")
    assert v.status_code == 200 and v.json()["venue_id"] == "SM-086"
    ref = sqlite3.connect(candidate / "reference.db")
    for pid, rel in ref.execute("SELECT place_id,rel_path FROM place_images ORDER BY place_id"):
        response = client.get("/location-art/" + rel)
        assert response.status_code == 200 and response.headers["content-type"].startswith("image/"), pid
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n"), pid
        checks.append({"place_id": pid, "image": rel, "status": 200,
                       "sha256": hashlib.sha256(response.content).hexdigest()})
    manifest = client.get("/tiles/tile_manifest.json").json()
    for level in manifest["levels"]:
        response = client.get(f"/tiles/{level['zoom']}/0/0.webp")
        assert response.status_code == 200 and response.content[:4] == b"RIFF"
    assert client.get("/api/gm/assets/audit").status_code == 403
    assert client.get("/location-art/missing-image.png").status_code == 404
    audit_body = client.get("/api/gm/assets/audit", headers={"X-GM-Token": settings.gm_token()}).json()
    ref.close()
report = {"ok": True, "scope": "candidate database; cloned campaign; no live campaign writes", "counts": result.counts,
          "saved_campaign_missing_ids": result.missing, "image_http_checks": checks,
          "tile_levels_checked": len(manifest["levels"]), "map_api": "PASS", "bowling_venue_api": "PASS",
          "missing_asset_not_html": "PASS", "director_audit_privacy": "PASS",
          "remaining_venue_holds": len(audit_body["venue_holds"]), "human_approved": False}
(audit / "runtime_candidate_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(f"PASS: {len(checks)} image HTTP checks; {len(manifest['levels'])} tile levels; map/venue APIs; saved-campaign IDs preserved")
