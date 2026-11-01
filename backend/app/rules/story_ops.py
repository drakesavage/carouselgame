"""Validated story ops (registered into engine.CHANGE_OPS by rules/__init__.py).

The fixed points of a Carousel story: an Omen is sighted and examined; the player's own Choice action begins its story,
which seals the dossier's truth and endings; the Plot Cycle, pressure, camera and cast move only through these ops;
eligible tickets revise the screenplay as versioned commits; conclude_story picks one of four endings and pays rewards.
The GM improvises everything between them. Nothing here writes the sealed packet into a player-visible event."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..carousel.refdata import require_reference
from ..db import one
from .engine import ChangeError, Ctx

PLAYABLE = "Playable Omen"
HUB = "Hub / generator"


class SightOmen(BaseModel):
    op: Literal["sight_omen"]
    omen: str
    how: str = Field(min_length=1, max_length=200)


class ExamineOmen(BaseModel):
    op: Literal["examine_omen"]
    omen: str


def _omen(ctx: Ctx, oid: str) -> dict:
    require_reference(ctx.conn)
    o = one(ctx.conn, "SELECT * FROM ref.omens WHERE id = ?", (oid,))
    if not o:
        raise ChangeError(f"no Omen {oid!r} in the reference data")
    return o


def _omen_state(ctx: Ctx, oid: str) -> dict | None:
    return one(ctx.conn, "SELECT * FROM omen_states WHERE campaign_id = ? AND omen_id = ?", (ctx.campaign_id, oid))


def premise(ctx: Ctx, oid: str) -> str | None:
    """The dossier's player-visible premise (the public projection, never the sealed dossier)."""
    b = one(ctx.conn, "SELECT dossier_json FROM ref.omen_beats WHERE omen_id = ?", (oid,))
    return (b["dossier"] or {}).get("player_visible_premise") if b else None


def _apply_sight(ctx: Ctx, op: SightOmen) -> dict:
    o = _omen(ctx, op.omen)
    if o["record_type"] not in (PLAYABLE, HUB):
        raise ChangeError(f"{op.omen} is a {o['record_type']!r} record, not an Omen the group can see")
    if _omen_state(ctx, op.omen):
        raise ChangeError(f"{op.omen} is already seen")
    node = one(ctx.conn, "SELECT node_id FROM campaigns WHERE id = ?", (ctx.campaign_id,))["node_id"]
    ctx.conn.execute("INSERT INTO omen_states (campaign_id, omen_id, stage, seen_at_node, seen_at_minutes) VALUES (?,?, 'visible', ?, ?)",
                     (ctx.campaign_id, op.omen, node, ctx.game_time()))
    summary = f"Something is wrong: {o['description']}"
    ctx.event("omen_sighted", summary, {"omen_id": op.omen, "description": o["description"], "node": node, "how": op.how})
    return {"summary": summary}


def _apply_examine(ctx: Ctx, op: ExamineOmen) -> dict:
    o = _omen(ctx, op.omen)
    if not _omen_state(ctx, op.omen):
        raise ChangeError(f"{op.omen} is not seen by the group yet")
    ctx.conn.execute("UPDATE omen_states SET stage = 'examined' WHERE campaign_id = ? AND omen_id = ?", (ctx.campaign_id, op.omen))
    text = premise(ctx, op.omen)
    summary = f"A closer look at {o['description']}" + (f": {text}" if text else "")
    ctx.event("omen_examined", summary, {"omen_id": op.omen, "premise": text})
    return {"summary": summary, "premise": text}


def register(ops: dict) -> None:
    ops.update({
        "sight_omen": (SightOmen, _apply_sight),
        "examine_omen": (ExamineOmen, _apply_examine),
    })
