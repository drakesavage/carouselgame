"""Route planning over the imported travel graph (ref.graph_nodes / ref.graph_edges). The graph is the only movement
authority: an edge is usable only if the campaign has every flag in its `requires` and every flag the node it leads
into requires; a directed edge runs from → to only. Coordinates and painted roads never add a connection."""
from __future__ import annotations

import heapq
import json
import math
import sqlite3
from dataclasses import dataclass, field

from .coords import to_pixel
from .import_places import resolve_place

MINUTES_PER_TICK = 3      # game minutes per graph cost tick (design §13: the builder's call)
SNAP_UNITS = 25.0


@dataclass(frozen=True)
class Route:
    nodes: list[str]
    edges: list[str]
    ticks: float
    minutes: int
    geometry_px: list[tuple[float, float]]
    legs: tuple[tuple[float, tuple[tuple[float, float], ...]], ...] = field(default=(), repr=False, compare=False)  # (cost, px points) per edge


def build_route(nodes: list[str], edges: list[str], legs) -> Route:
    """A route from its nodes, edge IDs and per-edge (cost, pixel points); geometry joins the legs without repeating shared vertices."""
    pts: list[tuple[float, float]] = []
    for _, leg in legs:
        for px in leg:
            if not pts or pts[-1] != px:
                pts.append(px)
    ticks = round(sum(c for c, _ in legs), 6)
    return Route(list(nodes), list(edges), ticks, round(ticks * MINUTES_PER_TICK), pts, tuple(legs))


@dataclass(frozen=True)
class _Edge:
    id: str
    to: str
    cost: float
    requires: frozenset[str]
    points: list[tuple[float, float]]   # [north, west] oriented in travel direction


def _ref(conn: sqlite3.Connection):
    """Graph tables are read as ref.* on a campaign connection; a bare reference connection works too."""
    attached = any(r[1] == "ref" for r in conn.execute("PRAGMA database_list"))
    return "ref." if attached else ""


def _graph(conn: sqlite3.Connection) -> tuple[dict[str, list[_Edge]], dict[str, frozenset[str]], dict[str, tuple[float, float]]]:
    p = _ref(conn)
    node_req, node_xy = {}, {}
    for r in conn.execute(f"SELECT id, north, west, requires_json FROM {p}graph_nodes"):
        node_req[r[0]] = frozenset(json.loads(r[3]))
        node_xy[r[0]] = (r[1], r[2])
    adj: dict[str, list[_Edge]] = {}
    for r in conn.execute(f"SELECT id, from_id, to_id, directed, requires_json, cost, geometry_json FROM {p}graph_edges"):
        eid, a, b, directed, req, cost, geom = r
        pts = [tuple(pt) for pt in json.loads(geom)] or [node_xy.get(a), node_xy.get(b)]
        req = frozenset(json.loads(req))
        adj.setdefault(a, []).append(_Edge(eid, b, cost or 0.0, req, pts))
        if not directed:
            adj.setdefault(b, []).append(_Edge(eid, a, cost or 0.0, req, list(reversed(pts))))
    return adj, node_req, node_xy


def _node_id(conn: sqlite3.Connection, place_or_node: str) -> str:
    """Graph node IDs equal place IDs for places; aliases resolve to their target. Junctions (J-…) pass through."""
    p = _ref(conn)
    if conn.execute(f"SELECT 1 FROM {p}graph_nodes WHERE id = ?", (place_or_node,)).fetchone():
        return place_or_node
    if p:
        row = conn.execute("SELECT target_id FROM ref.place_aliases WHERE alias_id = ?", (place_or_node,)).fetchone()
        return row[0] if row else place_or_node
    return resolve_place(conn, place_or_node) or place_or_node


LOCKED_STEP = 1e6   # missing_flags: a step needing a flag the campaign lacks costs this much more, so the fewest locked steps win


def _search(conn, start: str, goal: str, penalty) -> tuple[list[str], list[_Edge], float] | None:
    """Dijkstra; penalty(required flags) is None for a blocked step, else extra weight added to its cost."""
    adj, node_req, _ = _graph(conn)
    start, goal = _node_id(conn, start), _node_id(conn, goal)
    if start not in node_req or goal not in node_req:
        return None
    best = {start: 0.0}
    prev: dict[str, tuple[str, _Edge]] = {}
    heap = [(0.0, start)]
    while heap:
        d, n = heapq.heappop(heap)
        if n == goal:
            break
        if d > best.get(n, math.inf):
            continue
        for e in adj.get(n, []):
            extra = penalty(e.requires | node_req.get(e.to, frozenset()))
            if extra is None:
                continue
            nd = d + e.cost + extra
            if nd < best.get(e.to, math.inf):
                best[e.to], prev[e.to] = nd, (n, e)
                heapq.heappush(heap, (nd, e.to))
    if goal not in best:
        return None
    nodes, edges = [goal], []
    while nodes[-1] != start:
        n, e = prev[nodes[-1]]
        nodes.append(n)
        edges.append(e)
    return nodes[::-1], edges[::-1], best[goal]


def _route(nodes: list[str], edges: list[_Edge], ticks: float) -> Route:
    return build_route(nodes, [e.id for e in edges], [(e.cost, tuple(to_pixel(*pt) for pt in e.points)) for e in edges])


def plan_route(conn: sqlite3.Connection, start: str, goal: str, flags: set[str]) -> Route | None:
    found = _search(conn, start, goal, lambda req: 0.0 if req <= flags else None)
    return None if found is None else _route(*found)


def missing_flags(conn: sqlite3.Connection, start: str, goal: str, flags: set[str]) -> list[str]:
    """When plan_route is None: the flags needed on the path with the fewest locked steps, cheapest among those
    (empty if no path exists even with every flag)."""
    found = _search(conn, start, goal, lambda req: 0.0 if req <= flags else LOCKED_STEP)
    if found is None:
        return []
    _, node_req, _ = _graph(conn)
    need: set[str] = set()
    for e in found[1]:
        need |= e.requires | node_req.get(e.to, frozenset())
    return sorted(need - flags)


def nearest_node(conn: sqlite3.Connection, north: float, west: float, max_units: float = SNAP_UNITS) -> str | None:
    p = _ref(conn)
    best, best_d = None, max_units
    for nid, n, w in conn.execute(f"SELECT id, north, west FROM {p}graph_nodes WHERE map_id = 'town' AND north IS NOT NULL"):
        d = math.hypot(n - north, w - west)
        if d <= best_d:
            best, best_d = nid, d
    return best
