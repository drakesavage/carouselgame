"""The player's map: where the group is, the places it knows, the Omens it has seen, and travel by click.

POST /travel applies the `travel` op and starts the GM's arrival turn in the same request, under the turn's write lock
(409 while another turn runs), so the narration is already on its way when the walk animation starts. The travel and
its turn share the idempotency key: a repeated request returns the same turn and moves nobody."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .carousel.import_places import resolve_place
from .config import Settings
from .db import one
from .gm.orchestrator import BusyError
from .rules.engine import ChangeError, apply_change


class TravelIn(BaseModel):
    to: str | None = Field(default=None, max_length=40)
    north: float | None = None
    west: float | None = None
    idempotency_key: str = Field(min_length=8, max_length=80)


def _place_name(conn, pid: str | None) -> str | None:
    r = one(conn, "SELECT name FROM ref.places WHERE id = ?", (resolve_place(conn, pid) or pid,)) if pid else None
    return r["name"] if r else None


def arrival(conn, cid: int, result: dict, to: str | None) -> tuple[str, str]:
    """The arrival turn's text and kind: quiet arrivals are conversation, an Omen on the way is a mechanics turn."""
    if result["interrupted"]:
        dest = _place_name(conn, to) or _place_name(conn, result["arrived"]) or "the road"
        return f"[arrival] On the way to {dest}, we stop: {result['interrupted']['description'].rstrip('.')}.", "arrival_omen"
    loc = one(conn, "SELECT current_location FROM campaigns WHERE id = ?", (cid,))["current_location"]
    return (f"[arrival] We stop {loc}." if loc.startswith("on the road") else f"[arrival] We arrive at {loc}."), "arrival"


def register(app: FastAPI, settings: Settings, state) -> None:
    def last_travel(cid: int) -> dict | None:
        c = state.conn()
        try:
            e = one(c, "SELECT payload_json FROM events WHERE campaign_id = ? AND type = 'travel' AND rolled_back = 0 "
                       "ORDER BY id DESC LIMIT 1", (cid,))
            return e["payload"] if e else None
        finally:
            c.close()

    @app.post("/api/campaigns/{cid}/travel")
    def travel(cid: int, body: TravelIn):
        change = {"op": "travel", **({"to": body.to} if body.to else {"north": body.north, "west": body.west})}
        out: dict = {}

        def prepare(conn) -> tuple[str, str]:
            out["travel"] = apply_change(conn, cid, None, change)   # its own savepoint: a refusal leaves nothing behind
            return arrival(conn, cid, out["travel"], body.to)

        try:
            turn = state.turns.submit(cid, "", body.idempotency_key, prepare=prepare)
        except BusyError:
            raise HTTPException(409, "The GM is still resolving a turn.")
        except ChangeError as exc:
            raise HTTPException(422, str(exc))
        except KeyError:
            raise HTTPException(404, "campaign not found")
        return {"travel": out.get("travel") or last_travel(cid), "turn": turn}
