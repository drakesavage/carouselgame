"""Project an approved schematic through the existing discovery boundary."""
import json

from . import venues


def player_plan(conn, cid: int, venue: dict, here: str) -> dict | None:
    if not venue["enterable"]:
        return None
    raw = json.loads(venue["raw_json"])
    plan = raw.get("reconciliation", {}).get("player_plan")
    if not plan or not plan.get("layout_human_approved"):
        return None
    known = set(venues.visible_zones(conn, cid))
    visited = set(venues.visited(conn, cid, venue["id"]))
    ways = {w["zone_id"]: w for w in venues.neighbours(conn, cid, here)}
    projected = []
    for area in plan["zones"]:
        zid = venue["id"] + ":" + area["node_id"]
        if zid not in known:
            continue
        way = ways.get(zid)
        projected.append({"id": zid, "level": area["level"], "label": area["label"], "bounds": area["bounds"],
                          "current": zid == here, "visited": zid in visited,
                          "can_move": bool(way and not way["blocked_reason"]),
                          "blocked": way["blocked_reason"] if way else None})
    current = next((z for z in projected if z["current"]), None)
    if not current:
        return None
    return {"mode": "schematic", "current_level": current["level"], "zones": projected}
