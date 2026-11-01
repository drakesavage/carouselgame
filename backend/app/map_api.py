"""The player's map: where the group is, the places it knows, the Omens it has seen, and travel by click.
Only known places and sighted Omens ever reach the player: unknown places, spoiler records and unseen Omens stay in
the reference data. Location art is served read-only from the art studio's assets folder at /location-art.

POST /travel applies the `travel` op and starts the GM's arrival turn in the same request, under the turn's write lock
(409 while another turn runs), so the narration is already on its way when the walk animation starts. The travel and
its turn share the idempotency key: a repeated request returns the same turn and moves nobody."""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .carousel.coords import MASTER_PIXELS, to_pixel
from .carousel.import_places import resolve_place
from .carousel.refdata import has_reference, place_image
from .config import Settings
from .db import one, rows
from .reference_api import TILES_DIR
from .rules.travel_ops import SAFE_PLACE_IDS
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
    if loc.startswith("on the road"):
        return f"[arrival] We stop {loc}.", "arrival"
    venue = result.get("venue")
    return (f"[arrival] We arrive at {loc}, at the {venue['zone_label']}." if venue else f"[arrival] We arrive at {loc}."), "arrival"


ART_DIR = "Location_Art_Production/assets"


def _px(north, west) -> tuple[float, float] | tuple[None, None]:
    return to_pixel(north, west) if north is not None and west is not None else (None, None)


def player_map(conn, cid: int, levels: list) -> dict:
    camp = one(conn, "SELECT node_id, current_location FROM campaigns WHERE id = ?", (cid,))
    out = {"position": None, "known": [], "omens": [], "last_route": None,
           "tiles": {"template": "/tiles/{z}/{x}/{y}.webp", "levels": levels, "size": MASTER_PIXELS}}
    if has_reference(conn):
        if camp["node_id"]:
            n = one(conn, "SELECT id, location_id, north, west FROM ref.graph_nodes WHERE id = ?", (camp["node_id"],))
            px, py = _px(n["north"], n["west"]) if n else (None, None)
            out["position"] = {"node_id": camp["node_id"], "place_id": n["location_id"] if n else None, "px": px, "py": py,
                               "label": camp["current_location"]}
        for p in rows(conn, """SELECT p.id, p.name, p.category, p.north, p.west FROM known_places k JOIN ref.places p ON p.id = k.place_id
                               WHERE k.campaign_id = ? ORDER BY p.name""", (cid,)):
            px, py = _px(p["north"], p["west"])
            img = place_image(conn, p["id"])
            out["known"].append({"id": p["id"], "name": p["name"], "category": p["category"], "px": px, "py": py,
                                 "safe": p["id"] in SAFE_PLACE_IDS, "image": f"/location-art/{img}" if img else None})
        for o in rows(conn, """SELECT s.omen_id, s.stage, s.seen_at_node, o.description, p.north, p.west FROM omen_states s
                               JOIN ref.omens o ON o.id = s.omen_id LEFT JOIN ref.places p ON p.id = o.place_id
                               WHERE s.campaign_id = ? ORDER BY s.omen_id""", (cid,)):
            north, west = o["north"], o["west"]
            if o["seen_at_node"]:   # a roaming Omen is where the group saw it
                n = one(conn, "SELECT north, west FROM ref.graph_nodes WHERE id = ?", (o["seen_at_node"],))
                north, west = (n["north"], n["west"]) if n else (north, west)
            px, py = _px(north, west)
            out["omens"].append({"id": o["omen_id"], "description": o["description"], "px": px, "py": py, "stage": o["stage"]})
    e = one(conn, "SELECT payload_json FROM events WHERE campaign_id = ? AND type = 'travel' AND rolled_back = 0 ORDER BY id DESC LIMIT 1", (cid,))
    if e:
        out["last_route"] = {"geometry_px": e["payload"].get("geometry_px") or [], "interrupted": e["payload"].get("interrupted")}
    return out


def register(app: FastAPI, settings: Settings, state) -> None:
    manifest = settings.source_dir / TILES_DIR / "tile_manifest.json"
    try:
        levels = json.loads(manifest.read_text(encoding="utf-8-sig")).get("levels") or []
    except (OSError, ValueError):
        levels = []

    @app.get("/api/campaigns/{cid}/map")
    def get_map(cid: int):
        c = state.conn()
        try:
            if not one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,)):
                raise HTTPException(404, "campaign not found")
            return player_map(c, cid, levels)
        finally:
            c.close()

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

    art = settings.source_dir / ART_DIR
    if art.is_dir():
        app.mount("/location-art", StaticFiles(directory=art), name="location-art")
