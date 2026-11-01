"""Validated venue ops (registered into engine.CHANGE_OPS by rules/__init__.py).

`move_zone` is the only way a group moves inside a venue: one link at a time along the venue's graph as the live
overlays leave it (carousel/venues.py). It costs no game time (the GM uses advance_time for searching or waiting) and
entering a zone only sights the Omens placed there: a story still begins only on the player's typed Choice."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..carousel import venues
from ..carousel.refdata import require_reference
from ..db import one
from .engine import ChangeError, Ctx


class MoveZone(BaseModel):
    op: Literal["move_zone"]
    to: str
    group: str | None = None


def _target(ctx: Ctx, venue_id: str, to: str) -> dict:
    """A zone of this venue by ID or exact label (reference or live story-added)."""
    z = venues.zone(ctx.conn, to)
    if z and z["venue_id"] == venue_id:
        return z
    zid = one(ctx.conn, "SELECT id FROM ref.venue_zones WHERE venue_id = ? AND label = ?", (venue_id, to))
    if not zid:
        zid = one(ctx.conn, """SELECT a AS id FROM venue_overlays WHERE campaign_id = ? AND venue_id = ? AND kind = 'add_zone'
                               AND lifted = 0 AND label = ?""", (ctx.campaign_id, venue_id, to))
    if not zid:
        raise ChangeError(f"unknown zone {to!r} in this venue")
    return venues.zone(ctx.conn, zid["id"])


def _group(ctx: Ctx, key: str | None) -> dict | None:
    from .state_ops import pc_group
    if key is None:
        return pc_group(ctx)
    g = one(ctx.conn, "SELECT * FROM party_groups WHERE campaign_id = ? AND key = ? AND status = 'active'", (ctx.campaign_id, key))
    if not g:
        raise ChangeError(f"no active group {key!r}")
    return g


def _apply_move_zone(ctx: Ctx, op: MoveZone) -> dict:
    require_reference(ctx.conn)
    from ..carousel.opening import stage
    if stage(ctx.conn, ctx.campaign_id) in ("car", "silas"):
        raise ChangeError("you haven't left the car park yet")
    pc_g = _group(ctx, None)
    g = _group(ctx, op.group)
    is_pc = g is None or (pc_g is not None and g["id"] == pc_g["id"])
    venue_id, here = venues.position(ctx.conn, ctx.campaign_id) if is_pc else (g["venue_id"], g["zone_id"])
    if not venue_id or not here:
        raise ChangeError("the group is not in a venue")
    there = _target(ctx, venue_id, op.to)
    way = next((n for n in venues.neighbours(ctx.conn, ctx.campaign_id, here) if n["zone_id"] == there["id"]), None)
    if way is None:
        raise ChangeError(f"no way from {venues.label(ctx.conn, here)} to {there['label']}")
    if way["blocked_reason"]:
        raise ChangeError(f"{there['label']} is blocked: {way['blocked_reason']}")
    if is_pc:
        ctx.conn.execute("UPDATE campaigns SET zone_id = ? WHERE id = ?", (there["id"], ctx.campaign_id))
    if g:
        ctx.conn.execute("UPDATE party_groups SET venue_id = ?, zone_id = ? WHERE id = ?", (venue_id, there["id"], g["id"]))
    ctx.conn.execute("INSERT OR IGNORE INTO zone_visits (campaign_id, zone_id, first_minutes) VALUES (?,?,?)",
                     (ctx.campaign_id, there["id"], ctx.game_time()))
    summary = f"The group moves to {there['label']}"
    ctx.event("zone_entered", summary, {"venue_id": venue_id, "zone": there["label"]})
    node = one(ctx.conn, "SELECT node_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))["node_id"]
    sighted = []
    for oid in venues.zone_omens(ctx.conn, there["id"]):
        if one(ctx.conn, "SELECT 1 AS x FROM omen_states WHERE campaign_id = ? AND omen_id = ?", (ctx.campaign_id, oid)):
            continue
        o = one(ctx.conn, "SELECT description FROM ref.omens WHERE id = ?", (oid,))
        ctx.conn.execute("""INSERT INTO omen_states (campaign_id, omen_id, stage, seen_at_node, seen_at_minutes)
                            VALUES (?,?, 'visible', ?, ?)""", (ctx.campaign_id, oid, node, ctx.game_time()))
        ctx.event("omen_sighted", f"Seen at {there['label']}: {o['description']}",
                  {"omen_id": oid, "description": o["description"], "node": node, "kind": "fixed", "zone": there["label"]})
        sighted.append(oid)
    return {"summary": summary, "zone_id": there["id"], "sighted": sighted}


def register(ops: dict) -> None:
    ops.update({
        "move_zone": (MoveZone, _apply_move_zone),
    })
