"""Validated story-venue ops (registered into engine.CHANGE_OPS by rules/__init__.py).

A story-only venue (carousel/venues.STORY_LAYERS) is reached only through its story: `enter_story_venue` takes the
group in at the entry zone while a story is running and the venue belongs to it (it holds the story's Omen, or the
group stands at one of its member places). Travel waits while the group is inside (rules/travel_ops.py). The group
comes out by `leave_story_venue` at the exit zone, or when the story concludes, whatever the ending
(rules/story_ops.py), so a story can never strand it. The entry reason is GM-only."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..carousel import venues
from ..carousel.fate import GONE
from ..carousel.refdata import require_reference
from ..carousel.stories import active_story
from ..db import one, rows
from .engine import ChangeError, Ctx
from .state_ops import pc_group


class EnterStoryVenue(BaseModel):
    op: Literal["enter_story_venue"]
    venue: str
    reason: str = Field(min_length=1, max_length=200)


class LeaveStoryVenue(BaseModel):
    op: Literal["leave_story_venue"]


def _apply_enter(ctx: Ctx, op: EnterStoryVenue) -> dict:
    require_reference(ctx.conn)
    s = active_story(ctx.conn, ctx.campaign_id)
    if not s:
        raise ChangeError("a story venue is entered only during a story")
    v = venues.venue(ctx.conn, op.venue)
    if not v:
        raise ChangeError(f"unknown venue {op.venue!r}")
    if v["layer"] not in venues.STORY_LAYERS:
        raise ChangeError(f"{v['title']} is not a story venue; the group enters it by travelling there")
    if not v["entry_zone"]:
        raise ChangeError(f"{v['title']} has no way in")
    node = one(ctx.conn, "SELECT node_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))["node_id"]
    holds = one(ctx.conn, "SELECT 1 AS x FROM ref.venue_omens WHERE venue_id = ? AND omen_id = ?", (v["id"], s["omen_id"]))
    if not holds and not venues.members_at(ctx.conn, v["id"], node):
        raise ChangeError(f"{v['title']} has nothing to do with {s['title']} or this place")
    g = pc_group(ctx)
    members = (rows(ctx.conn, "SELECT status FROM characters WHERE campaign_id = ? AND assignment = ?", (ctx.campaign_id, g["key"])) if g
               else rows(ctx.conn, "SELECT c.status FROM characters c JOIN campaigns k ON k.active_character_id = c.id WHERE k.id = ?",
                         (ctx.campaign_id,)))   # no group recorded: the player character alone
    if members and all(m["status"] in GONE for m in members):
        raise ChangeError("nobody in the group is left to go in")
    at = venues.enter_story(ctx.conn, ctx.campaign_id, v["id"], g["id"] if g else None)
    summary = f"The group goes into {v['title']}."
    ctx.event("story_venue_entered", summary, {"venue_id": v["id"], "zone": at["label"]})
    ctx.event("story_venue_reason", f"Into {v['title']}: {op.reason}", {"venue_id": v["id"], "story_id": s["id"], "reason": op.reason}, "gm")
    return {"summary": summary, "venue_id": v["id"], "zone_id": at["zone_id"]}


def leave(ctx: Ctx, v: dict) -> dict | None:
    """Bring the group back to the town venue at its node (or none). Used by leave_story_venue and conclude_story."""
    node = one(ctx.conn, "SELECT node_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))["node_id"]
    g = pc_group(ctx)
    at = venues.enter(ctx.conn, ctx.campaign_id, node, g["id"] if g else None)
    ctx.event("story_venue_left", f"The group comes out of {v['title']}.", {"venue_id": v["id"]})
    return at


def _apply_leave(ctx: Ctx, op: LeaveStoryVenue) -> dict:
    require_reference(ctx.conn)
    v = venues.in_story_venue(ctx.conn, ctx.campaign_id)
    if not v:
        raise ChangeError("the group is not inside a story venue")
    _, here = venues.position(ctx.conn, ctx.campaign_id)
    if here != v["exit_zone"]:
        raise ChangeError(f"the way out of {v['title']} is {venues.label(ctx.conn, v['exit_zone'])}")
    leave(ctx, v)
    return {"summary": f"The group comes out of {v['title']}."}


def register(ops: dict) -> None:
    ops.update({
        "enter_story_venue": (EnterStoryVenue, _apply_enter),
        "leave_story_venue": (LeaveStoryVenue, _apply_leave),
    })
