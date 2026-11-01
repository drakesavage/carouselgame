"""Validated venue ops (registered into engine.CHANGE_OPS by rules/__init__.py).

`move_zone` is the only way a group moves inside a venue: one link at a time along the venue's graph as the live
overlays leave it (carousel/venues.py). It costs no game time (the GM uses advance_time for searching or waiting) and
entering a zone only sights the Omens placed there: a story still begins only on the player's typed Choice.

A running story may change the place through `block_passage`, `open_passage` and `add_zone`. Each is an overlay that
names the story and is lifted when it concludes (lift_overlays); the reference data is never changed."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..carousel import venues
from ..carousel.refdata import require_reference
from ..carousel.stories import active_story
from ..db import one
from .engine import ChangeError, Ctx


class MoveZone(BaseModel):
    op: Literal["move_zone"]
    to: str
    group: str | None = None


class Passage(BaseModel):
    op: Literal["block_passage", "open_passage"]
    a: str
    b: str
    reason: str = Field(min_length=1, max_length=300)


class AddZone(BaseModel):
    op: Literal["add_zone"]
    label: str = Field(min_length=1, max_length=80)
    connect_to: str
    reason: str = Field(min_length=1, max_length=300)


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


def _story(ctx: Ctx) -> dict:
    require_reference(ctx.conn)
    s = active_story(ctx.conn, ctx.campaign_id)
    if not s:
        raise ChangeError("a venue changes only inside a story")
    return s


def _here_venue(ctx: Ctx) -> str:
    venue_id, _ = venues.position(ctx.conn, ctx.campaign_id)
    if not venue_id:
        raise ChangeError("the group is not in a venue")
    return venue_id


def _block(ctx: Ctx, a: str, b: str) -> dict | None:
    return one(ctx.conn, """SELECT * FROM venue_overlays WHERE campaign_id = ? AND kind = 'block' AND lifted = 0
                            AND ((a = ? AND b = ?) OR (a = ? AND b = ?))""", (ctx.campaign_id, a, b, b, a))


FURTHER_IN = "somewhere further in"


def _shown(ctx: Ctx, zone: dict) -> str:
    """A zone's label for a player-visible event: only zones the group may know (visited or one link away) are named."""
    return zone["label"] if zone["id"] in venues.visible_zones(ctx.conn, ctx.campaign_id) else FURTHER_IN


def _apply_passage(ctx: Ctx, op: Passage) -> dict:
    s = _story(ctx)
    venue_id = _here_venue(ctx)
    a, b = _target(ctx, venue_id, op.a), _target(ctx, venue_id, op.b)
    if b["id"] not in {n["zone_id"] for n in venues.neighbours(ctx.conn, ctx.campaign_id, a["id"])}:
        raise ChangeError(f"no passage between {a['label']} and {b['label']}; a new way in is add_zone")
    held = _block(ctx, a["id"], b["id"])
    if op.op == "block_passage":
        if held:
            raise ChangeError(f"the way between {a['label']} and {b['label']} is already blocked: {held['reason']}")
        ctx.conn.execute("""INSERT INTO venue_overlays (campaign_id, story_id, venue_id, kind, a, b, reason, created_minutes)
                            VALUES (?,?,?, 'block', ?,?,?,?)""", (ctx.campaign_id, s["id"], venue_id, a["id"], b["id"], op.reason, ctx.game_time()))
        summary = f"The way between {a['label']} and {b['label']} is blocked: {op.reason}"
        sa, sb = _shown(ctx, a), _shown(ctx, b)
        ctx.event("passage_blocked", f"A way {FURTHER_IN} is blocked" if sa == sb == FURTHER_IN
                  else f"The way between {sa} and {sb} is blocked: {op.reason}", {"venue_id": venue_id, "a": sa, "b": sb})
    else:
        if not held or held["story_id"] != s["id"]:
            raise ChangeError(f"the way between {a['label']} and {b['label']} is not blocked by this story")
        ctx.conn.execute("UPDATE venue_overlays SET lifted = 1 WHERE id = ?", (held["id"],))
        summary = f"The way between {a['label']} and {b['label']} opens: {op.reason}"
        sa, sb = _shown(ctx, a), _shown(ctx, b)
        ctx.event("passage_opened", f"A way {FURTHER_IN} opens" if sa == sb == FURTHER_IN
                  else f"The way between {sa} and {sb} opens: {op.reason}", {"venue_id": venue_id, "a": sa, "b": sb})
    return {"summary": summary}


