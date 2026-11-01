"""Starting the new-game opening (carousel/opening.py): the server submits the first turn, of kind `opening`, which
narrates the car scene. The client sends an idempotency key it keeps across reloads, so a refresh or a double click
returns the same turn instead of starting a second one. After a wipe, POST /new-group puts a new group in the car
(campaigns.new_group); its opening then starts the same way."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .carousel.opening import stage
from .db import one
from .gm.orchestrator import BusyError

OPENING_TEXT = "(The game begins in the car.)"


class OpeningStartIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=80)


class NewGroupIn(BaseModel):
    character: str = Field(min_length=1, max_length=80)


def register(app: FastAPI, state, write_lock) -> None:
    @app.post("/api/campaigns/{cid}/new-group")
    def start_new_group(cid: int, body: NewGroupIn, _=Depends(write_lock)):
        from .campaigns import new_group
        c = state.conn()
        try:
            return {"character_id": new_group(c, cid, body.character)}
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        finally:
            c.close()

    @app.post("/api/campaigns/{cid}/opening/start")
    def start_opening(cid: int, body: OpeningStartIn):
        c = state.conn()
        try:
            if not one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,)):
                raise HTTPException(404, "campaign not found")
            same = one(c, "SELECT id FROM turns WHERE campaign_id = ? AND idempotency_key = ?", (cid, body.idempotency_key))
            if not same:
                if stage(c, cid) != "car":
                    raise HTTPException(409, "this campaign is not waiting to start its opening")
                if one(c, "SELECT id FROM turns WHERE campaign_id = ? AND status IN ('completed', 'processing')", (cid,)):
                    raise HTTPException(409, "the opening has already started")
            premise = one(c, "SELECT fact FROM lore_facts WHERE campaign_id = ? AND subject = 'Campaign premise' ORDER BY id LIMIT 1", (cid,))
        finally:
            c.close()
        text = OPENING_TEXT + (f" Player's wishes: {premise['fact']}" if premise else "")
        try:
            return {"turn": state.turns.submit(cid, text, body.idempotency_key, kind="opening")}
        except BusyError:
            raise HTTPException(409, "The GM is still resolving a turn.")
