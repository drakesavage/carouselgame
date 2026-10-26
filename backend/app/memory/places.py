"""The places the party knows, as a nested "known world" for the map and for location art (roadmap M8).

Built only from player-known records: the locations table (places set_location or a ruling recorded), party groups'
positions, scenes, recorded moves and remembered saferooms. A location string names its own nesting, most specific first
("The Molting Pigeon, Heavensworth, Over City"), so the tree needs no coordinates and invents no geography. Places that
are known but not visited (seen from outside, reported) are marked so; nothing unknown is shown.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..db import one, rows

SAFEROOM = re.compile(r"\bsaferoom\b", re.I)


def chain(location: str | None) -> list[str]:
    """'The Molting Pigeon, Heavensworth, Over City' -> ['Over City', 'Heavensworth', 'The Molting Pigeon'] (outermost first)."""
    if not location:
        return []
    parts = [p.strip(" .") for p in re.split(r",|—| - ", location) if p.strip(" .")]
    parts = [p for p in parts if not re.match(r"^(floor \d+|not recorded|position not recorded)$", p, re.I)]
    return list(reversed(parts))


def key(name: str) -> str:
    return re.sub(r"^the\s+", "", re.sub(r"[^a-z0-9 ]+", "", name.lower())).strip()


def known_world(conn: sqlite3.Connection, cid: int) -> dict:
    camp = one(conn, "SELECT current_location, current_floor, safe_room FROM campaigns WHERE id = ?", (cid,))
    floor = camp["current_floor"]
    nodes: dict[str, dict] = {}
    roots: list[str] = []

    def add(location: str | None, *, visited: bool, floor_: int | None = None, description: str | None = None, source: str = "") -> str | None:
        c = chain(location)
        parent = None
        for depth, name in enumerate(c):
            k = key(name)
            leaf = depth == len(c) - 1
            n = nodes.get(k)
            if n is None:
                n = nodes[k] = {"key": k, "name": name, "parent": None, "children": [], "visited": False, "saferoom": False, "current": False,
                                "floor": floor_ if floor_ is not None else floor, "description": None, "groups": [], "sources": []}
                if parent is None:
                    roots.append(k)
            if parent is not None and n["parent"] is None and k != parent and k not in _ancestors(nodes, parent):
                if k in roots:
                    roots.remove(k)
                n["parent"] = parent
                if k not in nodes[parent]["children"]:
                    nodes[parent]["children"].append(k)
            if leaf:
                n["visited"] = n["visited"] or visited
                if description and not n["description"]:
                    n["description"] = description
                if source and source not in n["sources"]:
                    n["sources"].append(source)
                if floor_ is not None:
                    n["floor"] = floor_
            elif visited:
                n["visited"] = True  # being inside a place means having been in its district
            parent = k
        return key(c[-1]) if c else None

    for loc in rows(conn, "SELECT name, floor, description, visited FROM locations WHERE campaign_id = ? AND discovered = 1 AND visibility = 'player'", (cid,)):
        add(loc["name"], visited=bool(loc.get("visited", 1)), floor_=loc["floor"], description=loc["description"], source="recorded place")
    for s in rows(conn, "SELECT location FROM scenes WHERE campaign_id = ? AND location IS NOT NULL", (cid,)):
        add(s["location"], visited=True, source="scene")
    moves = []
    for e in rows(conn, """SELECT type, payload_json, game_time_minutes FROM events WHERE campaign_id = ? AND visibility = 'player' AND rolled_back = 0
                           AND type IN ('location_changed', 'group_moved') ORDER BY id""", (cid,)):
        p = e.get("payload") or {}
        to = p.get("to") or p.get("location")
        frm = p.get("from")
        a = add(frm, visited=True, source="move") if frm else None
        b = add(to, visited=True, floor_=p.get("floor"), source="move") if to else None
        if a and b and a != b:
            moves.append((a, b, e["game_time_minutes"]))
    for g in rows(conn, "SELECT key, label, location, activity FROM party_groups WHERE campaign_id = ? AND status = 'active'", (cid,)):
        k = add(g["location"], visited=True, source="party group")
        members = one(conn, "SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ? AND assignment = ?", (cid, g["key"]))["n"]
        group = {"key": g["key"], "label": g["label"], "members": members}
        if k:
            nodes[k]["groups"].append(group)
        if SAFEROOM.search(f"{g['key']} {g['label']}") and k:
            nodes[k]["saferoom"] = True
    m = one(conn, "SELECT value FROM meta WHERE key = ?", (f"saferooms:{cid}",))
    for place in json.loads(m["value"]) if m else []:
        k = key(place.split(",")[0])
        if k in nodes:
            nodes[k]["saferoom"] = True
    here = add(camp["current_location"], visited=True, source="campaign")
    if here:
        nodes[here]["current"] = True
        if camp["safe_room"]:
            nodes[here]["saferoom"] = True
    away = [g for g in rows(conn, "SELECT key, label FROM party_groups WHERE campaign_id = ? AND status = 'active' AND location IS NULL", (cid,))]
    routes = {}
    for a, b, t in moves:
        pair = tuple(sorted((a, b)))
        routes.setdefault(pair, {"a": nodes[pair[0]]["name"], "b": nodes[pair[1]]["name"], "times": 0, "last": None})
        routes[pair]["times"] += 1
        routes[pair]["last"] = t
    return {"floor": floor, "roots": roots, "nodes": nodes, "routes": list(routes.values()),
            "unplaced_groups": [{"key": g["key"], "label": g["label"]} for g in away]}


def _ancestors(nodes: dict, k: str | None) -> set[str]:
    out = set()
    while k:
        out.add(k)
        k = nodes[k]["parent"]
    return out


def is_known(conn: sqlite3.Connection, cid: int, name: str) -> bool:
    """Only places on the party's map can be painted: art never depicts a discovery the party hasn't made."""
    return key(chain(name)[-1] if chain(name) else name) in known_world(conn, cid)["nodes"]
