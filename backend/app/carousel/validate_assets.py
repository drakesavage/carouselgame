"""Reject broken asset references before swapping the game's reference database."""
import json
import math
import sqlite3


def validate_assets(ref: sqlite3.Connection) -> None:
    checks = {
        "travel edge endpoint": "SELECT e.id FROM graph_edges e LEFT JOIN graph_nodes a ON a.id=e.from_id LEFT JOIN graph_nodes b ON b.id=e.to_id WHERE a.id IS NULL OR b.id IS NULL",
        "travel place": "SELECT n.id FROM graph_nodes n LEFT JOIN places p ON p.id=n.location_id WHERE n.location_id IS NOT NULL AND p.id IS NULL",
        "image identity": "SELECT i.place_id FROM place_images i LEFT JOIN places p ON p.id=i.place_id WHERE p.id IS NULL",
        "venue member": "SELECT v.place_id FROM venue_places v LEFT JOIN places p ON p.id=v.place_id WHERE p.id IS NULL",
        "zone link": "SELECT e.a FROM venue_links e LEFT JOIN venue_zones a ON a.id=e.a LEFT JOIN venue_zones b ON b.id=e.b WHERE a.id IS NULL OR b.id IS NULL OR a.venue_id!=e.venue_id OR b.venue_id!=e.venue_id",
        "playable story dossier": "SELECT o.id FROM omens o LEFT JOIN omen_beats b ON b.omen_id=o.id WHERE o.record_type='Playable Omen' AND b.omen_id IS NULL",
        "placed Omen zone": "SELECT o.omen_id FROM venue_omens o LEFT JOIN venue_zones z ON z.id=o.zone_id WHERE o.placed=1 AND (z.id IS NULL OR z.venue_id!=o.venue_id OR z.area_type='retired_reference')",
        "enterable portal": "SELECT v.id FROM venues v LEFT JOIN venue_zones a ON a.id=v.entry_zone LEFT JOIN venue_zones b ON b.id=v.exit_zone WHERE v.enterable=1 AND (a.id IS NULL OR b.id IS NULL OR a.venue_id!=v.id OR b.venue_id!=v.id)",
        "retired layout link": "SELECT e.a FROM venue_links e JOIN venue_zones a ON a.id=e.a JOIN venue_zones b ON b.id=e.b WHERE a.area_type='retired_reference' OR b.area_type='retired_reference'",
        "retired arrival portal": "SELECT v.id FROM venues v JOIN venue_zones a ON a.id=v.entry_zone JOIN venue_zones b ON b.id=v.exit_zone WHERE v.enterable=1 AND (a.area_type='retired_reference' OR b.area_type='retired_reference')",
    }
    errors = []
    for vid, a, b, rule in ref.execute("SELECT venue_id,a,b,source_lock FROM venue_links WHERE source_lock LIKE 'entry_story:%'"):
        parts = rule.split(":", 2)
        outside = vid + ":" + parts[1] if len(parts) == 3 else None
        zone = ref.execute("SELECT area_type FROM venue_zones WHERE id=?", (outside,)).fetchone()
        linked = ref.execute("SELECT 1 FROM venue_omens v JOIN omens o ON o.id=v.omen_id WHERE v.venue_id=? AND o.story_lock=?", (vid, parts[2] if len(parts) == 3 else "")).fetchone()
        if outside not in (a, b) or not zone or zone[0] != "exterior" or not linked:
            errors.append(f"invalid story entry gate: {vid}")
    for vid, enterable, raw in ref.execute("SELECT id,enterable,raw_json FROM venues"):
        record = json.loads(raw)
        plan = record.get("reconciliation", {}).get("player_plan")
        if plan:
            if not plan.get("layout_human_approved") or plan.get("mode") != "discovery_filtered_schematic":
                errors.append(f"unapproved or unsupported venue plan: {vid}")
            seen = set()
            for area in plan.get("zones", []):
                zid = vid + ":" + area["node_id"]
                bounds = area.get("bounds", [])
                valid_bounds = (len(bounds) == 4 and all(type(v) in (int, float) and math.isfinite(v) for v in bounds)
                                and bounds[0] < bounds[2] and bounds[1] < bounds[3])
                row = ref.execute("SELECT venue_id,area_type FROM venue_zones WHERE id=?", (zid,)).fetchone()
                if not valid_bounds or not row or row[0] != vid or row[1] == "retired_reference" or zid in seen:
                    errors.append(f"invalid venue plan area: {zid}")
                seen.add(zid)
        alias = record.get("alias_of_venue")
        if not alias:
            continue
        target = ref.execute("SELECT raw_json FROM venues WHERE id=?", (alias,)).fetchone()
        if not target or alias == vid or json.loads(target[0]).get("alias_of_venue"):
            errors.append(f"invalid venue alias: {vid}")
        if enterable or ref.execute("SELECT 1 FROM venue_places WHERE venue_id=?", (vid,)).fetchone():
            errors.append(f"independent historical venue: {vid}")
        for pid in record.get("members", []):
            binding = ref.execute("SELECT venue_id FROM venue_places WHERE place_id=?", (pid,)).fetchone()
            if not binding or binding[0] != alias:
                errors.append(f"historical venue member binding: {vid}/{pid}")
    for label, sql in checks.items():
        bad = [r[0] for r in ref.execute(sql)]
        if bad:
            errors.append(f"{label}: {', '.join(bad)}")
    for oid, lock, raw in ref.execute("SELECT o.id,o.story_lock,b.sealed_json FROM omens o JOIN omen_beats b ON b.omen_id=o.id"):
        dossier = json.loads(raw)
        if dossier.get("story_lock_key") != lock:
            errors.append(f"story-lock disagreement: {oid}")
        continuity = dossier.get("continuity") or {}
        predecessor = continuity.get("predecessor_omen_id")
        if predecessor:
            row = ref.execute("SELECT record_type FROM omens WHERE id=?", (predecessor,)).fetchone()
            if predecessor == oid or not row or row[0] != "Playable Omen":
                errors.append(f"invalid story predecessor: {oid}")
            if continuity.get("predecessor_policy") != "require_actual_concluded_run":
                errors.append(f"unsupported predecessor policy: {oid}")
    if errors:
        raise ValueError("asset reference validation failed: " + "; ".join(errors))
