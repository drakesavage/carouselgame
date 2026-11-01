"""Validated movement ops (registered into engine.CHANGE_OPS by rules/__init__.py).

`travel` is the only way the group moves on the town map: it plans over the travel graph with the campaign's story
flags, decides any Omen interruption up front (carousel/interruptions.py), then applies position, time, known places
and sightings in one step. Arriving at a venue's place puts the group at its entry zone (carousel/venues.py); a
group inside a venue leaves only from its entry or exit zone. `learn_place` adds a place the group has heard of; `set_flag` / `clear_flag` open and close
flagged edges (GM-only)."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..carousel.import_places import resolve_place
from ..carousel.interruptions import decide, truncate
from ..carousel.refdata import require_reference
from ..carousel import venues
from ..carousel.travel import missing_flags, nearest_node, plan_route
from ..db import one, rows
from .engine import AdvanceTime, ChangeError, Ctx, SetLocation, _apply_advance_time, _apply_set_location

# Camp Dyer, Dyer's Lodge, Town Square, the Diner (design §10; the bowling alley has no record yet)
SAFE_PLACE_IDS = {"CAR-046", "CAR-047", "CAR-002", "CAR-006"}


class Travel(BaseModel):
    op: Literal["travel"]
    to: str | None = None
    north: float | None = None
    west: float | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _target(self):
        if not self.to and (self.north is None or self.west is None):
            raise ValueError("give `to` (a place ID) or both `north` and `west`")
        return self


class LearnPlace(BaseModel):
    op: Literal["learn_place"]
    place: str
    how: str = Field(min_length=1, max_length=200)


class FlagChange(BaseModel):
    op: Literal["set_flag", "clear_flag"]
    flag: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=300)


def _ref_place(ctx: Ctx, pid: str) -> dict | None:
    return one(ctx.conn, "SELECT id, name, north, west FROM ref.places WHERE id = ?", (pid,))


def flags(ctx: Ctx) -> set[str]:
    return {r["flag"] for r in rows(ctx.conn, "SELECT flag FROM campaign_flags WHERE campaign_id = ?", (ctx.campaign_id,))}


def _know(ctx: Ctx, place_id: str, how: str) -> bool:
    return ctx.conn.execute("INSERT OR IGNORE INTO known_places (campaign_id, place_id, how, known_at_minutes) VALUES (?,?,?,?)",
                            (ctx.campaign_id, place_id, how, ctx.game_time())).rowcount > 0


def _label(ctx: Ctx, node: str) -> tuple[str, str | None]:
    """(location text, place ID or None): a place's name, or "on the road near <nearest known place>" for a junction."""
    place = _ref_place(ctx, resolve_place(ctx.conn, node) or "") if not node.startswith("J-") else None
    if place:
        return place["name"] or place["id"], place["id"]
    n = one(ctx.conn, "SELECT north, west FROM ref.graph_nodes WHERE id = ?", (node,))
    near = rows(ctx.conn, """SELECT p.name, p.north, p.west FROM known_places k JOIN ref.places p ON p.id = k.place_id
                             WHERE k.campaign_id = ? AND p.north IS NOT NULL""", (ctx.campaign_id,))
    if not n or not near:
        return "on the road", None
    best = min(near, key=lambda p: math.hypot(p["north"] - n["north"], p["west"] - n["west"]))
    return f"on the road near {best['name']}", None


