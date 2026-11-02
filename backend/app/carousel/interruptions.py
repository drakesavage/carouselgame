"""Omen interruptions, decided when a route is planned, never during the walk and never by a roll for an outcome.
The decision depends only on campaign state and the travel sequence number, which restores with a save, so the same
trip from the same save point is interrupted the same way. Seeing an Omen is always safe: a story starts only on its
Choice action (Phase 4). A fixed Omen anchored on a district or square (placement 'area') stops the group at that
place like any fixed one; an Omen whose place has no coordinates never stops a route; an Omen placed in a venue zone is
sighted only by entering that zone (rules/venue_ops.py), never from the street. Item-bound and special Omens never
interrupt: the GM gets them as leads (memory/context.py OMEN LEADS)."""
from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass
from typing import Literal

from .travel import Route, build_route

ROAM_RADIUS = 120.0
ROAM_CHANCE_PER_TICK, ROAM_CHANCE_MAX = 0.02, 0.5


@dataclass(frozen=True)
class Interruption:
    omen_id: str
    node: str
    kind: Literal["fixed", "roaming"]


def roam_chance(route: Route) -> float:
    return min(ROAM_CHANCE_MAX, route.ticks * ROAM_CHANCE_PER_TICK)


def _draw(cid: int, seq: int) -> float:
    return int(hashlib.sha256(f"{cid}:{seq}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _unseen(conn: sqlite3.Connection, cid: int, spawn: str) -> list[sqlite3.Row]:
    sql = """SELECT o.id, o.place_id, p.north, p.west FROM ref.omens o JOIN ref.places p ON p.id = o.place_id
             WHERE o.record_type = 'Playable Omen' AND o.spawn_mode = ?
               AND NOT EXISTS (SELECT 1 FROM omen_states s WHERE s.campaign_id = ? AND s.omen_id = o.id)
               AND NOT EXISTS (SELECT 1 FROM ref.venue_omens v WHERE v.omen_id = o.id AND v.placed = 1)"""
    return conn.execute(sql + " ORDER BY o.id", (spawn, cid)).fetchall()


def decide(conn: sqlite3.Connection, cid: int, route: Route, seq: int) -> Interruption | None:
    ahead = route.nodes[1:]   # never at the start node
    if not ahead:
        return None
    fixed: dict[str, str] = {}
    for o in _unseen(conn, cid, "fixed"):     # place- and area-anchored alike, first by Omen id
        if o["north"] is not None:            # a place without a parcel is never a stop
            fixed.setdefault(o["place_id"], o["id"])
    for node in ahead:
        if node in fixed:
            return Interruption(fixed[node], node, "fixed")
    xy = {r[0]: (r[1], r[2]) for r in conn.execute(
        f"SELECT id, north, west FROM ref.graph_nodes WHERE id IN ({','.join('?' * len(ahead))})", ahead)}
    pts = [(n, xy[n]) for n in ahead if n in xy and xy[n][0] is not None]

    def dist(o, p):
        return math.hypot(o["north"] - p[0], o["west"] - p[1])

    candidates = {o["id"]: o for o in _unseen(conn, cid, "roaming")
                  if o["north"] is not None and any(dist(o, p) <= ROAM_RADIUS for _, p in pts)}
    if not candidates:
        return None
    h = _draw(cid, seq)
    if h >= roam_chance(route):
        return None
    ids = sorted(candidates)
    o = candidates[ids[int(h * 1e6) % len(ids)]]
    node = min(pts, key=lambda np_: dist(o, np_[1]))[0]
    return Interruption(o["id"], node, "roaming")


def truncate(route: Route, node: str) -> Route:
    """The route up to and including `node` (its first visit)."""
    i = route.nodes.index(node)
    return build_route(route.nodes[:i + 1], route.edges[:i], route.legs[:i])