def _apply_add_zone(ctx: Ctx, op: AddZone) -> dict:
    s = _story(ctx)
    venue_id = _here_venue(ctx)
    joined = _target(ctx, venue_id, op.connect_to)
    n = ctx.conn.execute("SELECT COUNT(*) FROM venue_overlays WHERE story_id = ? AND kind = 'add_zone'", (s["id"],)).fetchone()[0] + 1
    zid = f"{venue_id}:story-{s['id']}-{n}"
    ctx.conn.execute("""INSERT INTO venue_overlays (campaign_id, story_id, venue_id, kind, a, b, label, reason, created_minutes)
                        VALUES (?,?,?, 'add_zone', ?,?,?,?,?)""",
                     (ctx.campaign_id, s["id"], venue_id, zid, joined["id"], op.label, op.reason, ctx.game_time()))
    summary = f"A way opens from {joined['label']}: {op.label}"
    if _shown(ctx, joined) == FURTHER_IN:
        ctx.event("zone_appeared", f"Something changes {FURTHER_IN}", {"venue_id": venue_id})
    else:
        ctx.event("zone_appeared", summary, {"venue_id": venue_id, "zone": op.label, "from": joined["label"]})
    return {"summary": summary, "zone_id": zid}


def lift_overlays(ctx: Ctx, story_id: int) -> int:
    """Lift every overlay the story made. A group standing in a room that goes with it is put back in the zone that room
    joined (following story rooms back to a reference zone), with a logged event."""
    added = {r["a"]: r for r in ctx.conn.execute("SELECT a, b, label FROM venue_overlays WHERE story_id = ? AND kind = 'add_zone' AND lifted = 0",
                                                 (story_id,))}
    n = ctx.conn.execute("UPDATE venue_overlays SET lifted = 1 WHERE story_id = ? AND lifted = 0", (story_id,)).rowcount

    def back(zid: str) -> str:
        while zid in added:
            zid = added[zid]["b"]
        return zid

    stranded = [("campaigns", r["id"], r["zone_id"]) for r in ctx.conn.execute("SELECT id, zone_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))
                if r["zone_id"] in added]
    stranded += [("party_groups", r["id"], r["zone_id"]) for r in ctx.conn.execute("SELECT id, zone_id FROM party_groups WHERE campaign_id = ?",
                                                                                    (ctx.campaign_id,)) if r["zone_id"] in added]
    for table, row_id, zid in stranded:
        to = back(zid)
        ctx.conn.execute(f"UPDATE {table} SET zone_id = ? WHERE id = ?", (to, row_id))
        if table == "campaigns":
            ctx.conn.execute("INSERT OR IGNORE INTO zone_visits (campaign_id, zone_id, first_minutes) VALUES (?,?,?)",
                             (ctx.campaign_id, to, ctx.game_time()))
            ctx.event("zone_vanished", f"{added[zid]['label']} is gone; the group is back in {venues.label(ctx.conn, to)}",
                      {"zone": added[zid]["label"], "to": venues.label(ctx.conn, to), "story_id": story_id})
    return n


def register(ops: dict) -> None:
    ops.update({
        "move_zone": (MoveZone, _apply_move_zone),
        "block_passage": (Passage, _apply_passage),
        "open_passage": (Passage, _apply_passage),
        "add_zone": (AddZone, _apply_add_zone),
    })
