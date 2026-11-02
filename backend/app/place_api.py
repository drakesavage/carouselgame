"""A place dossier for the town map: only for places the group knows. Built from explicit column lists, never a whole record:
name, category, region, art, whether it's a safe place, when and how the group learned of it, its gazetteer description
(none for a place flagged spoiler), how often the group has stopped there, the Omens sighted there (description and stage,
never the dossier), the met residents last seen there, and the stories that ended there (title and ending only).

An unknown place and a made-up id get the same 404, so the answer never confirms that a place exists."""
from __future__ import annotations

import json

from fastapi import FastAPI, HTTPException

from .carousel.import_places import resolve_place
from .carousel.refdata import has_reference, place_image
from .carousel.residents import unmet_clause
from .db import one, rows
from .rules.travel_ops import SAFE_PLACE_IDS


def place_dossier(conn, cid: int, pid: str) -> dict | None:
    if not has_reference(conn):
        return None
    pid = resolve_place(conn, pid) or pid
    k = one(conn, "SELECT how, known_at_minutes FROM known_places WHERE campaign_id = ? AND place_id = ?", (cid, pid))
    p = one(conn, "SELECT id, name, category, region, spoiler, raw_json FROM ref.places WHERE id = ?", (pid,)) if k else None
    if not p:
        return None
    spoiler = json.loads(p["spoiler"] or "null")   # stored as JSON text: 'false' is not falsy until decoded
    img = place_image(conn, pid)
    # the travel event's `arrived` is the graph node the group stopped at; place nodes share their place's id
    visits = rows(conn, """SELECT game_time_minutes FROM events WHERE campaign_id = ? AND type = 'travel' AND rolled_back = 0
                           AND json_extract(payload_json, '$.arrived') = ? ORDER BY id""", (cid, pid))
    return {
        "id": pid, "name": p["name"], "category": p["category"], "region": p["region"],
        "image": f"/location-art/{img}" if img else None, "safe": pid in SAFE_PLACE_IDS,
        "known": {"how": k["how"], "minutes": k["known_at_minutes"]},
        "description": None if spoiler else ((p["raw"] or {}).get("description") or None),   # one() decodes raw_json as "raw"
        "visits": {"count": len(visits), "last_minutes": visits[-1]["game_time_minutes"] if visits else None},
        "omens": [{"id": o["omen_id"], "description": o["description"], "stage": o["stage"], "seen_minutes": o["seen_at_minutes"]}
                  for o in rows(conn, """SELECT s.omen_id, s.stage, s.seen_at_minutes, o.description FROM omen_states s
                                         JOIN ref.omens o ON o.id = s.omen_id WHERE s.campaign_id = ? AND o.place_id = ?
                                         ORDER BY s.seen_at_minutes, s.omen_id""", (cid, pid))],
        "stories": [{"title": s["title"], "ending": s["ending"], "ended_minutes": s["ended_minutes"]}
                    for s in rows(conn, """SELECT x.title, x.ending, x.ended_minutes FROM stories x JOIN ref.omens o ON o.id = x.omen_id
                                           WHERE x.campaign_id = ? AND x.status = 'concluded' AND o.place_id = ? ORDER BY x.id""", (cid, pid))],
        # mark_seen writes "{place}[, {zone}] at day {d} {slot}": the place name followed by ", " or " at day "
        "people": [r["name"] for r in rows(conn, f"""SELECT c.name FROM characters c JOIN npc_profiles n ON n.character_id = c.id
                                                    WHERE c.campaign_id = ? AND {unmet_clause('c')}
                                                    AND (n.last_seen LIKE ? || ', %' OR n.last_seen LIKE ? || ' at day %')
                                                    ORDER BY c.name""", (cid, p["name"], p["name"]))],
    }


def register(app: FastAPI, state) -> None:
    @app.get("/api/campaigns/{cid}/places/{place_id}")
    def get_place(cid: int, place_id: str):
        c = state.conn()
        try:
            if not one(c, "SELECT id FROM campaigns WHERE id = ?", (cid,)):
                raise HTTPException(404, "campaign not found")
            d = place_dossier(c, cid, place_id)
            if d is None:
                raise HTTPException(404, "unknown place")
            return d
        finally:
            c.close()