def _apply_travel(ctx: Ctx, op: Travel) -> dict:
    require_reference(ctx.conn)
    from ..carousel.opening import stage
    if stage(ctx.conn, ctx.campaign_id) in ("car", "silas"):
        raise ChangeError("you haven't left the car park yet")
    camp = one(ctx.conn, "SELECT node_id, travel_seq, venue_id, zone_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))
    start = camp["node_id"]
    if not start:
        raise ChangeError("the group has no position on the town map yet")
    if camp["venue_id"]:
        v = venues.venue(ctx.conn, camp["venue_id"])
        if v and camp["zone_id"] not in (v["entry_zone"], v["exit_zone"]):
            ways = dict.fromkeys(venues.label(ctx.conn, z) for z in (v["entry_zone"], v["exit_zone"]))
            raise ChangeError(f"leave by {' or '.join(ways)}")
    if op.to:
        goal = resolve_place(ctx.conn, op.to) or op.to
    else:
        goal = nearest_node(ctx.conn, op.north, op.west)
        if goal is None:
            raise ChangeError("no road there")
    if goal == start:
        raise ChangeError("already here")
    have = flags(ctx)
    route = plan_route(ctx.conn, start, goal, have)
    if route is None:
        need = missing_flags(ctx.conn, start, goal, have)
        raise ChangeError(f"no route without: {', '.join(need)}" if need else "no route")
    seq = camp["travel_seq"] + 1
    stop = decide(ctx.conn, ctx.campaign_id, route, seq)
    if stop:
        route = truncate(route, stop.node)
    arrived = route.nodes[-1]
    where, place_id = _label(ctx, arrived)
    ctx.conn.execute("UPDATE campaigns SET node_id = ?, travel_seq = ? WHERE id = ?", (arrived, seq, ctx.campaign_id))
    _apply_set_location(ctx, SetLocation(op="set_location", location=where, safe_room=arrived in SAFE_PLACE_IDS))
    from .state_ops import pc_group
    g = pc_group(ctx)
    if g:
        ctx.conn.execute("UPDATE party_groups SET node_id = ? WHERE id = ?", (arrived, g["id"]))
    entered = venues.enter(ctx.conn, ctx.campaign_id, arrived, g["id"] if g else None)
    venue = {"venue_id": entered["venue_id"], "title": venues.venue(ctx.conn, entered["venue_id"])["title"],
             "zone_label": entered["label"]} if entered else None
    if route.minutes > 0:
        _apply_advance_time(ctx, AdvanceTime(op="advance_time", minutes=route.minutes, reason=op.reason or f"walking to {where}"))
    if place_id:
        _know(ctx, place_id, "visited")
    interrupted = None
    if stop:
        o = one(ctx.conn, "SELECT description FROM ref.omens WHERE id = ?", (stop.omen_id,))
        ctx.conn.execute("""INSERT INTO omen_states (campaign_id, omen_id, stage, seen_at_node, seen_at_minutes)
                            VALUES (?,?, 'visible', ?, ?)""", (ctx.campaign_id, stop.omen_id, arrived, ctx.game_time()))
        interrupted = {"omen_id": stop.omen_id, "description": o["description"]}
    summary = f"The group walks to {where} ({route.minutes} min)" + (" and stops: something is wrong" if stop else "")
    ctx.event("travel", summary, {"route_nodes": route.nodes, "minutes": route.minutes, "arrived": arrived,
                                  "interrupted": interrupted, "geometry_px": route.geometry_px, "venue": venue})
    if stop:
        ctx.event("omen_sighted", f"Seen at {where}: {interrupted['description']}", {**interrupted, "node": arrived, "kind": stop.kind})
    return {"summary": summary, "arrived": arrived, "interrupted": interrupted, "minutes": route.minutes, "geometry_px": route.geometry_px,
            "venue": venue}


def _apply_learn_place(ctx: Ctx, op: LearnPlace) -> dict:
    require_reference(ctx.conn)
    pid = resolve_place(ctx.conn, op.place)
    if pid is None:
        raise ChangeError(f"unknown place {op.place!r}")
    name = _ref_place(ctx, pid)["name"] or pid
    new = _know(ctx, pid, op.how)
    if new:
        ctx.event("place_learned", f"The group learns of {name}", {"place_id": pid, "how": op.how})
    return {"summary": f"{name} is {'now' if new else 'already'} known"}


def _apply_flag(ctx: Ctx, op: FlagChange) -> dict:
    if op.op == "set_flag":
        ctx.conn.execute("""INSERT INTO campaign_flags (campaign_id, flag, set_at_minutes, source) VALUES (?,?,?,?)
                            ON CONFLICT(campaign_id, flag) DO NOTHING""", (ctx.campaign_id, op.flag, ctx.game_time(), op.reason))
        summary = f"Story flag set: {op.flag}"
    else:
        ctx.conn.execute("DELETE FROM campaign_flags WHERE campaign_id = ? AND flag = ?", (ctx.campaign_id, op.flag))
        summary = f"Story flag cleared: {op.flag}"
    ctx.event("flag_set" if op.op == "set_flag" else "flag_cleared", summary, {"flag": op.flag, "reason": op.reason}, visibility="gm")
    return {"summary": summary}


def register(ops: dict) -> None:
    ops.update({
        "travel": (Travel, _apply_travel),
        "learn_place": (LearnPlace, _apply_learn_place),
        "set_flag": (FlagChange, _apply_flag),
        "clear_flag": (FlagChange, _apply_flag),
    })
