"""Check the actual running site's database and served image bytes."""
import concurrent.futures
import hashlib
import json
import pathlib
import sqlite3
import urllib.error
import urllib.request

root = pathlib.Path("E:/Carousel")
out = root / "Asset_Reconciliation"
base = "http://127.0.0.1:8797"


def get(path):
    with urllib.request.urlopen(base + path, timeout=15) as res:
        return res.status, res.headers.get("content-type", ""), res.read()


settings = json.loads(get("/api/settings")[2])
db = sqlite3.connect(f"file:{(pathlib.Path(settings['data_dir']) / 'reference.db').as_posix()}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
health = json.loads(get("/api/health")[2])
status = json.loads(get("/api/reference/status")[2])
paths = json.loads(get("/openapi.json")[2])["paths"]
expected = {r["location_id"]: r["sha256"] for r in json.loads((out / "game_reference.json").read_text(encoding="utf-8"))["references"]}


def image_check(item):
    pid, rel = item
    s, t, b = get("/location-art/" + rel)
    assert s == 200 and t.startswith("image/") and b.startswith(b"\x89PNG\r\n\x1a\n"), pid
    assert hashlib.sha256(b).hexdigest() == expected[pid], pid
    return {"place_id": pid, "ok": True}


with concurrent.futures.ThreadPoolExecutor(6) as pool:
    images = list(pool.map(image_check, db.execute("SELECT place_id,rel_path FROM place_images").fetchall()))
manifest = json.loads(get("/tiles/tile_manifest.json")[2])
for level in manifest["levels"]:
    s, t, b = get(f"/tiles/{level['zoom']}/0/0.webp")
    assert s == 200 and b[:4] == b"RIFF"
try:
    get("/location-art/does-not-exist.png")
    raise AssertionError("Missing image returned 200")
except urllib.error.HTTPError as e:
    assert e.code == 404
findings = [dict(x) for x in db.execute("SELECT category,item_id,message FROM import_report")]
assert not [f for f in findings if "but the Omen data places" in f["message"] or "gives Choice" in f["message"]]
assert "/api/campaigns/{cid}/map" in paths and "/api/campaigns/{cid}/venue" in paths
campaigns = json.loads(get("/api/campaigns")[2])
historical_aliases = []
for row in db.execute("SELECT id,enterable,raw_json FROM venues"):
    raw = json.loads(row["raw_json"])
    target = raw.get("alias_of_venue")
    if target:
        assert not row["enterable"], row["id"]
        assert not db.execute("SELECT 1 FROM venue_places WHERE venue_id=?", (row["id"],)).fetchone(), row["id"]
        for pid in raw.get("members", []):
            binding = db.execute("SELECT venue_id FROM venue_places WHERE place_id=?", (pid,)).fetchone()
            assert binding and binding[0] == target, (row["id"], pid)
        historical_aliases.append({"venue_id": row["id"], "canonical_venue_id": target})
report = {"ok": True, "url": base, "data_dir": settings["data_dir"], "health": health, "status": status,
          "historical_aliases_verified": historical_aliases,
          "images_verified": len(images), "tile_levels_verified": len(manifest["levels"]),
          "map_route_present": True, "venue_route_present": True, "missing_image_404": True,
          "location_membership_conflicts": 0, "choice_conflicts": 0, "findings": findings,
          "venue_counts": [dict(x) for x in db.execute("SELECT enterable,COUNT(*) n FROM venues GROUP BY enterable")],
          "placement_counts": [dict(x) for x in db.execute("SELECT placement,COUNT(*) n FROM omens WHERE record_type='Playable Omen' GROUP BY placement")],
          "human_approval_inferred": False, "campaign_response_received": True}
(out / "live_verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({k: report[k] for k in ["ok", "images_verified", "tile_levels_verified", "location_membership_conflicts", "choice_conflicts", "venue_counts", "placement_counts"]}, indent=2))
