"""The player's venue panel: the room the group stands in, the ways on (blocked ones with the reason), the rooms it has
been in, and the entry and exit. Only zones visited or one link from here are ever listed (carousel/venues.visible_zones);
no Omen, cue, link mode or unreached room reaches the player.

POST /venue/move applies `move_zone` and starts the GM's turn in the same request, the way map_api's travel does: the
move and its turn share the idempotency key, so a repeated request returns the same turn and moves nobody."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .carousel import venues
from .carousel.refdata import has_reference
from .db import one
from .gm.orchestrator import BusyError
from .rules.engine import ChangeError, apply_change


class MoveIn(BaseModel):
    to: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=80)


def player_venue(conn, cid: int) -> dict | None:
    if not has_reference(conn):
        return None
    venue_id, here = venues.position(conn, cid)
    v = venues.venue(conn, venue_id) if venue_id else None
    if not v or not here:
        return None
    visible = set(venues.visible_zones(conn, cid))
    from .carousel.venue_plans import player_plan
    return {"venue_id": v["id"], "title": v["title"], "zone": {"id": here, "label": venues.label(conn, here, cid)},
            "plan": player_plan(conn, cid, v, here),
            "departure_blocked": venues.departure_block(conn, cid, here),
            "ways": [{"id": n["zone_id"], "label": n["label"], "blocked": n["blocked_reason"]}
                     for n in venues.neighbours(conn, cid, here) if n["zone_id"] in visible],
            "seen": [{"id": z, "label": venues.label(conn, z, cid)} for z in venues.visited(conn, cid, v["id"])],
            "entry": venues.label(conn, v["entry_zone"]), "exit": venues.label(conn, v["exit_zone"])}


def register(app: FastAPI, state) -> None:
    @app.get("/api/campaigns/{cid}/venue")
    def get_venue(cid: int):
        c = state.conn()
        try:
            if not one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,)):
                raise HTTPException(404, "campaign not found")
            return player_venue(c, cid)
        finally:
            c.close()

    @app.post("/api/campaigns/{cid}/venue/move")
    def move(cid: int, body: MoveIn):
        out: dict = {}

        def prepare(conn) -> tuple[str, str]:
            out["move"] = apply_change(conn, cid, None, {"op": "move_zone", "to": body.to})   # its own savepoint: a refusal leaves nothing
            label = venues.label(conn, out["move"]["zone_id"])
            return f"(The group moves to {label}.)", "zone_omen" if out["move"]["sighted"] else "zone"

        try:
            turn = state.turns.submit(cid, "", body.idempotency_key, prepare=prepare)
        except BusyError:
            raise HTTPException(409, "The GM is still resolving a turn.")
        except ChangeError as exc:
            raise HTTPException(422, str(exc))
        except KeyError:
            raise HTTPException(404, "campaign not found")
        return {"move": out.get("move"), "turn": turn}
