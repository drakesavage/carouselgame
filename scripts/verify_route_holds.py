"""Read-only verification of source passage constraints and installed campaign preservation."""
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.carousel import venues

out = Path("E:/Carousel/Asset_Reconciliation")
spec = json.loads((out.parent / "sublocations/Carousel_Codex_Submap_Package_v1.1/Carousel_Codex_Submap_Spec_v1.json").read_text(encoding="utf-8"))
patches = json.loads((out / "game_reference.json").read_text(encoding="utf-8"))["venue_patches"]
superseded = []
for v in spec["maps"]:
    patch = patches.get(v["id"], {})
    if patch.get("graph_override"):
        assert patch["retired_source_graph"] == v["graph"], v["id"]
        superseded.extend({"venue_id": v["id"], "edge": e, "replacement_basis": patch["topology_basis"]}
                          for e in v["graph"]["edges"] if e.get("lock"))
        v["graph"] = patch["graph_override"]
expected = {
    (v["id"], *sorted((v["id"] + ":" + e["from"], v["id"] + ":" + e["to"]))): e["lock"]
    for v in spec["maps"] for e in v["graph"]["edges"]
    if e.get("mode") in {"walk", "vertical_transition"} and e.get("lock")
}
results = []
for receipt_name in ["root-installation.json", "active-installation.json"]:
    receipt = json.loads((out / receipt_name).read_text(encoding="utf-8"))
    backup = Path(receipt["backup"])
    data = backup.parent.parent
    with sqlite3.connect(f"file:{(data / 'campaign.db').as_posix()}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        c.execute("ATTACH DATABASE ? AS ref", (f"file:{(data / 'reference.db').as_posix()}?mode=ro",))
        actual = {(r[0], r[1], r[2]): r[3] for r in c.execute("SELECT venue_id,a,b,source_lock FROM ref.venue_links WHERE source_lock IS NOT NULL")}
        assert actual == expected, data
        sealed, public = c.execute("SELECT sealed_json,dossier_json FROM ref.omen_beats WHERE omen_id='OM-105'").fetchone()
        assert json.loads(sealed)["continuity"]["predecessor_omen_id"] == "OM-104"
        assert "predecessor_omen_id" not in public
        enterable, raw = c.execute("SELECT enterable,raw_json FROM ref.venues WHERE id='SM-087'").fetchone()
        assert enterable and not json.loads(raw)["illustrated_geometry_installed"]
        assert c.execute("SELECT COUNT(*) FROM ref.venue_zones WHERE venue_id='SM-087' AND area_type='retired_reference'").fetchone()[0] == 9
        for vid, enabled in {"SM-010": 1, "SM-049": 1, "SM-057": 0, "SM-068": 1, "SM-087": 1}.items():
            flag, raw = c.execute("SELECT enterable,raw_json FROM ref.venues WHERE id=?", (vid,)).fetchone()
            assert flag == enabled and json.loads(raw)["reconciliation"]["topology_basis"]["layout_human_approved"]
        place = c.execute("SELECT px,py,in_town_map,status FROM ref.places WHERE id='CAR-027'").fetchone()
        assert abs(place[0] - 7620) < 1e-6 and abs(place[1] - 3860) < 1e-6 and tuple(place[2:]) == (1, "active")
        cid = c.execute("SELECT id FROM campaigns ORDER BY id LIMIT 1").fetchone()
        cid = cid[0] if cid else 0
        blocked_directions, open_departures = 0, 0
        for vid, a, b in expected:
            for origin, target in [(a, b), (b, a)]:
                way = next(w for w in venues.neighbours(c, cid, origin) if w["zone_id"] == target)
                rule = expected[(vid, a, b)]
                should_block = True
                if rule.startswith("entry_story:"):
                    _, outside, lock = rule.split(":", 2)
                    active = c.execute("SELECT 1 FROM stories WHERE campaign_id=? AND status='active' AND story_lock=?", (cid, lock)).fetchone()
                    overlay = c.execute("SELECT 1 FROM venue_overlays WHERE campaign_id=? AND kind='block' AND lifted=0 AND ((a=? AND b=?) OR (a=? AND b=?))", (cid, origin, target, target, origin)).fetchone()
                    should_block = bool((origin == vid + ":" + outside and not active) or overlay)
                assert bool(way["blocked_reason"]) == should_block, (origin, target)
                blocked_directions += int(should_block)
                open_departures += int(not should_block)
        with sqlite3.connect(f"file:{(backup / 'campaign.db').as_posix()}?mode=ro", uri=True) as before:
            preserved = []
            for table in ["campaigns", "characters", "character_tickets", "save_points", "party_groups", "stories", "zone_visits"]:
                old = sorted(before.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
                new = sorted((tuple(r) for r in c.execute(f'SELECT * FROM "{table}"')), key=repr)
                assert old == new, (data, table)
                preserved.append(table)
        results.append({"data_dir": str(data), "source_locks_preserved": len(actual),
                        "sequel_private_predecessor_verified": True, "allcraft_text_topology_installed": True,
                        "five_approved_layouts_verified": True, "accepted_fraternity_pin_verified": True,
                        "directions_verified": len(actual) * 2, "directions_blocked_in_current_state": blocked_directions,
                        "conditional_departures_allowed": open_departures, "campaign_tables_preserved": preserved})
(out / "route-hold-verification.json").write_text(json.dumps({"ok": True, "installations": results,
    "superseded_constraints_preserved_in_archive": superseded,
    "constraints": [{"venue_id": vid, "from_zone": a, "to_zone": b, "source_lock": lock}
                    for (vid, a, b), lock in sorted(expected.items())]}, indent=2), encoding="utf-8")
print(json.dumps({"ok": True, "installations_verified": len(results), "source_locks_per_installation": len(expected)}))
